"""
╔══════════════════════════════════════════════════════════════════╗
║   MAIN LAUNCHER — Hybrid Trading Bot + Dashboard                 ║
║   Timezone: IST (Asia/Kolkata)                                   ║
║   Login: 8:45 AM IST daily (auto token refresh)                  ║
║   Dashboard: Live on Railway public URL                          ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import subprocess
import time
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import schedule

IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  PLAYWRIGHT INSTALL
# ─────────────────────────────────────────────
def ensure_playwright():
    browser_path = os.path.expanduser("~/.cache/ms-playwright")
    ready = False
    if os.path.exists(browser_path):
        for _, _, files in os.walk(browser_path):
            if any("chrome" in f.lower() for f in files):
                ready = True; break
    if not ready:
        log.info("📦 Installing Playwright Chromium...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                       check=True, timeout=300)
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"],
                           check=True, timeout=120)
        except Exception:
            pass
        log.info("✅ Playwright ready.")
    else:
        log.info("✅ Playwright already installed.")


# ─────────────────────────────────────────────
#  IST → UTC for schedule library
# ─────────────────────────────────────────────
def sched(hh: int, mm: int, fn):
    total = hh * 60 + mm - 330
    if total < 0: total += 1440
    t = f"{total//60:02d}:{total%60:02d}"
    schedule.every().day.at(t).do(fn)
    log.info(f"Scheduled {fn.__name__} at {hh:02d}:{mm:02d} IST → {t} UTC")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def start():
    print("\n" + "═"*55)
    print("  🤖  HYBRID TRADING BOT + DASHBOARD")
    print("  📊  BankNifty & Nifty | ORB+EMA+VWAP+ST")
    print("  ⏰  IST Timezone | Login: 8:45 AM daily")
    print("  🌐  Dashboard: Check Railway public URL")
    print("═"*55 + "\n")

    ensure_playwright()

    from kite_auto_login import get_access_token
    from hybrid_trading_bot import HybridBot, init_kite, send_telegram
    from dashboard import run_dashboard

    # ── Start dashboard in background thread ──
    dash_thread = threading.Thread(target=run_dashboard, daemon=True)
    dash_thread.start()
    log.info("🌐 Dashboard started in background.")

    # ── Initial login ──
    log.info("🔐 Logging in to Kite...")
    try:
        token = get_access_token()
    except Exception as e:
        log.error(f"Login failed: {e}")
        send_telegram(f"❌ <b>Bot Failed to Start</b>\n{str(e)[:200]}")
        return

    init_kite(token)
    bot = HybridBot()

    # ── KiteTicker — tick by tick WebSocket ──
    from dashboard import start_ticker, update_ema
    start_ticker(token)
    log.info("📡 KiteTicker WebSocket started — tick by tick live!")

    # ── EMA updater — every 5 min, push to dashboard ──
    def push_ema():
        from hybrid_trading_bot import get_ohlc, calc_ema
        for sym, tk, skey in [("BANKNIFTY", BANKNIFTY_TOKEN, "bn"),
                               ("NIFTY",     NIFTY_TOKEN,     "nf")]:
            try:
                df = get_ohlc(tk, "5minute", days=10)
                if df.empty: continue
                e50  = float(calc_ema(df["close"], 50).iloc[-1])
                e200 = float(calc_ema(df["close"], 200).iloc[-1])
                update_ema(skey, e50, e200)
            except Exception as e:
                log.error(f"EMA push error ({sym}): {e}")

    sched(9,  35, push_ema)
    for h in range(9, 16):
        for m in range(0, 60, 5):
            if h == 9 and m < 35: continue
            sched(h, m, push_ema)

    # ── Daily refresh at 8:45 AM IST ──
    def refresh_token():
        log.info("🔄 Refreshing token (8:45 AM IST)...")
        try:
            new_token = get_access_token(force_refresh=True)
            init_kite(new_token)
            bot.reset_day()
            start_ticker(new_token)   # Restart WebSocket with new token
            log.info("✅ Token refreshed + Ticker restarted.")
        except Exception as e:
            send_telegram(f"❌ <b>Token Refresh Failed</b>\n{str(e)[:200]}")

    # ─── All schedules in IST ───────────────
    sched(8,  45, refresh_token)           # Token refresh + day reset
    sched(9,  15, bot.market_open_alert)   # Market open: BN & NF LTP
    sched(9,  31, bot.run_orb_setup)       # ORB after 9:30 close

    # Signal + Monitor every 5 min: 9:35 → 14:00
    for h in range(9, 15):
        for m in range(0, 60, 5):
            if h == 9  and m < 35: continue
            if h == 14 and m >  0: break
            sched(h, m, bot.run_signal_check)
            sched(h, m, bot.run_monitor)

    sched(15, 10, bot.run_squareoff)       # Force squareoff + daily summary
    sched(15, 30, bot.market_close_alert)  # Market close alert

    log.info("🚀 Bot LIVE! Scheduler running...")
    send_telegram(
        f"🚀 <b>Bot + Dashboard LIVE!</b>\n"
        f"📅 {datetime.now(IST).strftime('%d %b %Y %H:%M IST')}\n"
        f"✅ Kite connected\n"
        f"🌐 Dashboard: Check Railway → Settings → Networking → Public URL\n"
        f"⏳ Next event: Market open alert at 9:15 AM IST"
    )

    while True:
        schedule.run_pending()
        time.sleep(20)


if __name__ == "__main__":
    start()
