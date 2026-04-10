"""
╔══════════════════════════════════════════════════════════════════╗
║   MAIN LAUNCHER v2.0 — Hybrid Bot + Dashboard                    ║
║   Fixed: NameError crash, duplicate alerts, market data thread   ║
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

# ── Token constants (needed in push_ema) ──
BANKNIFTY_TOKEN = int(os.environ.get("BANKNIFTY_TOKEN", "260105"))
NIFTY_TOKEN     = int(os.environ.get("NIFTY_TOKEN",     "256265"))


# ─────────────────────────────────────────────
#  PLAYWRIGHT INSTALL
# ─────────────────────────────────────────────
def ensure_playwright():
    browser_path = os.path.expanduser("~/.cache/ms-playwright")
    ready = False
    if os.path.exists(browser_path):
        for _, _, files in os.walk(browser_path):
            if any("chrome" in f.lower() for f in files):
                ready = True
                break
    if not ready:
        log.info("📦 Installing Playwright Chromium...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, timeout=300
        )
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install-deps", "chromium"],
                check=True, timeout=120
            )
        except Exception:
            pass
        log.info("✅ Playwright ready.")
    else:
        log.info("✅ Playwright already installed.")


# ─────────────────────────────────────────────
#  IST → UTC conversion for schedule library
#  Railway server runs UTC — IST = UTC + 5:30
# ─────────────────────────────────────────────
def sched(hh: int, mm: int, fn):
    total = hh * 60 + mm - 330
    if total < 0:
        total += 1440
    t = f"{total // 60:02d}:{total % 60:02d}"
    schedule.every().day.at(t).do(fn)
    log.info(f"  Scheduled {fn.__name__:30s} → {hh:02d}:{mm:02d} IST ({t} UTC)")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def start():
    print("\n" + "═" * 55)
    print("  ⚡ HYBRID TRADING BOT v2.0")
    print("  📊 BankNifty & Nifty | ORB+EMA9/21+VWAP+ST")
    print("  ⏰ IST Timezone | Auto-login 8:45 AM daily")
    print("  🌐 Dashboard: Railway public URL")
    print("═" * 55 + "\n")

    ensure_playwright()

    # ── Imports ──
    from kite_auto_login  import get_access_token
    from hybrid_trading_bot import (HybridBot, init_kite, send_telegram,
                                    get_ohlc, calc_ema)
    from dashboard import run_dashboard, start_ticker, update_ema

    # ── Start dashboard (background) ──
    threading.Thread(target=run_dashboard, daemon=True).start()
    log.info("🌐 Dashboard thread started.")

    # ── Initial login ──
    log.info("🔐 Logging in to Kite...")
    try:
        token = get_access_token()
        log.info("✅ Token obtained.")
    except Exception as e:
        log.error(f"Login failed: {e}")
        # send_telegram without init — use requests directly
        import requests as _req
        tg_token = os.environ.get("TELEGRAM_TOKEN",
                                  "8620220458:AAG-oxvhWhPio7iX9pWCk-0AFovl5KrUXxc")
        tg_chat  = os.environ.get("TELEGRAM_CHAT_ID", "-1003780954866")
        _req.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                  json={"chat_id": tg_chat,
                        "text": f"❌ <b>Bot Failed to Start</b>\n{str(e)[:200]}",
                        "parse_mode": "HTML"}, timeout=10)
        return

    init_kite(token)
    bot = HybridBot()

    # ── KiteTicker — tick by tick ──
    start_ticker(token)
    log.info("📡 KiteTicker started — tick by tick live!")

    # ── Market data updater (fallback for EMA/OHLC when market closed) ──
    def market_data_loop():
        while True:
            try:
                bot.update_market_data()
            except Exception as e:
                log.error(f"Market data loop error: {e}")
            time.sleep(30)   # every 30s — tick handles live LTP

    threading.Thread(target=market_data_loop, daemon=True).start()
    log.info("📊 Market data updater started (30s interval).")

    # ── EMA push to dashboard every 5 min ──
    def push_ema():
        for sym_key, tk in [("bn", BANKNIFTY_TOKEN), ("nf", NIFTY_TOKEN)]:
            try:
                df = get_ohlc(tk, "5minute", days=10)
                if df.empty:
                    continue
                e50  = float(calc_ema(df["close"], 50).iloc[-1])
                e200 = float(calc_ema(df["close"], 200).iloc[-1])
                update_ema(sym_key, e50, e200)
                log.info(f"EMA pushed {sym_key.upper()}: EMA50={e50:.1f} EMA200={e200:.1f}")
            except Exception as e:
                log.error(f"EMA push error ({sym_key}): {e}")

    # ── Daily token refresh at 8:45 AM IST ──
    def refresh_token():
        log.info("🔄 Refreshing token (8:45 AM IST)...")
        try:
            new_token = get_access_token(force_refresh=True)
            init_kite(new_token)
            bot.reset_day()
            start_ticker(new_token)
            log.info("✅ Token refreshed + Ticker restarted.")
        except Exception as e:
            log.error(f"Token refresh failed: {e}")
            send_telegram(f"❌ <b>Token Refresh Failed</b>\n{str(e)[:200]}")

    # ─────────────────────────────────────────
    #  SCHEDULE (all times IST)
    # ─────────────────────────────────────────
    log.info("\n📅 Scheduling jobs (IST):")
    sched(8,  45, refresh_token)           # Token refresh + day reset
    sched(9,  15, bot.market_open_alert)   # Market open alert
    sched(9,  31, bot.run_orb_setup)       # ORB set after 9:30

    # Signal check + monitor: every 5 min from 9:35 to 14:00
    for h in range(9, 15):
        for m in range(0, 60, 5):
            if h == 9  and m < 35: continue
            if h == 14 and m >  0: break
            sched(h, m, bot.run_signal_check)
            sched(h, m, bot.run_monitor)

    # EMA push: every 5 min from 9:35 to 15:30
    for h in range(9, 16):
        for m in range(0, 60, 5):
            if h == 9  and m < 35: continue
            if h == 15 and m > 30: break
            sched(h, m, push_ema)

    sched(15, 10, bot.run_squareoff)       # EOD squareoff + summary
    sched(15, 30, bot.market_close_alert)  # Market close alert

    # ─────────────────────────────────────────
    #  STARTUP COMPLETE
    # ─────────────────────────────────────────
    now_ist = datetime.now(IST)
    send_telegram(
        f"🚀 <b>Bot v2.0 LIVE!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {now_ist.strftime('%d %b %Y %H:%M IST')}\n"
        f"✅ Kite connected\n"
        f"📡 KiteTicker WebSocket active\n"
        f"🌐 Dashboard: Railway → Settings → Networking\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Strategy:</b> ORB + EMA9/21 + VWAP + Supertrend\n"
        f"⏰ <b>Trade window:</b> 9:31 AM – 2:00 PM IST\n"
        f"🛑 <b>SL:</b> 30%  |  🎯 <b>Target:</b> 60%\n"
        f"⏳ Market opens at 9:15 AM IST"
    )
    log.info("🚀 Bot LIVE! Entering scheduler loop...")

    while True:
        schedule.run_pending()
        time.sleep(20)


if __name__ == "__main__":
    start()
