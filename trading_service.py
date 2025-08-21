import os
import requests
import logging
from datetime import datetime, timedelta, date
from models import TradingConfig, Order, WebhookLog
from app import db

logger = logging.getLogger(__name__)

class TradingService:
    def __init__(self):
        # Keep your original bases/endpoints
        self.base_url = "https://paper-api.alpaca.markets"   # account, clock, orders
        self.live_url = "https://api.alpaca.markets"         # legacy v1beta1 contracts (as you had)
        self.data_url = "https://data.alpaca.markets"        # prices

    # ---------- Headers / Auth ----------

    def get_headers(self):
        """Get Alpaca API headers from DB config or environment variables."""
        config = TradingConfig.query.first()
        if config and config.alpaca_api_key and config.alpaca_secret_key:
            key = config.alpaca_api_key
            secret = config.alpaca_secret_key
        else:
            key = os.getenv("ALPACA_API_KEY")
            secret = os.getenv("ALPACA_SECRET_KEY")
        return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    # ---------- Health ----------

    def test_connection(self):
        """Test Alpaca API connection."""
        try:
            headers = self.get_headers()
            if not headers["APCA-API-KEY-ID"] or not headers["APCA-API-SECRET-KEY"]:
                return False, "API credentials not configured"
            r = requests.get(f"{self.base_url}/v2/account", headers=headers, timeout=10)
            if r.status_code == 200:
                return True, "Connection successful"
            return False, f"API Error: {r.status_code} - {r.text}"
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False, f"Connection failed: {e}"

    # ---------- Helpers: dates / prices ----------

    def get_2dte_date(self) -> str:
        """Get date that is 2 business days from today (Mon–Fri)."""
        d = date.today()
        remaining = 2
        while remaining > 0:
            d += timedelta(days=1)
            if d.weekday() < 5:
                remaining -= 1
        return d.strftime("%Y-%m-%d")

    def round_to_nearest_strike(self, price: float) -> int:
        """Round to nearest whole-dollar strike (SPY usually $1 increments)."""
        return int(round(float(price)))

    def _latest_trade(self, symbol: str) -> float:
        headers = self.get_headers()
        r = requests.get(
            f"{self.data_url}/v2/stocks/trades/latest",
            params={"symbols": symbol},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return float(data["trades"][symbol]["p"])

    def get_spy_last_price(self) -> float:
        try:
            return self._latest_trade("SPY")
        except Exception as e:
            logger.error(f"Error getting SPY last price: {e}")
            raise

    def get_current_price(self, ticker: str) -> float:
        try:
            t = ticker.upper()
            return self._latest_trade(t)
        except Exception as e:
            logger.error(f"Error getting price for {ticker}: {e}")
            raise

    # ---------- OCC symbol helper (fallback) ----------

    def construct_option_symbol(self, ticker: str, expiry_date: str, option_type: str, strike_price: float):
        """
        OCC: {TICKER}{YYMMDD}{C|P}{STRIKE*1000:08d}
        e.g., SPY 2025-08-19 put 645.00 -> SPY250819P00645000
        """
        try:
            expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
            date_str = expiry_dt.strftime("%y%m%d")
            type_letter = "C" if option_type.lower() == "call" else "P"
            strike_str = f"{int(round(float(strike_price) * 1000)):08d}"
            return f"{ticker.upper()}{date_str}{type_letter}{strike_str}"
        except Exception as e:
            logger.error(f"Error constructing option symbol: {e}")
            return None

    # ---------- Market hours guard (queues when closed) ----------

    def market_open_now(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/v2/clock", headers=self.get_headers(), timeout=10)
            r.raise_for_status()
            return bool(r.json().get("is_open"))
        except Exception as e:
            logger.warning(f"Clock check failed: {e}")
            # Be permissive on transient errors
            return True

    # ---------- Contracts lookup (keeps your v1beta1 endpoint) ----------

    def get_atm_option_contract(self, ticker: str, direction: str):
        """
        Try exact strike via v1beta1, then fetch a batch and pick nearest, then fallback to constructed OCC.
        Keeps your original endpoint: GET {live_url}/v1beta1/options/contracts
        """
        try:
            ticker = ticker.upper()
            spot = self.get_current_price(ticker)
            expiry = self.get_2dte_date()
            target_strike = self.round_to_nearest_strike(spot)
            option_type = "call" if direction.upper() == "CALL" else "put"
            headers = self.get_headers()

            # 1) Exact strike attempt
            try:
                r = requests.get(
                    f"{self.live_url}/v1beta1/options/contracts",
                    headers=headers,
                    params={
                        "underlying_symbol": ticker,
                        "expiration_date": expiry,
                        "option_type": option_type,
                        "strike_price": target_strike,
                        "limit": 1,
                    },
                    timeout=10,
                )
                r.raise_for_status()
                payload = r.json()
                contracts = payload.get("option_contracts") or payload.get("contracts") or []
                if contracts:
                    c = contracts[0]
                    return c["symbol"], float(c.get("strike_price", target_strike)), expiry
            except requests.RequestException as e:
                logger.warning(f"Exact-strike contract lookup failed: {e}")

            # 2) Nearest strike: fetch a page (omit strike filter) and pick closest
            try:
                r = requests.get(
                    f"{self.live_url}/v1beta1/options/contracts",
                    headers=headers,
                    params={
                        "underlying_symbol": ticker,
                        "expiration_date": expiry,
                        "option_type": option_type,
                        "limit": 1000,
                    },
                    timeout=10,
                )
                r.raise_for_status()
                payload = r.json()
                contracts = payload.get("option_contracts") or payload.get("contracts") or []
                if contracts:
                    best = min(contracts, key=lambda c: abs(float(c["strike_price"]) - spot))
                    return best["symbol"], float(best["strike_price"]), expiry
            except requests.RequestException as e:
                logger.warning(f"Nearest-strike page lookup failed: {e}")

            # 3) Fallback: construct OCC symbol manually
            constructed = self.construct_option_symbol(ticker, expiry, option_type, target_strike)
            if constructed:
                return constructed, float(target_strike), expiry

            return None, float(target_strike), expiry

        except Exception as e:
            logger.error(f"Error finding option contract: {e}")
            raise

    # ---------- Place order (keeps /v2/orders) ----------

    def place_market_order(self, contract_symbol: str, quantity: int):
        """Place MARKET order for an option contract via /v2/orders (OCC symbol in 'symbol')."""
        try:
            headers = {**self.get_headers(), "Content-Type": "application/json"}
            body = {
                "symbol": contract_symbol,    # e.g., SPY250819P00645000
                "qty": str(quantity),
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "asset_class": "option",      # optional, helpful
            }
            r = requests.post(
                f"{self.base_url}/v2/orders",
                headers=headers,
                json=body,
                timeout=10,
            )
            if not r.ok:
                logger.error("Order failed %s: %s", r.status_code, r.text)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            resp = e.response
            logger.error("Order HTTPError %s: %s", resp.status_code, resp.text)
            raise
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            raise

    # ---------- Webhook processing ----------

    def process_webhook_signal(self, signal_data, ip_address, user_agent):
        """Process incoming webhook signal and place/queue an order."""
        order = None
        webhook_log = None
        try:
            # Log inbound webhook
            webhook_log = WebhookLog(
                payload=str(signal_data),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.add(webhook_log)

            # Validate/normalize
            signal = (signal_data.get("signal") or "").upper()
            if signal not in ("CALL", "PUT"):
                raise ValueError("Invalid signal. Must be 'CALL' or 'PUT'")

            ticker = (signal_data.get("ticker") or "SPY").upper()
            try:
                quantity = int(signal_data.get("qty", 1))
            except Exception:
                quantity = 1
            if quantity <= 0:
                raise ValueError("Quantity must be positive")

            # Create order record
            order = Order(
                ticker=ticker,
                signal=signal,
                quantity=quantity,
                order_status="processing",
            )
            db.session.add(order)
            db.session.flush()  # get order.id

            # Find a real contract / strike / expiry
            contract_symbol, strike, expiry = self.get_atm_option_contract(ticker, signal)
            if not contract_symbol:
                raise ValueError(f"No {signal} option contract found for {ticker}")

            # Fill details
            order.contract_symbol = contract_symbol
            order.strike_price = strike
            order.expiry_date = expiry

            # Market-hours guard → queue instead of fail after-hours
            if not self.market_open_now():
                order.order_status = "queued"
                order.error_message = "Market closed; queued for next open"
                if webhook_log:
                    webhook_log.response_status = 200
                    webhook_log.response_message = "Queued: market closed"
                db.session.commit()
                return {
                    "success": False,
                    "error": "Market closed",
                    "action": "queued",
                    "order_id": order.id,
                    "contract_symbol": contract_symbol,
                    "strike_price": strike,
                    "expiry_date": expiry,
                }

            # Place the order (kept endpoint)
            result = self.place_market_order(contract_symbol, quantity)

            if "id" in result:
                order.alpaca_order_id = result["id"]
                order.order_status = "submitted"
                if webhook_log:
                    webhook_log.response_status = 200
                    webhook_log.response_message = f"Order placed: {contract_symbol}"
            else:
                order.order_status = "failed"
                order.error_message = "No order ID returned from Alpaca"

            db.session.commit()
            return {
                "success": order.order_status == "submitted",
                "message": f"{signal} order {'placed' if order.order_status=='submitted' else 'failed'}",
                "order_id": order.id,
                "contract_symbol": contract_symbol,
                "strike_price": strike,
                "expiry_date": expiry,
                "alpaca_order_id": getattr(order, "alpaca_order_id", None),
            }

        except Exception as e:
            err = str(e)
            logger.error(f"Error processing webhook: {err}")
            if order and order.order_status not in ("queued", "submitted"):
                order.order_status = "failed"
                order.error_message = err
            if webhook_log:
                webhook_log.response_status = 400
                webhook_log.response_message = err
            db.session.commit()
            return {"success": False, "error": err, "order_id": order.id if order else None}
