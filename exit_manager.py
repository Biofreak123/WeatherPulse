# exit_manager.py
# -------------------------------------------------------------
# Adds TP/SL + continuous monitoring (no EOD flattening) for Alpaca options.
# - Places a REAL stop-loss at Alpaca right after entry (−50%).
# - Polls option quotes; when TP is reached it SELLS and cancels the stop.
# - Also polls the stop order; if it fills, the monitor stops.
# Requires: requests, pytz
# Env: ALPACA_API_KEY, ALPACA_API_SECRET, [ALPACA_BASE_URL], TRADIER_TOKEN
# -------------------------------------------------------------
import os, json, time, threading, datetime as dt, requests, pytz
from typing import Optional

# ---------- Broker Wrapper (Alpaca) ----------
class AlpacaBroker:
    def __init__(self, api_key: Optional[str]=None, api_secret: Optional[str]=None, base_url: Optional[str]=None):
        self.api_key   = api_key   or os.getenv("ALPACA_API_KEY", "")
        self.api_secret= api_secret or os.getenv("ALPACA_API_SECRET", "")
        self.base_url  = base_url  or os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        self.s = requests.Session()
        self.s.headers.update({
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json"
        })

    def place_market_buy(self, option_symbol: str, qty: int):
        p = {"symbol": option_symbol, "qty": qty, "side": "buy", "type": "market",
             "time_in_force": "day", "asset_class": "option"}
        r = self.s.post(f"{self.base_url}/v2/orders", data=json.dumps(p), timeout=30); r.raise_for_status()
        return r.json()

    def place_market_sell(self, option_symbol: str, qty: int):
        p = {"symbol": option_symbol, "qty": qty, "side": "sell", "type": "market",
             "time_in_force": "day", "asset_class": "option"}
        r = self.s.post(f"{self.base_url}/v2/orders", data=json.dumps(p), timeout=30); r.raise_for_status()
        return r.json()

    def place_limit_sell(self, option_symbol: str, qty: int, limit_price: float):
        p = {"symbol": option_symbol, "qty": qty, "side": "sell", "type": "limit",
             "limit_price": round(float(limit_price), 2), "time_in_force": "day", "asset_class": "option"}
        r = self.s.post(f"{self.base_url}/v2/orders", data=json.dumps(p), timeout=30); r.raise_for_status()
        return r.json()

    def place_stop_sell(self, option_symbol: str, qty: int, stop_price: float):
        p = {"symbol": option_symbol, "qty": qty, "side": "sell", "type": "stop",
             "stop_price": round(float(stop_price), 2), "time_in_force": "day", "asset_class": "option"}
        r = self.s.post(f"{self.base_url}/v2/orders", data=json.dumps(p), timeout=30); r.raise_for_status()
        return r.json()

    def cancel_order(self, order_id: str):
        # returns 204 on success, 404 if already gone
        return self.s.delete(f"{self.base_url}/v2/orders/{order_id}", timeout=30).status_code

    def get_order(self, order_id: str):
        r = self.s.get(f"{self.base_url}/v2/orders/{order_id}", timeout=30); r.raise_for_status()
        return r.json()

    def wait_for_fill_price(self, order_id: str, timeout_sec: int = 90) -> Optional[float]:
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            d = self.get_order(order_id)
            st = d.get("status")
            if st == "filled":
                try: return float(d.get("filled_avg_price"))
                except: return None
            if st in ("canceled","expired","rejected"): return None
            time.sleep(1.0)
        return None

# ---------- Quote Provider (Tradier) ----------
class TradierQuotes:
    """Fetch OCC option quotes from Tradier (sandbox or prod via token)."""
    def __init__(self, token: Optional[str]=None, sandbox: bool=True):
        self.token = token or os.getenv("TRADIER_TOKEN","")
        self.base  = "https://sandbox.tradier.com" if sandbox else "https://api.tradier.com"
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}", "Accept":"application/json"})

    def mid_or_last(self, option_symbol: str) -> Optional[float]:
        try:
            r = self.s.get(f"{self.base}/v1/markets/quotes", params={"symbols": option_symbol}, timeout=20)
            r.raise_for_status()
            q = r.json().get("quotes",{}).get("quote")
            if not q: return None
            if isinstance(q, list): q = q[0]
            bid = float(q.get("bid",0) or 0); ask = float(q.get("ask",0) or 0); last = float(q.get("last",0) or 0)
            if bid>0 and ask>0: return round((bid+ask)/2.0, 2)
            return round(last,2) if last>0 else None
        except Exception:
            return None

# ---------- Exit Manager (fake-OCO; no EOD flatten) ----------
class ExitManager:
    def __init__(self, broker: AlpacaBroker, quotes: TradierQuotes, poll_sec: float=2.0):
        self.broker = broker; self.quotes = quotes; self.poll_sec = poll_sec
        self.est = pytz.timezone("America/New_York")

    def start_monitor(self, option_symbol: str, qty: int, fill_price: float,
                      take_profit_mult: float, stop_mult: float, use_market_for_tp: bool=True):
        assert fill_price and fill_price>0, "fill_price required"
        stop_price = max(0.01, round(fill_price*float(stop_mult),2))
        tp_price   = round(fill_price*float(take_profit_mult),2)

        stop = self.broker.place_stop_sell(option_symbol, qty, stop_price)
        stop_id = stop.get("id")

        state = {
            "symbol": option_symbol, "qty": qty,
            "fill": float(fill_price), "tp_price": tp_price,
            "stop_price": stop_price, "stop_id": stop_id,
            "tp_filled": False, "done": False, "use_mkt_tp": use_market_for_tp
        }
        t = threading.Thread(target=self._loop, args=(state,)); t.daemon=True; t.start()
        return {"ok": True, "stop_id": stop_id, "tp_level": tp_price, "stop_level": stop_price}

    def _loop(self, st: dict):
        while not st["done"]:
            # a) if stop already filled, we're done
            if st.get("stop_id"):
                try:
                    od = self.broker.get_order(st["stop_id"])
                    if od.get("status") == "filled":
                        st["done"] = True
                        break
                except Exception:
                    pass

            # b) TP check
            px = self.quotes.mid_or_last(st["symbol"]) or 0.0
            if px >= st["tp_price"] and not st["tp_filled"]:
                try:
                    if st["use_mkt_tp"]:
                        self.broker.place_market_sell(st["symbol"], st["qty"])
                    else:
                        self.broker.place_limit_sell(st["symbol"], st["qty"], st["tp_price"])
                    st["tp_filled"] = True
                    if st.get("stop_id"): self.broker.cancel_order(st["stop_id"])
                    st["done"] = True
                    break
                except Exception:
                    # try again next poll
                    pass

            time.sleep(self.poll_sec)
