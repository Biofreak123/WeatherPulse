import os
import requests
import logging
from datetime import datetime, timedelta
from models import TradingConfig, Order, WebhookLog
from app import db

logger = logging.getLogger(__name__)

class TradingService:
    def __init__(self):
        self.base_url = "https://paper-api.alpaca.markets"
        self.data_url = "https://data.alpaca.markets"
        
    def get_headers(self):
        """Get Alpaca API headers from database config or environment variables"""
        config = TradingConfig.query.first()
        if config and config.alpaca_api_key and config.alpaca_secret_key:
            return {
                "APCA-API-KEY-ID": config.alpaca_api_key,
                "APCA-API-SECRET-KEY": config.alpaca_secret_key,
            }
        else:
            # Fallback to environment variables
            return {
                "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY"),
                "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY"),
            }
    
    def test_connection(self):
        """Test Alpaca API connection"""
        try:
            headers = self.get_headers()
            if not headers["APCA-API-KEY-ID"] or not headers["APCA-API-SECRET-KEY"]:
                return False, "API credentials not configured"
            
            response = requests.get(f"{self.base_url}/v2/account", headers=headers, timeout=10)
            if response.status_code == 200:
                return True, "Connection successful"
            else:
                return False, f"API Error: {response.status_code} - {response.text}"
        except Exception as e:
            logger.error(f"Connection test failed: {str(e)}")
            return False, f"Connection failed: {str(e)}"
    
    def get_2dte_date(self):
        """Get date that is 2 business days from today"""
        today = datetime.now()
        dte = 2
        date = today
        while dte > 0:
            date += timedelta(days=1)
            if date.weekday() < 5:  # Monday = 0, Friday = 4
                dte -= 1
        return date.strftime("%Y-%m-%d")
    
    def round_to_nearest_strike(self, price):
        """Round price to nearest dollar for strike selection"""
        return round(price)
    
    def get_spy_last_price(self):
        """Get SPY last trade price from Alpaca trades endpoint"""
        try:
            headers = self.get_headers()
            response = requests.get(
                f"{self.data_url}/v2/stocks/trades/latest?symbols=SPY", 
                headers=headers, 
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if 'trades' in data and 'SPY' in data['trades']:
                return float(data['trades']['SPY']['p'])
            else:
                raise ValueError("No trade price data available for SPY")
                
        except Exception as e:
            logger.error(f"Error getting SPY last price: {str(e)}")
            raise
    
    def get_current_price(self, ticker):
        """Get current stock price from Alpaca trades endpoint"""
        try:
            # Use specialized SPY function for SPY ticker
            if ticker.upper() == 'SPY':
                return self.get_spy_last_price()
            
            headers = self.get_headers()
            response = requests.get(
                f"{self.data_url}/v2/stocks/trades/latest?symbols={ticker}", 
                headers=headers, 
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if 'trades' in data and ticker in data['trades']:
                return float(data['trades'][ticker]['p'])
            else:
                raise ValueError(f"No trade price data available for {ticker}")
                
        except Exception as e:
            logger.error(f"Error getting price for {ticker}: {str(e)}")
            raise
    
    def get_atm_option_contract(self, ticker, direction):
        """Find ATM option contract for given ticker and direction"""
        try:
            current_price = self.get_current_price(ticker)
            strike = self.round_to_nearest_strike(current_price)
            expiry = self.get_2dte_date()
            option_type = "call" if direction == "CALL" else "put"
            
            headers = self.get_headers()
            params = {
                "underlying_symbol": ticker,
                "expiration_date": expiry,
                "option_type": option_type,
                "strike_price": strike,
            }
            
            response = requests.get(
                f"{self.base_url}/v1beta1/options/contracts", 
                headers=headers, 
                params=params,
                timeout=10
            )
            response.raise_for_status()
            contracts = response.json().get("option_contracts", [])
            
            if contracts:
                return contracts[0]["symbol"], strike, expiry
            else:
                return None, strike, expiry
                
        except Exception as e:
            logger.error(f"Error finding option contract: {str(e)}")
            raise
    
    def place_market_order(self, contract_symbol, quantity):
        """Place market order for options contract"""
        try:
            headers = self.get_headers()
            order_data = {
                "symbol": contract_symbol,
                "qty": str(quantity),
                "side": "buy",
                "type": "market",
                "time_in_force": "day"
            }
            
            response = requests.post(
                f"{self.base_url}/v2/orders", 
                headers=headers, 
                json=order_data,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Error placing order: {str(e)}")
            raise
    
    def process_webhook_signal(self, signal_data, ip_address, user_agent):
        """Process incoming webhook signal and place order"""
        order = None
        webhook_log = None
        
        try:
            # Log the webhook request
            webhook_log = WebhookLog(
                payload=str(signal_data),
                ip_address=ip_address,
                user_agent=user_agent
            )
            db.session.add(webhook_log)
            
            # Validate signal data
            signal = signal_data.get("signal")
            ticker = signal_data.get("ticker", "SPY").upper()
            quantity = int(signal_data.get("qty", 1))
            
            if signal not in ["CALL", "PUT"]:
                raise ValueError("Invalid signal. Must be 'CALL' or 'PUT'")
            
            if quantity <= 0:
                raise ValueError("Quantity must be positive")
            
            # Create order record
            order = Order(
                ticker=ticker,
                signal=signal,
                quantity=quantity,
                order_status='processing'
            )
            db.session.add(order)
            db.session.flush()  # Get the order ID
            
            # Find option contract
            contract_symbol, strike, expiry = self.get_atm_option_contract(ticker, signal)
            
            if not contract_symbol:
                raise ValueError(f"No {signal} option contract found for {ticker}")
            
            # Update order with contract details
            order.contract_symbol = contract_symbol
            order.strike_price = strike
            order.expiry_date = expiry
            
            # Place the order
            order_result = self.place_market_order(contract_symbol, quantity)
            
            # Update order with Alpaca order ID
            if 'id' in order_result:
                order.alpaca_order_id = order_result['id']
                order.order_status = 'submitted'
            else:
                order.order_status = 'failed'
                order.error_message = "No order ID returned from Alpaca"
            
            # Update webhook log
            webhook_log.response_status = 200
            webhook_log.response_message = f"Order placed successfully: {contract_symbol}"
            
            db.session.commit()
            
            return {
                "success": True,
                "message": f"{signal} order placed successfully",
                "order_id": order.id,
                "contract_symbol": contract_symbol,
                "strike_price": strike,
                "expiry_date": expiry,
                "alpaca_order_id": order.alpaca_order_id
            }
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error processing webhook: {error_msg}")
            
            # Update order status if it exists
            if order:
                order.order_status = 'failed'
                order.error_message = error_msg
            
            # Update webhook log
            if webhook_log:
                webhook_log.response_status = 400
                webhook_log.response_message = error_msg
            
            db.session.commit()
            
            return {
                "success": False,
                "error": error_msg,
                "order_id": order.id if order else None
            }
