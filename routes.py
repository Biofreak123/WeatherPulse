from flask import render_template, request, jsonify, flash, redirect, url_for
from app import app, db
from models import TradingConfig, Order, WebhookLog
from trading_service import TradingService
import logging
import json  # keep

# --- NEW: Exit manager imports/instances ---
from exit_manager import AlpacaBroker, TradierQuotes, ExitManager

broker = AlpacaBroker()
# set sandbox=False if you’re using production Tradier quotes
quotes = TradierQuotes(sandbox=True)
exits = ExitManager(broker, quotes)
# --- end NEW ---

logger = logging.getLogger(__name__)
trading_service = TradingService()

# --- NEW: minimal-alert defaults + normalizer ---
DEFAULT_TICKER = "SPY"
DEFAULT_QTY = 1

def _normalize_signal_payload(request):
    """
    Accepts:
      - /webhook?side=call|put
      - text/plain body: 'CALL' or 'PUT'
      - JSON: {"side":"CALL"} or {"signal":"PUT", "ticker":"SPY", "qty":1}
      - form-encoded fields
    Returns a dict like: {"signal":"CALL","ticker":"SPY","qty":1} or None if invalid.
    """
    # 1) Query param (?side=call|put)
    side_q = (request.args.get("side") or "").strip().lower()
    if side_q in ("call", "put"):
        return {"signal": side_q.upper(), "ticker": DEFAULT_TICKER, "qty": DEFAULT_QTY}

    # 2) Raw body text: CALL / PUT (text/plain)
    raw = (request.get_data(cache=False, as_text=True) or "").strip()
    if raw.upper() in ("CALL", "PUT"):
        return {"signal": raw.upper(), "ticker": DEFAULT_TICKER, "qty": DEFAULT_QTY}

    # 3) Proper JSON body
    data = None
    if request.is_json:
        data = request.get_json(silent=True) or {}

    # 3a) Raw string that contains JSON
    if data is None and raw.startswith("{") and raw.endswith("}"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None

    # 3b) Form-encoded JSON or fields
    if data is None and request.form:
        for key in ("payload", "json", "message", "alert_message"):
            v = request.form.get(key, "").strip()
            if v:
                try:
                    data = json.loads(v)
                    break
                except json.JSONDecodeError:
                    pass
        if data is None and ("signal" in request.form or "side" in request.form):
            try:
                qty_val = int(request.form.get("qty", DEFAULT_QTY) or DEFAULT_QTY)
            except Exception:
                qty_val = DEFAULT_QTY
            data = {
                "signal": request.form.get("signal") or request.form.get("side"),
                "ticker": request.form.get("ticker") or DEFAULT_TICKER,
                "qty": qty_val,
            }

    if not isinstance(data, dict):
        return None

    # Normalize keys
    signal = (str(data.get("signal") or data.get("side") or "")).strip().upper()
    if signal not in ("CALL", "PUT"):
        return None

    ticker = (data.get("ticker") or DEFAULT_TICKER).strip().upper()
    try:
        qty = int(data.get("qty", DEFAULT_QTY))
    except Exception:
        qty = DEFAULT_QTY

    return {"signal": signal, "ticker": ticker, "qty": qty}
# --- end NEW ---

@app.route("/")
def dashboard():
    """Main dashboard page"""
    # Get recent orders
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(10).all()
    
    # Get order statistics
    total_orders = Order.query.count()
    successful_orders = Order.query.filter_by(order_status='submitted').count()
    failed_orders = Order.query.filter_by(order_status='failed').count()
    
    # Check API connection
    is_connected, connection_msg = trading_service.test_connection()
    
    return render_template('dashboard.html',
                         recent_orders=recent_orders,
                         total_orders=total_orders,
                         successful_orders=successful_orders,
                         failed_orders=failed_orders,
                         is_connected=is_connected,
                         connection_msg=connection_msg)

@app.route("/orders")
def orders():
    """Orders history page"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    orders = Order.query.order_by(Order.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('orders.html', orders=orders)

@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Settings page for API configuration"""
    config = TradingConfig.query.first()
    
    if request.method == "POST":
        api_key = request.form.get('alpaca_api_key', '').strip()
        secret_key = request.form.get('alpaca_secret_key', '').strip()
        
        if not config:
            config = TradingConfig()
            db.session.add(config)
        
        config.alpaca_api_key = api_key
        config.alpaca_secret_key = secret_key
        
        try:
            db.session.commit()
            flash('Settings saved successfully!', 'success')
            
            # Test the connection
            is_connected, msg = trading_service.test_connection()
            if is_connected:
                flash('API connection test successful!', 'success')
            else:
                flash(f'API connection test failed: {msg}', 'warning')
                
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving settings: {str(e)}', 'error')
        
        return redirect(url_for('settings'))
    
    return render_template('settings.html', config=config)

# ---- WEBHOOK with exit logic attached ----
@app.route("/webhook", methods=["POST"])
def webhook():
    """Webhook endpoint for receiving trading signals; attaches exits after entry."""
    try:
        ip_address = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        user_agent = request.headers.get('User-Agent', '')
        content_type = request.headers.get('Content-Type', '')

        normalized = _normalize_signal_payload(request)
        if not normalized:
            logger.error(
                "Webhook parse failed. Content-Type=%s, body=%r, form=%r, args=%r",
                content_type, (request.get_data(as_text=True) or "")[:500],
                dict(request.form), dict(request.args)
            )
            return jsonify({
                "success": False,
                "error": "Unsupported payload. Send CALL/PUT in ?side= or body or JSON.",
                "examples": {
                    "query_param": "/webhook?side=call",
                    "text_body": "CALL",
                    "json": {"side":"PUT"},
                }
            }), 415

        # Hand off to your existing service (unchanged contract)
        result = trading_service.process_webhook_signal(
            normalized,
            ip_address,
            user_agent
        )

        # Attach exit logic if we can extract order info
        exits_payload = {"exits_attached": False}
        try:
            side = normalized["signal"]          # "CALL" or "PUT"
            qty = int(normalized.get("qty", 1))

            # Try to pull symbol/fill/order_id from the service response
            option_symbol = result.get("option_symbol") or result.get("symbol")
            fill_price = result.get("fill_price") or result.get("filled_avg_price")
            order_id = result.get("order_id") or result.get("alpaca_order_id") or result.get("entry_id")

            # If we have an order_id but not symbol/fill, fetch from Alpaca
            if order_id and (not option_symbol or not fill_price):
                try:
                    od = broker.get_order(order_id)
                    option_symbol = option_symbol or od.get("symbol")
                    if not fill_price and od.get("filled_avg_price"):
                        fill_price = float(od["filled_avg_price"])
                    if not qty:
                        try:
                            qty = int(od.get("qty") or qty)
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"Could not fetch order {order_id} from Alpaca: {e}")

            # If we have enough to attach, start the monitor
            if option_symbol and fill_price:
                if side.upper() == "CALL":
                    tp_mult, stop_mult = 1.90, 0.50   # +90%, -50%
                else:
                    tp_mult, stop_mult = 1.50, 0.50   # +50%, -50%

                monitor_res = exits.start_monitor(
                    option_symbol=option_symbol,
                    qty=qty,
                    fill_price=float(fill_price),
                    take_profit_mult=tp_mult,
                    stop_mult=stop_mult,
                    use_market_for_tp=True
                )
                exits_payload.update({
                    "exits_attached": True,
                    "tp_level": monitor_res.get("tp_level"),
                    "stop_level": monitor_res.get("stop_level"),
                    "stop_id": monitor_res.get("stop_id")
                })
            else:
                exits_payload["exit_error"] = "Missing symbol/fill; could not attach exits."

        except Exception as e:
            logger.exception("Exit attach error")
            exits_payload["exit_error"] = f"{type(e).__name__}: {e}"

        status = 200 if result.get("success") else 400
        # Merge exit info into the original result so your UI sees it
        merged = {**result, **exits_payload}
        return jsonify(merged), status

    except Exception:
        logger.exception("Webhook error")
        return jsonify({"success": False, "error": "Internal server error"}), 500
