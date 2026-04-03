"""
╔══════════════════════════════════════════════════════════════════╗
║   KITE AUTO-LOGIN HELPER                                         ║
║   Fix: Intercept redirect URL before chrome-error occurs         ║
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
#  CREDENTIALS
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
#  AUTO LOGIN — Intercept redirect
# ─────────────────────────────────────────────
def auto_login() -> str:
    log.info("🌐 Starting auto-login with Playwright...")
    kite      = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()

    captured_token = {"value": None}   # mutable container for closure

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

        context = browser.new_context()

        # ── KEY FIX: Intercept ALL requests ──
        # Kite redirects to 127.0.0.1/?request_token=xxx
        # We catch it BEFORE browser tries to load it (which fails)
        def handle_request(route, request):
            url = request.url
            if "request_token=" in url:
                token = url.split("request_token=")[1].split("&")[0]
                captured_token["value"] = token
                log.info(f"✅ request_token intercepted: {token[:8]}...")
                # Abort the request — we don't need it to load
                route.abort()
            else:
                route.continue_()

        context.route("**/*", handle_request)
        page = context.new_page()

        try:
            # ── Open login page ──
            log.info("Opening Kite login page...")
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)

            # ── Enter User ID ──
            page.wait_for_selector("#userid", timeout=15000)
            page.fill("#userid", KITE_USER_ID)
            page.wait_for_timeout(400)

            # ── Enter Password ──
            page.fill("#password", KITE_PASSWORD)
            page.wait_for_timeout(400)

            # ── Click Login ──
            page.click("button[type='submit']")
            log.info("🔐 Credentials submitted, waiting for 2FA...")
            page.wait_for_timeout(3000)

            # ── TOTP ──
            page.wait_for_selector("input[type='number']", timeout=15000)

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

            # ── Submit TOTP ──
            try:
                page.click("button[type='submit']")
            except Exception:
                pass

            # ── Wait for redirect to be intercepted (max 15 sec) ──
            log.info("⏳ Waiting for request_token...")
            for _ in range(30):
                if captured_token["value"]:
                    break
                time.sleep(0.5)

            if not captured_token["value"]:
                page.screenshot(path="/tmp/login_debug.png")
                raise Exception("request_token not captured within timeout.")

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

    # ── Generate Access Token ──
    request_token = captured_token["value"]
    log.info(f"🔑 Generating session with request_token: {request_token[:8]}...")
    data         = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]
    log.info("✅ Access token generated!")
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
