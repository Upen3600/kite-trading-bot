"""
╔══════════════════════════════════════════════════════════════════╗
║   HYBRID TRADING SYSTEM — BankNifty & Nifty                     ║
║   Strategy: ORB + EMA (9/21) + VWAP                             ║
║   Timeframe: 5min entry | 15min trend confirmation              ║
║   Instrument: Options (CE/PE buying)                             ║
║   Broker: Zerodha Kite                                           ║
╚══════════════════════════════════════════════════════════════════╝
"""

import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
import schedule

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
API_KEY            = "yj3cey9o0ho0gi1b"
ACCESS_TOKEN       = ""          # <-- Har subah login ke baad paste karo
BANKNIFTY_TOKEN    = 260105
NIFTY_TOKEN        = 256265
TELEGRAM_TOKEN     = "8620220458:AAG-oxvhWhPio7iX9pWCk-0AFovl5KrUXxc"
TELEGRAM_CHAT_ID   = "-1003780954866"

# ─── Risk / Trade Settings ───────────────────
LOT_SIZE           = {"BANKNIFTY": 15, "NIFTY": 50}
MAX_TRADES_PER_DAY = 6          # BN + Nifty combined (3 each max)
MAX_LOSS_PER_DAY   = 5000       # ₹ — daily loss limit (bot stops after this)
OPTION_BUDGET      = 8000       # ₹ per trade (approx premium budget)
SL_PERCENT         = 30         # % of premium as Stop Loss
TARGET_PERCENT     = 60         # % of premium as Target

# ─── Timing ──────────────────────────────────
ORB_START          = "09:15"
ORB_END            = "09:30"
TRADE_START        = "09:31"
TRADE_END          = "14:00"    # No new trades after 2 PM
SQUARE_OFF_TIME    = "15:10"    # Force close all positions

# ─── Strike Selection ────────────────────────
STRIKE_OFFSET      = {"BANKNIFTY": 100, "NIFTY": 50}   # ATM ± offset

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("hybrid_bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(msg: str, parse_mode="HTML"):
    """Send message to Telegram channel."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": parse_mode
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log.error(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


def alert_signal(symbol, direction, entry, sl, target, strategy, expiry, strike, opt_type):
    msg = (
        f"🚨 <b>TRADE ALERT</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Symbol:</b> {symbol}\n"
        f"📅 <b>Expiry:</b> {expiry}\n"
        f"🎯 <b>Strike:</b> {strike} {opt_type}\n"
        f"📈 <b>Direction:</b> {'🟢 BUY CE' if direction == 'CALL' else '🔴 BUY PE'}\n"
        f"💰 <b>Entry (Premium):</b> ₹{entry:.1f}\n"
        f"🛑 <b>Stop Loss:</b> ₹{sl:.1f}  ({SL_PERCENT}%)\n"
        f"✅ <b>Target:</b> ₹{target:.1f}  ({TARGET_PERCENT}%)\n"
        f"📊 <b>Strategy:</b> {strategy}\n"
        f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(msg)


def alert_followup(symbol, opt_symbol, status, entry, current_price, pnl, lots):
    emoji = "✅" if pnl > 0 else "🔴"
    msg = (
        f"{emoji} <b>FOLLOWUP — {status}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 {symbol} → {opt_symbol}\n"
        f"💰 Entry: ₹{entry:.1f} | LTP: ₹{current_price:.1f}\n"
        f"📦 Lots: {lots}\n"
        f"💵 <b>P&L: ₹{pnl:+.0f}</b>\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(msg)


def alert_daily_summary(summary: dict):
    total_pnl = sum(t["pnl"] for t in summary["trades"])
    wins = sum(1 for t in summary["trades"] if t["pnl"] > 0)
    losses = len(summary["trades"]) - wins
    msg = (
        f"📋 <b>DAILY SUMMARY — {datetime.now().strftime('%d %b %Y')}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 Total Trades: {len(summary['trades'])}\n"
        f"✅ Winners: {wins}  |  ❌ Losers: {losses}\n"
        f"💵 <b>Net P&L: ₹{total_pnl:+.0f}</b>\n\n"
    )
    for i, t in enumerate(summary["trades"], 1):
        emoji = "✅" if t["pnl"] > 0 else "❌"
        msg += f"{emoji} {i}. {t['symbol']} {t['type']} → ₹{t['pnl']:+.0f}\n"
    send_telegram(msg)


# ─────────────────────────────────────────────
#  KITE CONNECT
# ─────────────────────────────────────────────
kite = KiteConnect(api_key=API_KEY)


def init_kite(access_token: str):
    kite.set_access_token(access_token)
    log.info("✅ Kite connected.")
    send_telegram("🤖 <b>Hybrid Bot Started</b>\n✅ Kite connected\n📊 Watching: BankNifty & Nifty")


# ─────────────────────────────────────────────
#  MARKET DATA HELPERS
# ─────────────────────────────────────────────
def get_ohlc(instrument_token: int, interval: str, lookback_days: int = 5) -> pd.DataFrame:
    """Fetch historical candles from Kite."""
    to_date   = datetime.now()
    from_date = to_date - timedelta(days=lookback_days)
    try:
        data = kite.historical_data(
            instrument_token, from_date, to_date, interval
        )
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df
    except Exception as e:
        log.error(f"OHLC fetch error: {e}")
        return pd.DataFrame()


def get_ltp(instrument_token: int) -> float:
    try:
        q = kite.ltp([f"NSE:{instrument_token}"])
        return list(q.values())[0]["last_price"]
    except Exception as e:
        log.error(f"LTP error: {e}")
        return 0.0


def get_atm_strike(ltp: float, symbol: str) -> int:
    offset = STRIKE_OFFSET[symbol]
    return round(ltp / offset) * offset


def get_weekly_expiry() -> str:
    """Return nearest Thursday expiry string (e.g. 03APR25)."""
    today = datetime.now()
    days_ahead = (3 - today.weekday()) % 7  # 3 = Thursday
    if days_ahead == 0 and today.hour >= 15:
        days_ahead = 7
    expiry = today + timedelta(days=days_ahead)
    return expiry.strftime("%d%b%y").upper()


# ─────────────────────────────────────────────
#  INDICATORS
# ─────────────────────────────────────────────
def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP — resets each day."""
    df = df.copy()
    df["date_only"] = df.index.date
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tpv"] = df["tp"] * df["volume"]
    df["cum_tpv"] = df.groupby("date_only")["tpv"].cumsum()
    df["cum_vol"] = df.groupby("date_only")["volume"].cumsum()
    return df["cum_tpv"] / df["cum_vol"]


def calc_supertrend(df: pd.DataFrame, period=7, multiplier=3) -> pd.Series:
    hl2 = (df["high"] + df["low"]) / 2
    atr = df["high"].combine(df["close"].shift(), max) - df["low"].combine(df["close"].shift(), min)
    atr = atr.rolling(period).mean()
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    supertrend = pd.Series(index=df.index, dtype=float)
    direction  = pd.Series(index=df.index, dtype=int)
    for i in range(1, len(df)):
        if df["close"].iloc[i] > upper.iloc[i-1]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i] < lower.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
        supertrend.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]
    return direction