# ---- end webhook ----

@app.route("/api/orders")
def api_orders():
    """API endpoint for getting orders (for AJAX updates)"""
    try:
        orders = Order.query.order_by(Order.created_at.desc()).limit(50).all()
        return jsonify([order.to_dict() for order in orders])
    except Exception as e:
        logger.error(f"API orders error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/stats")
def api_stats():
    """API endpoint for getting dashboard statistics"""
    try:
        total_orders = Order.query.count()
        successful_orders = Order.query.filter_by(order_status='submitted').count()
        failed_orders = Order.query.filter_by(order_status='failed').count()
        pending_orders = Order.query.filter_by(order_status='processing').count()
        
        is_connected, connection_msg = trading_service.test_connection()
        
        return jsonify({
            "total_orders": total_orders,
            "successful_orders": successful_orders,
            "failed_orders": failed_orders,
            "pending_orders": pending_orders,
            "is_connected": is_connected,
            "connection_msg": connection_msg
        })
    except Exception as e:
        logger.error(f"API stats error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/test-webhook", methods=["GET"])
def test_webhook():
    """Test endpoint to generate a sample webhook call"""
    return '''
    <h2>Test Webhook</h2>
    <p>Send a POST request to <code>/webhook</code> with JSON payload:</p>
    <pre>
{
    "signal": "CALL",
    "ticker": "SPY",
    "qty": 1
}
    </pre>
    <p>Or:</p>
    <pre>
{
    "signal": "PUT",
    "ticker": "QQQ",
    "qty": 2
}
    </pre>
    '''
