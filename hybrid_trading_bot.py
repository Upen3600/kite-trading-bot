"""
╔══════════════════════════════════════════════════════════════════╗
║   HYBRID TRADING SYSTEM — BankNifty & Nifty                     ║
║   Strategy: ORB + EMA (9/21) + VWAP + Supertrend               ║
║   Timezone: IST (Asia/Kolkata)                                   ║
║   Options: ATM Strike CE/PE buying                               ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import json
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect

# ─────────────────────────────────────────────
#  CONFIG — from env vars (Railway) or defaults
# ─────────────────────────────────────────────
API_KEY          = os.environ.get("API_KEY",        "yj3cey9o0ho0gi1b")
BANKNIFTY_TOKEN  = int(os.environ.get("BANKNIFTY_TOKEN", "260105"))
NIFTY_TOKEN      = int(os.environ.get("NIFTY_TOKEN",     "256265"))
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",  "8620220458:AAG-oxvhWhPio7iX9pWCk-0AFovl5KrUXxc")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID","-1003780954866")

IST = ZoneInfo("Asia/Kolkata")

LOT_SIZE         = {"BANKNIFTY": 15, "NIFTY": 50}
MAX_TRADES_PER_DAY = 3          # per symbol
MAX_LOSS_PER_DAY   = 5000       # ₹ combined daily loss limit
OPTION_BUDGET      = 8000       # ₹ per trade
SL_PERCENT         = 30
TARGET_PERCENT     = 60
STRIKE_OFFSET      = {"BANKNIFTY": 100, "NIFTY": 50}

# Trade log file (persisted for dashboard)
TRADE_LOG_FILE = "/tmp/trades_log.json"

# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s IST | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)

kite = KiteConnect(api_key=API_KEY)


def now_ist():
    return datetime.now(IST)

def ist_str(fmt="%H:%M:%S"):
    return now_ist().strftime(fmt)

def ist_full():
    return now_ist().strftime("%d %b %Y %H:%M IST")


# ─────────────────────────────────────────────
#  KITE INIT
# ─────────────────────────────────────────────
def init_kite(access_token: str):
    kite.set_access_token(access_token)
    log.info("✅ Kite connected.")
    send_telegram(
        f"🤖 <b>Hybrid Bot Connected</b>\n"
        f"📅 {ist_full()}\n"
        f"📊 Watching: BankNifty &amp; Nifty\n"
        f"⏳ Market opens at 9:15 AM IST"
    )


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(msg: str, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": parse_mode
        }, timeout=10)
        if r.status_code != 200:
            log.error(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


# ─────────────────────────────────────────────
#  TRADE LOG (for Dashboard)
# ─────────────────────────────────────────────
def load_trade_log():
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_trade_log(trades: list):
    with open(TRADE_LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2)

def append_trade(trade: dict):
    trades = load_trade_log()
    trades.append(trade)
    save_trade_log(trades)


# ─────────────────────────────────────────────
#  MARKET DATA
# ─────────────────────────────────────────────
def get_ohlc(token: int, interval: str, days: int = 5) -> pd.DataFrame:
    to   = datetime.now(IST)
    frm  = to - timedelta(days=days)
    try:
        data = kite.historical_data(token, frm, to, interval)
        df   = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df
    except Exception as e:
        log.error(f"OHLC error ({token}): {e}")
        return pd.DataFrame()

def get_ltp(token: int) -> float:
    try:
        q = kite.ltp([f"NSE:{token}"])
        return list(q.values())[0]["last_price"]
    except Exception as e:
        log.error(f"LTP error: {e}")
        return 0.0

def get_atm_strike(ltp: float, symbol: str) -> int:
    off = STRIKE_OFFSET[symbol]
    return round(ltp / off) * off

def get_weekly_expiry() -> str:
    today = now_ist()
    days  = (3 - today.weekday()) % 7
    if days == 0 and today.hour >= 15:
        days = 7
    exp = today + timedelta(days=days)
    return exp.strftime("%d%b%y").upper()

def get_option_ltp(symbol: str, strike: int, opt_type: str, expiry: str):
    """Fetch option LTP and instrument token from NFO."""
    opt_sym = f"{symbol}{expiry}{strike}{opt_type}"
    try:
        instruments = kite.instruments("NFO")
        idf = pd.DataFrame(instruments)
        match = idf[idf["tradingsymbol"] == opt_sym]
        if match.empty:
            log.warning(f"Option not found: {opt_sym}")
            return None, None, opt_sym
        token  = match.iloc[0]["instrument_token"]
        ltp_d  = kite.ltp([f"NFO:{token}"])
        ltp    = list(ltp_d.values())[0]["last_price"]
        return ltp, token, opt_sym
    except Exception as e:
        log.error(f"Option LTP error: {e}")
        return None, None, opt_sym


# ─────────────────────────────────────────────
#  INDICATORS
# ─────────────────────────────────────────────
def calc_ema(s, p): return s.ewm(span=p, adjust=False).mean()

def calc_vwap(df):
    df = df.copy()
    df["_d"] = df.index.date
    df["tp"]  = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = df.groupby("_d")["tp"].transform(
        lambda x: x.expanding().mean()
    )
    return df["vwap"]

def calc_supertrend(df, period=7, mult=3):
    hl2 = (df["high"] + df["low"]) / 2
    tr  = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    atr   = tr.ewm(span=period, adjust=False).mean()
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    st    = pd.Series(0, index=df.index, dtype=int)
    for i in range(1, len(df)):
        if df["close"].iloc[i] > upper.iloc[i-1]:
            st.iloc[i] = 1
        elif df["close"].iloc[i] < lower.iloc[i-1]:
            st.iloc[i] = -1
        else:
            st.iloc[i] = st.iloc[i-1]
    return st


# ─────────────────────────────────────────────
#  STRATEGY
# ─────────────────────────────────────────────
class HybridStrategy:
    def __init__(self, symbol: str, token: int):
        self.symbol      = symbol
        self.token       = token
        self.orb_high    = None
        self.orb_low     = None
        self.orb_set     = False
        self.trade_today = 0
        self.daily_pnl   = 0.0
        self.active      = None
        self.trades_log  = []

    def set_orb(self):
        df = get_ohlc(self.token, "5minute", days=1)
        if df.empty: return
        today = now_ist().date()
        td    = df[df.index.date == today]
        orb   = td.between_time("09:15", "09:29")
        if orb.empty: return
        self.orb_high = orb["high"].max()
        self.orb_low  = orb["low"].min()
        self.orb_set  = True
        log.info(f"{self.symbol} ORB → H:{self.orb_high} L:{self.orb_low}")
        send_telegram(
            f"📐 <b>ORB Set — {self.symbol}</b>\n"
            f"🔼 High: <b>{self.orb_high:,.0f}</b>\n"
            f"🔽 Low:  <b>{self.orb_low:,.0f}</b>\n"
            f"📏 Range: {self.orb_high - self.orb_low:.0f} pts\n"
            f"⏰ {ist_str()}"
        )

    def get_signal(self):
        if not self.orb_set: return None
        if self.trade_today >= MAX_TRADES_PER_DAY: return None
        if self.daily_pnl <= -MAX_LOSS_PER_DAY: return None
        if self.active: return None

        now = now_ist().strftime("%H:%M")
        if not ("09:31" <= now <= "14:00"): return None

        df5 = get_ohlc(self.token, "5minute", days=2)
        if df5.empty or len(df5) < 25: return None
        df5["ema9"]  = calc_ema(df5["close"], 9)
        df5["ema21"] = calc_ema(df5["close"], 21)
        df5["vwap"]  = calc_vwap(df5)

        df15 = get_ohlc(self.token, "15minute", days=3)
        if df15.empty or len(df15) < 20: return None
        df15["ema9"]  = calc_ema(df15["close"], 9)
        df15["ema21"] = calc_ema(df15["close"], 21)
        df15["st"]    = calc_supertrend(df15)

        ltp      = df5["close"].iloc[-1]
        e9_5     = df5["ema9"].iloc[-1]
        e21_5    = df5["ema21"].iloc[-1]
        vwap     = df5["vwap"].iloc[-1]
        e9_15    = df15["ema9"].iloc[-1]
        e21_15   = df15["ema21"].iloc[-1]
        st       = df15["st"].iloc[-1]

        if (ltp > self.orb_high and e9_5 > e21_5 and
                e9_15 > e21_15 and ltp > vwap and st == 1):
            return "CALL"
        if (ltp < self.orb_low and e9_5 < e21_5 and
                e9_15 < e21_15 and ltp < vwap and st == -1):
            return "PUT"
        return None

    def execute_trade(self, direction: str):
        idx_ltp = get_ltp(self.token)
        strike  = get_atm_strike(idx_ltp, self.symbol)
        expiry  = get_weekly_expiry()
        opt_type = "CE" if direction == "CALL" else "PE"

        opt_ltp, opt_token, opt_sym = get_option_ltp(
            self.symbol, strike, opt_type, expiry
        )
        if opt_ltp is None or opt_ltp == 0:
            log.warning(f"{self.symbol}: Option LTP not found — skipping")
            return

        lots   = max(1, int(OPTION_BUDGET / (opt_ltp * LOT_SIZE[self.symbol])))
        sl     = round(opt_ltp * (1 - SL_PERCENT / 100), 1)
        target = round(opt_ltp * (1 + TARGET_PERCENT / 100), 1)
        cost   = opt_ltp * lots * LOT_SIZE[self.symbol]

        self.active = {
            "symbol":    self.symbol,
            "opt_sym":   opt_sym,
            "opt_token": opt_token,
            "direction": direction,
            "strike":    strike,
            "expiry":    expiry,
            "opt_type":  opt_type,
            "entry":     opt_ltp,
            "idx_entry": idx_ltp,
            "sl":        sl,
            "target":    target,
            "lots":      lots,
            "cost":      cost,
            "entry_time": ist_full(),
        }
        self.trade_today += 1

        send_telegram(
            f"🚨 <b>TRADE ALERT — {self.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Strike:</b> {strike} {opt_type}  |  Exp: {expiry}\n"
            f"📊 <b>Option:</b> {opt_sym}\n"
            f"{'📈' if direction=='CALL' else '📉'} <b>Direction:</b> {'BUY CE (CALL)' if direction=='CALL' else 'BUY PE (PUT)'}\n"
            f"💰 <b>Entry Premium:</b> ₹{opt_ltp:.1f}\n"
            f"📦 <b>Lots:</b> {lots}  |  Cost: ₹{cost:,.0f}\n"
            f"🛑 <b>Stop Loss:</b> ₹{sl:.1f}  ({SL_PERCENT}%↓)\n"
            f"🎯 <b>Target:</b> ₹{target:.1f}  ({TARGET_PERCENT}%↑)\n"
            f"🏦 <b>Index LTP:</b> {idx_ltp:,.0f}\n"
            f"⏰ <b>Time:</b> {ist_str()}"
        )
        log.info(f"{self.symbol}: Trade opened → {opt_sym} @ ₹{opt_ltp}")

    def monitor_trade(self, send_update=False):
        if not self.active: return
        t = self.active
        try:
            ltp_d   = kite.ltp([f"NFO:{t['opt_token']}"])
            cur_ltp = list(ltp_d.values())[0]["last_price"]
        except Exception as e:
            log.error(f"Monitor LTP error: {e}")
            return

        pnl = (cur_ltp - t["entry"]) * t["lots"] * LOT_SIZE[self.symbol]
        pct = (cur_ltp - t["entry"]) / t["entry"] * 100

        if cur_ltp <= t["sl"]:
            self._close("🛑 SL HIT", cur_ltp, pnl)
        elif cur_ltp >= t["target"]:
            self._close("🎯 TARGET HIT", cur_ltp, pnl)
        elif send_update:
            send_telegram(
                f"📊 <b>TRADE UPDATE — {t['symbol']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 {t['opt_sym']}\n"
                f"💰 Entry: ₹{t['entry']:.1f}  →  LTP: ₹{cur_ltp:.1f}\n"
                f"{'🟢' if pnl>=0 else '🔴'} <b>P&amp;L: ₹{pnl:+,.0f}  ({pct:+.1f}%)</b>\n"
                f"📦 Lots: {t['lots']}\n"
                f"⏰ {ist_str()}"
            )

    def _close(self, status: str, exit_ltp: float, pnl: float):
        t = self.active
        pct = (exit_ltp - t["entry"]) / t["entry"] * 100
        emoji = "✅" if pnl >= 0 else "❌"
        send_telegram(
            f"{emoji} <b>{status} — {t['symbol']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {t['opt_sym']}\n"
            f"💰 Entry: ₹{t['entry']:.1f}  →  Exit: ₹{exit_ltp:.1f}\n"
            f"<b>P&amp;L: ₹{pnl:+,.0f}  ({pct:+.1f}%)</b>\n"
            f"📦 Lots: {t['lots']}\n"
            f"⏰ {ist_str()}"
        )
        self.daily_pnl += pnl
        rec = {
            "date":       now_ist().strftime("%Y-%m-%d"),
            "symbol":     t["symbol"],
            "opt_sym":    t["opt_sym"],
            "strike":     t["strike"],
            "opt_type":   t["opt_type"],
            "direction":  t["direction"],
            "lots":       t["lots"],
            "entry":      t["entry"],
            "exit":       exit_ltp,
            "pnl":        round(pnl, 2),
            "pct":        round(pct, 2),
            "status":     status,
            "entry_time": t["entry_time"],
            "exit_time":  ist_full(),
        }
        self.trades_log.append(rec)
        append_trade(rec)
        self.active = None
        log.info(f"{t['symbol']} closed: {status} | P&L: ₹{pnl:+,.0f}")

    def force_squareoff(self):
        if not self.active: return
        t = self.active
        try:
            ltp_d   = kite.ltp([f"NFO:{t['opt_token']}"])
            cur_ltp = list(ltp_d.values())[0]["last_price"]
        except Exception:
            cur_ltp = t["entry"]
        pnl = (cur_ltp - t["entry"]) * t["lots"] * LOT_SIZE[self.symbol]
        self._close("⏰ EOD SQUAREOFF", cur_ltp, pnl)

    def reset_day(self):
        self.orb_set     = False
        self.orb_high    = None
        self.orb_low     = None
        self.trade_today = 0
        self.daily_pnl   = 0.0
        self.active      = None
        self.trades_log  = []
        log.info(f"{self.symbol}: Day reset ✅")


# ─────────────────────────────────────────────
#  BOT ORCHESTRATOR
# ─────────────────────────────────────────────
class HybridBot:
    def __init__(self):
        self.bn  = HybridStrategy("BANKNIFTY", BANKNIFTY_TOKEN)
        self.nf  = HybridStrategy("NIFTY",     NIFTY_TOKEN)
        self._last_update = now_ist()

    def market_open_alert(self):
        bn_ltp = get_ltp(BANKNIFTY_TOKEN)
        nf_ltp = get_ltp(NIFTY_TOKEN)
        send_telegram(
            f"🔔 <b>MARKET OPEN — {now_ist().strftime('%d %b %Y')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 BankNifty: <b>{bn_ltp:,.0f}</b>\n"
            f"📊 Nifty:     <b>{nf_ltp:,.0f}</b>\n"
            f"⏰ {ist_str()} IST\n"
            f"⏳ ORB capturing 9:15–9:30..."
        )

    def market_close_alert(self):
        trades = load_trade_log()
        today  = now_ist().strftime("%Y-%m-%d")
        today_trades = [t for t in trades if t.get("date") == today]
        total_pnl = sum(t["pnl"] for t in today_trades)
        wins  = sum(1 for t in today_trades if t["pnl"] > 0)
        losses= len(today_trades) - wins
        emoji = "🟢" if total_pnl >= 0 else "🔴"
        send_telegram(
            f"🔕 <b>MARKET CLOSE — {now_ist().strftime('%d %b %Y')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Trades Today: {len(today_trades)}\n"
            f"✅ Wins: {wins}  |  ❌ Losses: {losses}\n"
            f"{emoji} <b>Day P&amp;L: ₹{total_pnl:+,.0f}</b>\n"
            f"⏰ {ist_str()} IST"
        )

    def run_orb_setup(self):
        log.info("📐 Setting ORB...")
        self.bn.set_orb()
        self.nf.set_orb()

    def run_signal_check(self):
        for s in [self.bn, self.nf]:
            sig = s.get_signal()
            if sig:
                s.execute_trade(sig)

    def run_monitor(self):
        now = now_ist()
        send_upd = (now - self._last_update).seconds >= 900
        self.bn.monitor_trade(send_update=send_upd)
        self.nf.monitor_trade(send_update=send_upd)
        if send_upd:
            self._last_update = now

    def run_squareoff(self):
        self.bn.force_squareoff()
        self.nf.force_squareoff()
        self.market_close_alert()
        self.daily_summary()

    def daily_summary(self):
        trades = load_trade_log()
        today  = now_ist().strftime("%Y-%m-%d")
        today_trades = [t for t in trades if t.get("date") == today]
        if not today_trades:
            send_telegram("📋 <b>Daily Summary</b>\nAaj koi trade nahi hua.")
            return
        total_pnl = sum(t["pnl"] for t in today_trades)
        msg = (f"📋 <b>DAILY SUMMARY — {now_ist().strftime('%d %b %Y')}</b>\n"
               f"━━━━━━━━━━━━━━━━━━━━━\n")
        for i, t in enumerate(today_trades, 1):
            e = "✅" if t["pnl"] > 0 else "❌"
            msg += f"{e} {i}. {t['opt_sym']} → ₹{t['pnl']:+,.0f} ({t['status'].split()[0]})\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━\n💵 <b>Net: ₹{total_pnl:+,.0f}</b>"
        send_telegram(msg)

    def update_market_data(self):
        """Fetch LTP, Day H/L, EMA50/200 for dashboard — runs every 10s via thread."""
        MARKET_FILE = "/tmp/market_data.json"
        data = {}
        for sym, token in [("bn", BANKNIFTY_TOKEN), ("nf", NIFTY_TOKEN)]:
            try:
                # LTP + OHLC quote
                quote = kite.quote([f"NSE:{token}"])
                q     = list(quote.values())[0]
                ltp   = q["last_price"]
                ohlc  = q.get("ohlc", {})

                # 5min candles for EMA50 & EMA200
                df = get_ohlc(token, "5minute", days=10)
                ema50  = float(calc_ema(df["close"], 50).iloc[-1])  if not df.empty else 0
                ema200 = float(calc_ema(df["close"], 200).iloc[-1]) if not df.empty else 0

                data[sym] = {
                    "ltp":        round(ltp, 2),
                    "day_high":   round(ohlc.get("high",  ltp), 2),
                    "day_low":    round(ohlc.get("low",   ltp), 2),
                    "prev_close": round(ohlc.get("close", ltp), 2),
                    "ema50":      round(ema50,  2),
                    "ema200":     round(ema200, 2),
                    "updated":    now_ist().strftime("%H:%M:%S"),
                }
            except Exception as e:
                log.error(f"Market data error ({sym}): {e}")
                data[sym] = {}
        try:
            import json as _json
            with open(MARKET_FILE, "w") as f:
                _json.dump(data, f)
        except Exception as e:
            log.error(f"Market file write error: {e}")

    def reset_day(self):
        self.bn.reset_day()
        self.nf.reset_day()
        send_telegram(
            f"🌅 <b>New Day — {now_ist().strftime('%d %b %Y')}</b>\n"
            f"🤖 Bot ready | Login refreshed\n"
            f"⏰ Market opens at 9:15 AM IST"
        )