# ─────────────────────────────────────────────
#  STRATEGY: HYBRID ORB + EMA + VWAP
# ─────────────────────────────────────────────
class HybridStrategy:
    """
    Signal Logic:
    ─────────────
    CALL (BUY CE):
      • Price breaks above ORB High
      • 5min: EMA9 > EMA21
      • 15min: EMA9 > EMA21 (trend confirm)
      • Price > VWAP (5min)
      • Supertrend bullish (15min)

    PUT (BUY PE):
      • Price breaks below ORB Low
      • 5min: EMA9 < EMA21
      • 15min: EMA9 < EMA21
      • Price < VWAP (5min)
      • Supertrend bearish (15min)
    """

    def __init__(self, symbol: str, token: int):
        self.symbol = symbol
        self.token  = token
        self.orb_high  = None
        self.orb_low   = None
        self.orb_set   = False
        self.trade_today = 0
        self.daily_pnl   = 0.0
        self.active_trade = None   # dict when in trade
        self.trades_log   = []

    def set_orb(self):
        """Calculate Opening Range High/Low from 9:15–9:30."""
        df = get_ohlc(self.token, "5minute", lookback_days=1)
        if df.empty:
            log.warning(f"{self.symbol}: Empty OHLC for ORB")
            return
        today = datetime.now().date()
        df_today = df[df.index.date == today]
        orb_candles = df_today.between_time("09:15", "09:29")
        if orb_candles.empty:
            return
        self.orb_high = orb_candles["high"].max()
        self.orb_low  = orb_candles["low"].min()
        self.orb_set  = True
        log.info(f"{self.symbol} ORB → H:{self.orb_high} L:{self.orb_low}")
        send_telegram(
            f"📐 <b>ORB Set — {self.symbol}</b>\n"
            f"🔼 High: {self.orb_high}\n"
            f"🔽 Low: {self.orb_low}"
        )

    def get_signal(self) -> str | None:
        """Returns 'CALL', 'PUT', or None."""
        if not self.orb_set:
            return None
        if self.trade_today >= 3:
            return None
        if self.daily_pnl <= -MAX_LOSS_PER_DAY:
            return None

        now = datetime.now().strftime("%H:%M")
        if not (TRADE_START <= now <= TRADE_END):
            return None

        # ── 5min data ──
        df5 = get_ohlc(self.token, "5minute", lookback_days=2)
        if df5.empty or len(df5) < 25:
            return None

        df5["ema9"]  = calc_ema(df5["close"], 9)
        df5["ema21"] = calc_ema(df5["close"], 21)
        df5["vwap"]  = calc_vwap(df5)

        # ── 15min data ──
        df15 = get_ohlc(self.token, "15minute", lookback_days=3)
        if df15.empty or len(df15) < 20:
            return None

        df15["ema9"]  = calc_ema(df15["close"], 9)
        df15["ema21"] = calc_ema(df15["close"], 21)
        df15["st_dir"] = calc_supertrend(df15)

        # Latest values
        ltp      = df5["close"].iloc[-1]
        ema9_5   = df5["ema9"].iloc[-1]
        ema21_5  = df5["ema21"].iloc[-1]
        vwap_5   = df5["vwap"].iloc[-1]
        ema9_15  = df15["ema9"].iloc[-1]
        ema21_15 = df15["ema21"].iloc[-1]
        st_dir   = df15["st_dir"].iloc[-1]

        # CALL conditions
        call_conditions = [
            ltp > self.orb_high,       # ORB breakout up
            ema9_5  > ema21_5,         # 5min EMA bullish
            ema9_15 > ema21_15,        # 15min EMA bullish
            ltp > vwap_5,              # Above VWAP
            st_dir == 1,               # Supertrend up
        ]

        # PUT conditions
        put_conditions = [
            ltp < self.orb_low,        # ORB breakdown
            ema9_5  < ema21_5,
            ema9_15 < ema21_15,
            ltp < vwap_5,
            st_dir == -1,
        ]

        if all(call_conditions):
            log.info(f"{self.symbol}: ✅ CALL signal | LTP={ltp}")
            return "CALL"
        elif all(put_conditions):
            log.info(f"{self.symbol}: ✅ PUT signal | LTP={ltp}")
            return "PUT"

        return None

    def execute_trade(self, direction: str):
        """Find ATM option, calculate entry/SL/target, send alert."""
        ltp    = get_ltp(self.token)
        strike = get_atm_strike(ltp, self.symbol)
        expiry = get_weekly_expiry()
        opt_type = "CE" if direction == "CALL" else "PE"

        # Build option symbol (Zerodha format)
        opt_symbol = f"{self.symbol}{expiry}{strike}{opt_type}"

        # Fetch option LTP
        try:
            instruments = kite.instruments("NFO")
            opt_df = pd.DataFrame(instruments)
            match = opt_df[opt_df["tradingsymbol"] == opt_symbol]
            if match.empty:
                log.warning(f"Option not found: {opt_symbol}")
                return
            opt_token = match.iloc[0]["instrument_token"]
            opt_ltp_data = kite.ltp([f"NFO:{opt_token}"])
            opt_ltp = list(opt_ltp_data.values())[0]["last_price"]
        except Exception as e:
            log.error(f"Option LTP fetch failed: {e}")
            return

        lots  = max(1, int(OPTION_BUDGET / (opt_ltp * LOT_SIZE[self.symbol])))
        sl    = round(opt_ltp * (1 - SL_PERCENT / 100), 1)
        tgt   = round(opt_ltp * (1 + TARGET_PERCENT / 100), 1)

        self.active_trade = {
            "symbol":     self.symbol,
            "opt_symbol": opt_symbol,
            "opt_token":  opt_token,
            "direction":  direction,
            "entry":      opt_ltp,
            "sl":         sl,
            "target":     tgt,
            "lots":       lots,
            "status":     "OPEN"
        }
        self.trade_today += 1

        # Strategy label for alert
        strategy = f"ORB+EMA+VWAP | 5m+15m"
        alert_signal(
            self.symbol, direction, opt_ltp, sl, tgt,
            strategy, expiry, strike, opt_type
        )

    def monitor_trade(self):
        """Check SL/Target on active trade every 5min."""
        if not self.active_trade or self.active_trade["status"] != "OPEN":
            return

        trade = self.active_trade
        try:
            ltp_data = kite.ltp([f"NFO:{trade['opt_token']}"])
            current  = list(ltp_data.values())[0]["last_price"]
        except Exception as e:
            log.error(f"Monitor LTP error: {e}")
            return

        pnl = (current - trade["entry"]) * trade["lots"] * LOT_SIZE[self.symbol]

        if current <= trade["sl"]:
            self._close_trade("🛑 SL HIT", current, pnl)
        elif current >= trade["target"]:
            self._close_trade("🎯 TARGET HIT", current, pnl)
        else:
            # Send followup update every 15 mins (caller controls frequency)
            alert_followup(
                trade["symbol"], trade["opt_symbol"],
                "UPDATE", trade["entry"], current, pnl, trade["lots"]
            )

    def _close_trade(self, status: str, exit_price: float, pnl: float):
        trade = self.active_trade
        alert_followup(
            trade["symbol"], trade["opt_symbol"],
            status, trade["entry"], exit_price, pnl, trade["lots"]
        )
        self.daily_pnl += pnl
        self.trades_log.append({
            "symbol": trade["opt_symbol"],
            "type":   trade["direction"],
            "entry":  trade["entry"],
            "exit":   exit_price,
            "pnl":    pnl
        })
        self.active_trade = None
        log.info(f"{self.symbol} trade closed: {status} | P&L: ₹{pnl:+.0f}")

    def force_squareoff(self):
        """3:10 PM — force close any open trade."""
        if self.active_trade and self.active_trade["status"] == "OPEN":
            try:
                ltp_data = kite.ltp([f"NFO:{self.active_trade['opt_token']}"])
                current  = list(ltp_data.values())[0]["last_price"]
            except:
                current = self.active_trade["entry"]
            pnl = (current - self.active_trade["entry"]) * \
                  self.active_trade["lots"] * LOT_SIZE[self.symbol]
            self._close_trade("⏰ SQUAREOFF (EOD)", current, pnl)

    def reset_day(self):
        """Reset counters for new trading day."""
        self.orb_set     = False
        self.orb_high    = None
        self.orb_low     = None
        self.trade_today = 0
        self.daily_pnl   = 0.0
        self.active_trade = None
        self.trades_log   = []
        log.info(f"{self.symbol}: Day reset ✅")


