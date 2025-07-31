from app import db
from datetime import datetime
from sqlalchemy import Text

class TradingConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alpaca_api_key = db.Column(db.String(255))
    alpaca_secret_key = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(10), nullable=False)
    signal = db.Column(db.String(10), nullable=False)  # CALL or PUT
    contract_symbol = db.Column(db.String(50))
    quantity = db.Column(db.Integer, nullable=False)
    strike_price = db.Column(db.Float)
    expiry_date = db.Column(db.String(20))
    order_status = db.Column(db.String(20), default='pending')
    alpaca_order_id = db.Column(db.String(50))
    error_message = db.Column(Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    filled_at = db.Column(db.DateTime)
    
    def to_dict(self):
        return {
            'id': self.id,
            'ticker': self.ticker,
            'signal': self.signal,
            'contract_symbol': self.contract_symbol,
            'quantity': self.quantity,
            'strike_price': self.strike_price,
            'expiry_date': self.expiry_date,
            'order_status': self.order_status,
            'alpaca_order_id': self.alpaca_order_id,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'filled_at': self.filled_at.isoformat() if self.filled_at else None
        }

class WebhookLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    payload = db.Column(Text)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    response_status = db.Column(db.Integer)
    response_message = db.Column(Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
