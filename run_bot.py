"""
╔══════════════════════════════════════════════════════════════════╗
║   MAIN LAUNCHER — Hybrid Trading Bot                             ║
║   Auto-login + Bot start in one command                          ║
╚══════════════════════════════════════════════════════════════════╝

RUN: python run_bot.py
"""

import schedule
import time
import logging
from datetime import datetime
from kite_auto_login import get_access_token
from hybrid_trading_bot import HybridBot, init_kite, send_telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot_main.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def start():
    print("\n" + "═"*55)
    print("  🤖  HYBRID TRADING BOT — BankNifty & Nifty")
    print("  📊  Strategy: ORB + EMA9/21 + VWAP + Supertrend")
    print("  ⏰  Timeframe: 5min entry | 15min confirmation")
    print("  🎯  Instrument: Options (CE/PE buying)")
    print("═"*55 + "\n")

    # ── Step 1: Get Access Token (auto) ──
    log.info("🔐 Getting access token...")
    try:
        access_token = get_access_token()
        log.info("✅ Token ready.")
    except Exception as e:
        log.error(f"❌ Login failed: {e}")
        send_telegram(f"❌ <b>Bot Failed to Start</b>\nLogin error: {str(e)[:200]}")
        return

    # ── Step 2: Init Kite ──
    init_kite(access_token)

    # ── Step 3: Setup Bot ──
    bot = HybridBot()

    # ── Step 4: Schedule ──
    schedule.every().day.at("09:00").do(bot.reset_day)
    schedule.every().day.at("09:31").do(bot.run_orb_setup)

    # Signal + Monitor every 5 min from 9:31 to 14:00
    for hour in range(9, 15):
        for minute in range(0, 60, 5):
            t = f"{hour:02d}:{minute:02d}"
            schedule.every().day.at(t).do(bot.run_signal_check)
            schedule.every().day.at(t).do(bot.run_monitor)

    # Followup every 15 min
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