# ─────────────────────────────────────────────
#  BOT ORCHESTRATOR
# ─────────────────────────────────────────────
class HybridBot:
    def __init__(self):
        self.bn   = HybridStrategy("BANKNIFTY", BANKNIFTY_TOKEN)
        self.nf   = HybridStrategy("NIFTY",     NIFTY_TOKEN)
        self._last_followup = datetime.now()

    def run_orb_setup(self):
        log.info("📐 Setting ORB...")
        self.bn.set_orb()
        self.nf.set_orb()

    def run_signal_check(self):
        for strat in [self.bn, self.nf]:
            if strat.active_trade:
                continue   # already in trade, skip new signal
            signal = strat.get_signal()
            if signal:
                strat.execute_trade(signal)

    def run_monitor(self):
        self.bn.monitor_trade()
        self.nf.monitor_trade()

    def run_followup(self):
        """Send followup update every 15 min if in trade."""
        now = datetime.now()
        if (now - self._last_followup).seconds >= 900:
            self.bn.monitor_trade()
            self.nf.monitor_trade()
            self._last_followup = now

    def run_squareoff(self):
        self.bn.force_squareoff()
        self.nf.force_squareoff()
        # Daily summary
        all_trades = self.bn.trades_log + self.nf.trades_log
        alert_daily_summary({"trades": all_trades})

    def reset_day(self):
        self.bn.reset_day()
        self.nf.reset_day()
        send_telegram(
            f"🌅 <b>New Day Started</b>\n"
            f"📅 {datetime.now().strftime('%d %b %Y')}\n"
            f"🤖 Bot is ready. ORB will be set at 9:30."
        )


