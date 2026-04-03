"""
╔══════════════════════════════════════════════════════════════════╗
║   MAIN LAUNCHER — Hybrid Trading Bot                             ║
║   Auto-login + Bot start in one command                          ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import subprocess
import schedule
import time
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  STEP 0: Ensure Playwright browser installed
# ─────────────────────────────────────────────
def ensure_playwright():
    browser_path = os.path.expanduser("~/.cache/ms-playwright")
    chromium_ready = False

    if os.path.exists(browser_path):
        for root, dirs, files in os.walk(browser_path):
            for f in files:
                if "chrome" in f.lower() or "chromium" in f.lower():
                    chromium_ready = True
                    break

    if not chromium_ready:
        log.info("📦 Installing Playwright Chromium browser...")
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True, timeout=300
            )
            subprocess.run(
                [sys.executable, "-m", "playwright", "install-deps", "chromium"],
                check=True, timeout=120
            )
            log.info("✅ Playwright Chromium installed successfully.")
        except subprocess.CalledProcessError as e:
            log.error(f"❌ Playwright install failed: {e}")
            # Try without install-deps (may not have sudo)
            try:
                subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    check=True, timeout=300
                )
                log.info("✅ Playwright Chromium installed (without deps).")
            except Exception as e2:
                log.error(f"❌ Playwright install failed again: {e2}")
                raise
    else:
        log.info("✅ Playwright Chromium already installed.")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def start():
    print("\n" + "═"*55)
    print("  🤖  HYBRID TRADING BOT — BankNifty & Nifty")
    print("  📊  Strategy: ORB + EMA9/21 + VWAP + Supertrend")
    print("  ⏰  Timeframe: 5min entry | 15min confirmation")
    print("  🎯  Instrument: Options (CE/PE buying)")
    print("═"*55 + "\n")

    # ── Ensure browser ready ──
    ensure_playwright()

    # ── Import after playwright check ──
    from kite_auto_login import get_access_token
    from hybrid_trading_bot import HybridBot, init_kite, send_telegram

    # ── Get Access Token ──
    log.info("🔐 Getting access token...")
    try:
        access_token = get_access_token()
        log.info("✅ Token ready.")
    except Exception as e:
        log.error(f"❌ Login failed: {e}")
        send_telegram(f"❌ <b>Bot Failed to Start</b>\nLogin error: {str(e)[:200]}")
        return

    # ── Init Kite ──
    init_kite(access_token)

    # ── Setup Bot ──
    bot = HybridBot()

    # ── Schedule ──
    schedule.every().day.at("09:00").do(bot.reset_day)
    schedule.every().day.at("09:31").do(bot.run_orb_setup)

    for hour in range(9, 15):
        for minute in range(0, 60, 5):
            t = f"{hour:02d}:{minute:02d}"
            schedule.every().day.at(t).do(bot.run_signal_check)
            schedule.every().day.at(t).do(bot.run_monitor)

    for hour in range(9, 15):
        for minute in range(0, 60, 15):
            t = f"{hour:02d}:{minute:02d}"
            schedule.every().day.at(t).do(bot.run_followup)

    schedule.every().day.at("15:10").do(bot.run_squareoff)

    log.info("🚀 Bot is LIVE. Scheduler running...")
    send_telegram(
        f"🚀 <b>Bot LIVE!</b>\n"
        f"📅 {datetime.now().strftime('%d %b %Y')}\n"
        f"✅ Auto-login successful\n"
        f"⏳ ORB will be set at 9:31 AM"
    )

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    start()
