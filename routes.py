from flask import render_template, request, jsonify, flash, redirect, url_for
from app import app, db
from models import TradingConfig, Order, WebhookLog
from trading_service import TradingService
import logging

logger = logging.getLogger(__name__)
trading_service = TradingService()

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

@app.route("/webhook", methods=["POST"])
def webhook():
    """Webhook endpoint for receiving trading signals"""
    try:
        # Get client info
        ip_address = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        user_agent = request.headers.get('User-Agent', '')
        
        # Process the signal
        result = trading_service.process_webhook_signal(
            request.json, 
            ip_address, 
            user_agent
        )
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500

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