# ─────────────────────────────────────────────
#  SCHEDULER
# ─────────────────────────────────────────────
def main():
    # ── Set access token BEFORE running ──
    ACCESS_TOKEN_INPUT = input("Paste today's Kite Access Token: ").strip()
    init_kite(ACCESS_TOKEN_INPUT)

    bot = HybridBot()

    # ── Schedule Jobs ──
    schedule.every().day.at("09:00").do(bot.reset_day)
    schedule.every().day.at("09:31").do(bot.run_orb_setup)      # ORB after 9:30 close

    # Signal check every 5 minutes during market hours
    for minute in range(0, 60, 5):
        t = f"{minute:02d}"
        for hour in range(9, 14):
            schedule.every().day.at(f"{hour:02d}:{t}").do(bot.run_signal_check)
            schedule.every().day.at(f"{hour:02d}:{t}").do(bot.run_monitor)

    # Followup every 15 min
    for minute in range(0, 60, 15):
        t = f"{minute:02d}"
        for hour in range(9, 15):
            schedule.every().day.at(f"{hour:02d}:{t}").do(bot.run_followup)

    schedule.every().day.at("15:10").do(bot.run_squareoff)

    log.info("🚀 Bot running... Press Ctrl+C to stop.")
    send_telegram("🚀 <b>Hybrid Bot Scheduler Started</b>\n⏰ Waiting for market open...")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
