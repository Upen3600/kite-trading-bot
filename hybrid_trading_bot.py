"""
╔══════════════════════════════════════════════════════════════════╗
║   HYBRID TRADING SYSTEM — BankNifty & Nifty  v2.0               ║
║   Strategy: ORB + EMA9/21 + VWAP + Supertrend                   ║
║   Timeframe: 5min entry | 15min trend confirm                    ║
║   Fixed: crash bugs, signal debug, supertrend init               ║
╚══════════════════════════════════════════════════════════════════╝

STRATEGY RULES (Manual check reference):
─────────────────────────────────────────
CALL (BUY CE) — ALL 5 must be true:
  1. Index LTP > ORB High (9:15–9:30 ka high)
  2. 5min EMA9 > EMA21 (short-term bullish)
  3. 15min EMA9 > EMA21 (trend confirm)
  4. LTP > VWAP (5min) (price above intraday avg)
  5. Supertrend direction = +1 on 15min (bullish)

PUT (BUY PE) — ALL 5 must be true:
  1. Index LTP < ORB Low (9:15–9:30 ka low)
  2. 5min EMA9 < EMA21
  3. 15min EMA9 < EMA21
  4. LTP < VWAP (5min)
  5. Supertrend direction = -1 on 15min (bearish)

ENTRY: ATM strike option at market
SL   : 30% of entry premium
TARGET: 60% of entry premium
MAX TRADES: 3 per symbol per day
TRADE WINDOW: 9:31 AM – 2:00 PM IST
SQUAREOFF: 3:10 PM IST (force)
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
#  CONFIG
# ─────────────────────────────────────────────
API_KEY          = os.environ.get("API_KEY",             "yj3cey9o0ho0gi1b")
BANKNIFTY_TOKEN  = int(os.environ.get("BANKNIFTY_TOKEN", "260105"))
NIFTY_TOKEN      = int(os.environ.get("NIFTY_TOKEN",     "256265"))
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",      "8620220458:AAG-oxvhWhPio7iX9pWCk-0AFovl5KrUXxc")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",    "-1003780954866")

IST = ZoneInfo("Asia/Kolkata")

LOT_SIZE           = {"BANKNIFTY": 15, "NIFTY": 50}
MAX_TRADES_PER_DAY = 3
MAX_LOSS_PER_DAY   = 5000
OPTION_BUDGET      = 8000
SL_PERCENT         = 30
TARGET_PERCENT     = 60
STRIKE_OFFSET      = {"BANKNIFTY": 100, "NIFTY": 50}
TRADE_LOG_FILE     = "/tmp/trades_log.json"
MARKET_FILE        = "/tmp/market_data.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
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
#  KITE INIT — silent (no telegram spam)
# ─────────────────────────────────────────────
def init_kite(access_token: str):
    kite.set_access_token(access_token)
    log.info("✅ Kite access token set.")
    # NOTE: No send_telegram here — avoids spam on every token refresh


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
#  TRADE LOG
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
    to  = datetime.now(IST)
    frm = to - timedelta(days=days)
    try:
        data = kite.historical_data(token, frm, to, interval)
        df   = pd.DataFrame(data)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df
    except Exception as e:
        log.error(f"OHLC error token={token} interval={interval}: {e}")
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
    return int(round(ltp / off) * off)

def get_weekly_expiry() -> str:
    today = now_ist()
    days  = (3 - today.weekday()) % 7
    if days == 0 and today.hour >= 15:
        days = 7
    exp = today + timedelta(days=days)
    return exp.strftime("%d%b%y").upper()

def get_option_ltp(symbol: str, strike: int, opt_type: str, expiry: str):
    opt_sym = f"{symbol}{expiry}{strike}{opt_type}"
    try:
        instruments = kite.instruments("NFO")
        idf   = pd.DataFrame(instruments)
        match = idf[idf["tradingsymbol"] == opt_sym]
        if match.empty:
            log.warning(f"Option not found: {opt_sym}")
            return None, None, opt_sym
        tok   = int(match.iloc[0]["instrument_token"])
        ltp_d = kite.ltp([f"NFO:{tok}"])
        ltp   = list(ltp_d.values())[0]["last_price"]
        return ltp, tok, opt_sym
    except Exception as e:
        log.error(f"Option LTP error ({opt_sym}): {e}")
        return None, None, opt_sym


# ─────────────────────────────────────────────
#  INDICATORS
# ─────────────────────────────────────────────
def calc_ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()

def calc_vwap(df: pd.DataFrame) -> pd.Series:
    df = df.copy()
    df["_d"]  = df.index.date
    df["tp"]  = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = df.groupby("_d")["tp"].transform(
        lambda x: x.expanding().mean()
    )
    return df["vwap"]

def calc_supertrend(df: pd.DataFrame, period=7, mult=3) -> pd.Series:
    """
    Returns Series: +1 = bullish, -1 = bearish, 0 = undefined
    FIX: Properly initialised so early candles don't stay 0
    """
    hl2 = (df["high"] + df["low"]) / 2
    tr  = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    atr   = tr.ewm(span=period, adjust=False).mean()
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr

    close = df["close"]
    st    = pd.Series(0, index=df.index, dtype=int)

    # Initialize first valid candle based on price vs bands
    first = period
    if first < len(df):
        st.iloc[first] = 1 if close.iloc[first] > hl2.iloc[first] else -1

    for i in range(first + 1, len(df)):
        prev = st.iloc[i - 1]
        if close.iloc[i] > upper.iloc[i - 1]:
            st.iloc[i] = 1
        elif close.iloc[i] < lower.iloc[i - 1]:
            st.iloc[i] = -1
        else:
            st.iloc[i] = prev if prev != 0 else (1 if close.iloc[i] > hl2.iloc[i] else -1)
    return st


# ─────────────────────────────────────────────
#  STRATEGY CLASS
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
        self._last_scan_log = ""   # avoid duplicate scan logs

    def set_orb(self):
        df = get_ohlc(self.token, "5minute", days=2)
        if df.empty:
            log.warning(f"{self.symbol}: OHLC empty for ORB")
            return
        today = now_ist().date()
        td    = df[df.index.date == today]
        orb   = td.between_time("09:15", "09:29")
        if orb.empty:
            log.warning(f"{self.symbol}: No candles in ORB window")
            return
        self.orb_high = float(orb["high"].max())
        self.orb_low  = float(orb["low"].min())
        self.orb_set  = True
        log.info(f"{self.symbol} ORB set → H:{self.orb_high} L:{self.orb_low}")
        send_telegram(
            f"📐 <b>ORB Set — {self.symbol}</b>\n"
            f"🔼 High: <b>{self.orb_high:,.2f}</b>\n"
            f"🔽 Low:  <b>{self.orb_low:,.2f}</b>\n"
            f"📏 Range: {self.orb_high - self.orb_low:.2f} pts\n"
            f"⏰ {ist_str()} IST\n"
            f"👀 Watching for breakout..."
        )

    def get_signal(self):
        """
        Returns 'CALL', 'PUT', or None.
        Logs detailed reason why signal did/didn't fire.
        """
        # ── Guard checks ──
        if not self.orb_set:
            return None
        if self.active:
            return None
        if self.trade_today >= MAX_TRADES_PER_DAY:
            return None
        if self.daily_pnl <= -MAX_LOSS_PER_DAY:
            log.warning(f"{self.symbol}: Daily loss limit hit")
            return None

        now_str = now_ist().strftime("%H:%M")
        if not ("09:31" <= now_str <= "14:00"):
            return None

        # ── Fetch 5min data ──
        df5 = get_ohlc(self.token, "5minute", days=3)
        if df5.empty or len(df5) < 15:
            log.warning(f"{self.symbol}: Not enough 5min candles ({len(df5)})")
            return None

        df5["ema9"]  = calc_ema(df5["close"], 9)
        df5["ema21"] = calc_ema(df5["close"], 21)
        df5["vwap"]  = calc_vwap(df5)

        # ── Fetch 15min data ──
        df15 = get_ohlc(self.token, "15minute", days=5)
        if df15.empty or len(df15) < 10:
            log.warning(f"{self.symbol}: Not enough 15min candles ({len(df15)})")
            return None

        df15["ema9"]  = calc_ema(df15["close"], 9)
        df15["ema21"] = calc_ema(df15["close"], 21)
        df15["st"]    = calc_supertrend(df15)

        # ── Latest values ──
        ltp    = float(df5["close"].iloc[-1])
        e9_5   = float(df5["ema9"].iloc[-1])
        e21_5  = float(df5["ema21"].iloc[-1])
        vwap   = float(df5["vwap"].iloc[-1])
        e9_15  = float(df15["ema9"].iloc[-1])
        e21_15 = float(df15["ema21"].iloc[-1])
        st     = int(df15["st"].iloc[-1])

        # ── Debug scan log (every check) ──
        scan_info = (
            f"{self.symbol} | {now_str} | LTP={ltp:.2f} | "
            f"ORB H={self.orb_high:.0f} L={self.orb_low:.0f} | "
            f"EMA9/21(5m)={e9_5:.0f}/{e21_5:.0f} | "
            f"EMA9/21(15m)={e9_15:.0f}/{e21_15:.0f} | "
            f"VWAP={vwap:.0f} | ST={st}"
        )
        if scan_info != self._last_scan_log:
            log.info(f"SCAN: {scan_info}")
            self._last_scan_log = scan_info

        # ── CALL conditions ──
        call_checks = {
            "LTP>ORB_H": ltp > self.orb_high,
            "EMA9>21(5m)": e9_5 > e21_5,
            "EMA9>21(15m)": e9_15 > e21_15,
            "LTP>VWAP": ltp > vwap,
            "ST=Bull": st == 1,
        }
        # ── PUT conditions ──
        put_checks = {
            "LTP<ORB_L": ltp < self.orb_low,
            "EMA9<21(5m)": e9_5 < e21_5,
            "EMA9<21(15m)": e9_15 < e21_15,
            "LTP<VWAP": ltp < vwap,
            "ST=Bear": st == -1,
        }

        if all(call_checks.values()):
            log.info(f"✅ {self.symbol} CALL SIGNAL TRIGGERED")
            return "CALL"

        if all(put_checks.values()):
            log.info(f"✅ {self.symbol} PUT SIGNAL TRIGGERED")
            return "PUT"

        # ── Log which filters failed (only when near signal) ──
        call_pass = sum(call_checks.values())
        put_pass  = sum(put_checks.values())

        if call_pass >= 3:
            failed = [k for k, v in call_checks.items() if not v]
            log.info(f"⚡ {self.symbol} CALL near ({call_pass}/5) | Missing: {failed}")
        if put_pass >= 3:
            failed = [k for k, v in put_checks.items() if not v]
            log.info(f"⚡ {self.symbol} PUT near ({put_pass}/5) | Missing: {failed}")

        return None

    def execute_trade(self, direction: str):
        idx_ltp  = get_ltp(self.token)
        if idx_ltp == 0:
            log.error(f"{self.symbol}: Cannot get index LTP, skipping trade")
            return

        strike   = get_atm_strike(idx_ltp, self.symbol)
        expiry   = get_weekly_expiry()
        opt_type = "CE" if direction == "CALL" else "PE"

        opt_ltp, opt_token, opt_sym = get_option_ltp(
            self.symbol, strike, opt_type, expiry
        )
        if opt_ltp is None or opt_ltp == 0:
            log.warning(f"{self.symbol}: Option LTP=0 for {opt_sym} — skipping")
            send_telegram(
                f"⚠️ <b>Trade Skipped — {self.symbol}</b>\n"
                f"Option {opt_sym} not found or LTP=0\n"
                f"Expiry used: {expiry}"
            )
            return

        lots   = max(1, int(OPTION_BUDGET / (opt_ltp * LOT_SIZE[self.symbol])))
        sl     = round(opt_ltp * (1 - SL_PERCENT / 100), 2)
        target = round(opt_ltp * (1 + TARGET_PERCENT / 100), 2)
        cost   = round(opt_ltp * lots * LOT_SIZE[self.symbol], 2)

        self.active = {
            "symbol":     self.symbol,
            "opt_sym":    opt_sym,
            "opt_token":  opt_token,
            "direction":  direction,
            "strike":     strike,
            "expiry":     expiry,
            "opt_type":   opt_type,
            "entry":      opt_ltp,
            "idx_entry":  idx_ltp,
            "sl":         sl,
            "target":     target,
            "lots":       lots,
            "cost":       cost,
            "entry_time": ist_full(),
        }
        self.trade_today += 1

        # Notify dashboard
        try:
            from dashboard import set_active_trade
            set_active_trade(self.symbol, self.active)
        except Exception:
            pass

        send_telegram(
            f"🚨 <b>TRADE ALERT — {self.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Strike:</b> {strike} {opt_type}  |  Exp: {expiry}\n"
            f"📊 <b>Option:</b> {opt_sym}\n"
            f"{'📈' if direction == 'CALL' else '📉'} <b>Direction:</b> "
            f"{'BUY CE ↑' if direction == 'CALL' else 'BUY PE ↓'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Entry Premium:</b> ₹{opt_ltp:.2f}\n"
            f"📦 <b>Lots:</b> {lots}  |  Capital: ₹{cost:,.0f}\n"
            f"🛑 <b>SL:</b> ₹{sl:.2f}  (loss ≈ ₹{(opt_ltp-sl)*lots*LOT_SIZE[self.symbol]:,.0f})\n"
            f"🎯 <b>Target:</b> ₹{target:.2f}  (profit ≈ ₹{(target-opt_ltp)*lots*LOT_SIZE[self.symbol]:,.0f})\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 <b>Index LTP:</b> {idx_ltp:,.2f}\n"
            f"📐 ORB H:{self.orb_high:,.0f} L:{self.orb_low:,.0f}\n"
            f"⏰ <b>Time:</b> {ist_str()} IST\n"
            f"🔢 Trade #{self.trade_today} today"
        )
        log.info(f"✅ {self.symbol} trade executed: {opt_sym} @ ₹{opt_ltp} | SL:{sl} TGT:{target}")

    def monitor_trade(self, send_update=False):
        if not self.active:
            return
        t = self.active
        try:
            ltp_d   = kite.ltp([f"NFO:{t['opt_token']}"])
            cur_ltp = float(list(ltp_d.values())[0]["last_price"])
        except Exception as e:
            log.error(f"{self.symbol} monitor LTP error: {e}")
            return

        pnl = (cur_ltp - t["entry"]) * t["lots"] * LOT_SIZE[self.symbol]
        pct = (cur_ltp - t["entry"]) / t["entry"] * 100 if t["entry"] else 0

        log.info(f"{self.symbol} monitor: LTP={cur_ltp:.2f} Entry={t['entry']:.2f} P&L=₹{pnl:+.0f}")

        if cur_ltp <= t["sl"]:
            self._close("🛑 SL HIT", cur_ltp, pnl)
        elif cur_ltp >= t["target"]:
            self._close("🎯 TARGET HIT", cur_ltp, pnl)
        elif send_update:
            send_telegram(
                f"📊 <b>TRADE UPDATE — {t['symbol']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 {t['opt_sym']}\n"
                f"💰 Entry: ₹{t['entry']:.2f}  →  LTP: ₹{cur_ltp:.2f}\n"
                f"{'🟢' if pnl >= 0 else '🔴'} <b>P&amp;L: ₹{pnl:+,.0f}  ({pct:+.1f}%)</b>\n"
                f"🛑 SL: ₹{t['sl']:.2f}  🎯 TGT: ₹{t['target']:.2f}\n"
                f"📦 Lots: {t['lots']}\n"
                f"⏰ {ist_str()} IST"
            )

    def _close(self, status: str, exit_ltp: float, pnl: float):
        t   = self.active
        pct = (exit_ltp - t["entry"]) / t["entry"] * 100 if t["entry"] else 0
        emoji = "✅" if pnl >= 0 else "❌"
        send_telegram(
            f"{emoji} <b>{status} — {t['symbol']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {t['opt_sym']}\n"
            f"💰 Entry: ₹{t['entry']:.2f}  →  Exit: ₹{exit_ltp:.2f}\n"
            f"<b>P&amp;L: ₹{pnl:+,.0f}  ({pct:+.1f}%)</b>\n"
            f"📦 Lots: {t['lots']}\n"
            f"⏰ {ist_str()} IST"
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
        # Clear from dashboard
        try:
            from dashboard import set_active_trade
            set_active_trade(self.symbol, None)
        except Exception:
            pass
        log.info(f"{t['symbol']} closed | {status} | P&L: ₹{pnl:+,.0f}")

    def force_squareoff(self):
        if not self.active:
            return
        t = self.active
        try:
            ltp_d   = kite.ltp([f"NFO:{t['opt_token']}"])
            cur_ltp = float(list(ltp_d.values())[0]["last_price"])
        except Exception:
            cur_ltp = t["entry"]
        pnl = (cur_ltp - t["entry"]) * t["lots"] * LOT_SIZE[self.symbol]
        self._close("⏰ EOD SQUAREOFF", cur_ltp, pnl)

    def reset_day(self):
        self.orb_set        = False
        self.orb_high       = None
        self.orb_low        = None
        self.trade_today    = 0
        self.daily_pnl      = 0.0
        self.active         = None
        self.trades_log     = []
        self._last_scan_log = ""
        log.info(f"{self.symbol}: Day reset ✅")


# ─────────────────────────────────────────────
#  BOT ORCHESTRATOR
# ─────────────────────────────────────────────
class HybridBot:
    def __init__(self):
        self.bn           = HybridStrategy("BANKNIFTY", BANKNIFTY_TOKEN)
        self.nf           = HybridStrategy("NIFTY",     NIFTY_TOKEN)
        self._last_update = now_ist()

    def market_open_alert(self):
        bn_ltp = get_ltp(BANKNIFTY_TOKEN)
        nf_ltp = get_ltp(NIFTY_TOKEN)
        send_telegram(
            f"🔔 <b>MARKET OPEN — {now_ist().strftime('%d %b %Y')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 BankNifty: <b>{bn_ltp:,.2f}</b>\n"
            f"📊 Nifty:     <b>{nf_ltp:,.2f}</b>\n"
            f"⏰ {ist_str()} IST\n"
            f"⏳ ORB capturing 9:15–9:30...\n"
            f"🎯 Strategy: ORB+EMA9/21+VWAP+Supertrend"
        )

    def market_close_alert(self):
        trades      = load_trade_log()
        today       = now_ist().strftime("%Y-%m-%d")
        today_trades= [t for t in trades if t.get("date") == today]
        total_pnl   = sum(t["pnl"] for t in today_trades)
        wins        = sum(1 for t in today_trades if t["pnl"] > 0)
        losses      = len(today_trades) - wins
        emoji       = "🟢" if total_pnl >= 0 else "🔴"
        send_telegram(
            f"🔕 <b>MARKET CLOSE — {now_ist().strftime('%d %b %Y')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Trades Today: {len(today_trades)}\n"
            f"✅ Wins: {wins}  |  ❌ Losses: {losses}\n"
            f"{emoji} <b>Day P&amp;L: ₹{total_pnl:+,.0f}</b>\n"
            f"⏰ {ist_str()} IST"
        )

    def run_orb_setup(self):
        log.info("📐 Setting ORB for both symbols...")
        self.bn.set_orb()
        self.nf.set_orb()

    def run_signal_check(self):
        for s in [self.bn, self.nf]:
            try:
                sig = s.get_signal()
                if sig:
                    s.execute_trade(sig)
            except Exception as e:
                log.error(f"Signal check error ({s.symbol}): {e}")

    def run_monitor(self):
        now      = now_ist()
        elapsed  = (now - self._last_update).total_seconds()
        send_upd = elapsed >= 900   # 15 min update
        for s in [self.bn, self.nf]:
            try:
                s.monitor_trade(send_update=send_upd)
            except Exception as e:
                log.error(f"Monitor error ({s.symbol}): {e}")
        if send_upd:
            self._last_update = now

    def run_squareoff(self):
        self.bn.force_squareoff()
        self.nf.force_squareoff()
        self.daily_summary()

    def daily_summary(self):
        trades       = load_trade_log()
        today        = now_ist().strftime("%Y-%m-%d")
        today_trades = [t for t in trades if t.get("date") == today]
        if not today_trades:
            send_telegram(
                f"📋 <b>Daily Summary — {now_ist().strftime('%d %b %Y')}</b>\n"
                f"No trades today."
            )
            return
        total_pnl = sum(t["pnl"] for t in today_trades)
        msg = (f"📋 <b>DAILY SUMMARY — {now_ist().strftime('%d %b %Y')}</b>\n"
               f"━━━━━━━━━━━━━━━━━━━━━\n")
        for i, t in enumerate(today_trades, 1):
            e    = "✅" if t["pnl"] > 0 else "❌"
            stxt = (t.get("status") or "").split()[0]
            msg += f"{e} {i}. {t['opt_sym']} → ₹{t['pnl']:+,.0f} [{stxt}]\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━\n💵 <b>Net: ₹{total_pnl:+,.0f}</b>"
        send_telegram(msg)

    def update_market_data(self):
        """Fetch quote + EMA50/200 for dashboard."""
        data = {}
        for sym_key, token in [("bn", BANKNIFTY_TOKEN), ("nf", NIFTY_TOKEN)]:
            try:
                q    = kite.quote([f"NSE:{token}"])
                qv   = list(q.values())[0]
                ltp  = float(qv["last_price"])
                ohlc = qv.get("ohlc", {})

                df = get_ohlc(token, "5minute", days=10)
                ema50  = float(calc_ema(df["close"], 50).iloc[-1])  if not df.empty else 0
                ema200 = float(calc_ema(df["close"], 200).iloc[-1]) if not df.empty else 0

                data[sym_key] = {
                    "ltp":        round(ltp, 2),
                    "day_high":   round(float(ohlc.get("high",  ltp)), 2),
                    "day_low":    round(float(ohlc.get("low",   ltp)), 2),
                    "prev_close": round(float(ohlc.get("close", ltp)), 2),
                    "ema50":      round(ema50,  2),
                    "ema200":     round(ema200, 2),
                    "change":     round(ltp - float(ohlc.get("close", ltp)), 2),
                    "change_pct": round((ltp - float(ohlc.get("close", ltp))) /
                                       float(ohlc.get("close", ltp)) * 100, 2)
                                  if ohlc.get("close") else 0,
                    "updated":    ist_str(),
                }
            except Exception as e:
                log.error(f"update_market_data error ({sym_key}): {e}")
                data[sym_key] = {}
        try:
            with open(MARKET_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            log.error(f"Market file write error: {e}")

    def reset_day(self):
        self.bn.reset_day()
        self.nf.reset_day()
        self._last_update = now_ist()
        send_telegram(
            f"🌅 <b>New Day — {now_ist().strftime('%d %b %Y')}</b>\n"
            f"🤖 Bot ready | Token refreshed\n"
            f"⏰ Market opens at 9:15 AM IST"
        )
