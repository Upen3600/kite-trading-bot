"""
╔══════════════════════════════════════════════════════════════════╗
║   KITE AUTO-LOGIN HELPER                                         ║
║   Using: Playwright (works perfectly on Railway)                 ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import time
import pyotp
import requests
import logging
from datetime import datetime
from playwright.sync_api import sync_playwright
from kiteconnect import KiteConnect

# ─────────────────────────────────────────────
#  CREDENTIALS — Railway Variables se auto-load
# ─────────────────────────────────────────────
API_KEY          = os.environ.get("API_KEY",          "yj3cey9o0ho0gi1b")
API_SECRET       = os.environ.get("API_SECRET",       "")
KITE_USER_ID     = os.environ.get("KITE_USER_ID",     "")
KITE_PASSWORD    = os.environ.get("KITE_PASSWORD",    "")
TOTP_SECRET      = os.environ.get("TOTP_SECRET",      "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "8620220458:AAG-oxvhWhPio7iX9pWCk-0AFovl5KrUXxc")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "-1003780954866")

TOKEN_FILE = "/tmp/token.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log.error(f"Telegram error: {e}")


# ─────────────────────────────────────────────
#  TOKEN SAVE / LOAD
# ─────────────────────────────────────────────
def save_token(access_token: str):
    with open(TOKEN_FILE, "w") as f:
        f.write(f"{access_token}\n{datetime.now().strftime('%Y-%m-%d')}")
    log.info("✅ Token saved.")


def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r") as f:
            lines = f.read().strip().split("\n")
        token      = lines[0]
        saved_date = lines[1] if len(lines) > 1 else ""
        if saved_date == datetime.now().strftime("%Y-%m-%d") and token:
            log.info("✅ Valid token found for today.")
            return token
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
#  AUTO LOGIN — Playwright
# ─────────────────────────────────────────────
def auto_login() -> str:
    log.info("🌐 Starting auto-login with Playwright...")
    kite = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()
    request_token = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ]
        )
        page = browser.new_page()

        try:
            # ── Step 1: Open login page ──
            log.info(f"Opening login page...")
            page.goto(login_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)

            # ── Step 2: Enter User ID ──
            page.fill("#userid", KITE_USER_ID)
            page.wait_for_timeout(400)

            # ── Step 3: Enter Password ──
            page.fill("#password", KITE_PASSWORD)
            page.wait_for_timeout(400)

            # ── Step 4: Click Login ──
            page.click("button[type='submit']")
            log.info("🔐 Credentials submitted, waiting for 2FA...")
            page.wait_for_timeout(3000)

            # ── Step 5: TOTP ──
            try:
                page.wait_for_selector("input[type='number']", timeout=15000)
            except Exception:
                # Try alternate selectors
                page.wait_for_selector("input[label='External TOTP']", timeout=5000)

            if TOTP_SECRET:
                totp_code = pyotp.TOTP(TOTP_SECRET).now()
                log.info("🔑 Auto TOTP generated.")
            else:
                send_telegram(
                    "🔐 <b>TOTP Required</b>\n"
                    "Telegram pe 6-digit code reply karo\n"
                    "<i>(60 seconds ke andar)</i>"
                )
                totp_code = wait_for_telegram_totp()

            page.fill("input[type='number']", totp_code)
            page.wait_for_timeout(500)

            # ── Step 6: Submit TOTP ──
            try:
                page.click("button[type='submit']")
            except Exception:
                pass

            # ── Step 7: Wait for redirect ──
            log.info("⏳ Waiting for redirect...")
            page.wait_for_timeout(5000)

            current_url = page.url
            log.info(f"Current URL: {current_url[:80]}")

            # Wait more if needed
            if "request_token=" not in current_url:
                page.wait_for_timeout(4000)
                current_url = page.url

            if "request_token=" not in current_url:
                # Try waiting for URL change
                try:
                    page.wait_for_url("**/request_token=**", timeout=10000)
                    current_url = page.url
                except Exception:
                    pass

            if "request_token=" not in current_url:
                # Screenshot for debug
                page.screenshot(path="/tmp/login_debug.png")
                raise Exception(f"request_token not found. URL: {current_url}")

            request_token = current_url.split("request_token=")[1].split("&")[0]
            log.info(f"✅ request_token captured: {request_token[:8]}...")

        except Exception as e:
            log.error(f"Login error: {e}")
            try:
                page.screenshot(path="/tmp/login_error.png")
            except Exception:
                pass
            send_telegram(f"❌ <b>Auto-Login Failed</b>\n{str(e)[:200]}")
            raise
        finally:
            browser.close()

    # ── Step 8: Generate Access Token ──
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]
    log.info(f"✅ Access token generated!")
    return access_token


# ─────────────────────────────────────────────
#  TELEGRAM TOTP LISTENER
# ─────────────────────────────────────────────
def wait_for_telegram_totp(timeout=90) -> str:
    url      = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    deadline = time.time() + timeout
    try:
        r       = requests.get(url, params={"timeout": 0}, timeout=5)
        updates = r.json().get("result", [])
        offset  = updates[-1]["update_id"] + 1 if updates else 0
    except Exception:
        offset = 0

    while time.time() < deadline:
        try:
            r = requests.get(url, params={"timeout": 30, "offset": offset}, timeout=35)
            for update in r.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {}).get("text", "").strip()
                if msg.isdigit() and len(msg) == 6:
                    log.info("📲 TOTP received from Telegram.")
                    return msg
        except Exception:
            time.sleep(2)

    raise TimeoutError("TOTP not received in time.")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def get_access_token(force_refresh: bool = False) -> str:
    if not force_refresh:
        cached = load_token()
        if cached:
            return cached

    send_telegram(
        f"🌅 <b>Auto-Login Starting</b>\n"
        f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}"
    )
    access_token = auto_login()
    save_token(access_token)
    send_telegram("✅ <b>Login Successful!</b>\n🤖 Bot starting now...")
    return access_token


if __name__ == "__main__":
    token = get_access_token()
    print(f"✅ Token: {token[:8]}...{token[-4:]}")
