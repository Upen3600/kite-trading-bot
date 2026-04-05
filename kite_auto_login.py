"""
╔══════════════════════════════════════════════════════════════════╗
║   KITE AUTO-LOGIN HELPER — FINAL FIX                             ║
║   Uses page.on('request') to catch 127.0.0.1 redirect           ║
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
#  AUTO LOGIN
# ─────────────────────────────────────────────
def auto_login() -> str:
    log.info("🌐 Starting auto-login...")
    kite      = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()

    captured_token = {"value": None}

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
        page    = context.new_page()

        # ── KEY FIX: Listen to ALL requests via event ──
        # context.route blocks some requests before event fires
        # page.on('request') catches EVERYTHING including failed navigations
        def on_request(request):
            url = request.url
            if "request_token=" in url:
                token = url.split("request_token=")[1].split("&")[0]
                captured_token["value"] = token
                log.info(f"✅ request_token captured from request event: {token[:8]}...")

        # Also listen on response — sometimes token is in redirect response header
        def on_response(response):
            url = response.url
            if "request_token=" in url:
                token = url.split("request_token=")[1].split("&")[0]
                captured_token["value"] = token
                log.info(f"✅ request_token captured from response event: {token[:8]}...")
            # Check Location header for redirect
            try:
                location = response.headers.get("location", "")
                if "request_token=" in location:
                    token = location.split("request_token=")[1].split("&")[0]
                    captured_token["value"] = token
                    log.info(f"✅ request_token from Location header: {token[:8]}...")
            except Exception:
                pass

        page.on("request",  on_request)
        page.on("response", on_response)

        try:
            # Open login
            log.info("Opening Kite login page...")
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # Fill User ID
            page.wait_for_selector("#userid", timeout=15000)
            page.fill("#userid", KITE_USER_ID)
            page.wait_for_timeout(400)

            # Fill Password
            page.fill("#password", KITE_PASSWORD)
            page.wait_for_timeout(400)

            # Submit login
            page.click("button[type='submit']")
            log.info("🔐 Credentials submitted...")
            page.wait_for_timeout(3000)

            # TOTP — field label is "External TOTP" (not type=number on all browsers)
            log.info("Waiting for TOTP field...")
            totp_selector = None
            for sel in [
                "input[type='number']",
                "input[autocomplete='one-time-code']",
                "input[label='External TOTP']",
                "input[placeholder]",
            ]:
                try:
                    page.wait_for_selector(sel, timeout=5000)
                    totp_selector = sel
                    log.info(f"TOTP field found: {sel}")
                    break
                except Exception:
                    continue

            if not totp_selector:
                raise Exception("TOTP input field not found on page.")

            # Generate TOTP
            if TOTP_SECRET:
                totp_code = pyotp.TOTP(TOTP_SECRET).now()
                log.info(f"🔑 Auto TOTP: {totp_code}")
            else:
                send_telegram(
                    "🔐 <b>TOTP Required</b>\n"
                    "Telegram pe 6-digit code reply karo\n"
                    "<i>(60 seconds ke andar)</i>"
                )
                totp_code = wait_for_telegram_totp()

            # Fill TOTP
            page.fill(totp_selector, totp_code)
            page.wait_for_timeout(600)

            # Submit TOTP — button text is "Continue"
            for btn_sel in [
                "button:has-text('Continue')",
                "button[type='submit']",
                "button",
            ]:
                try:
                    page.click(btn_sel, timeout=3000)
                    log.info(f"TOTP submitted via: {btn_sel}")
                    break
                except Exception:
                    continue

            # Wait for request_token to be captured (20 sec max)
            log.info("⏳ Waiting for request_token...")
            for _ in range(40):
                if captured_token["value"]:
                    break
                time.sleep(0.5)

            # Last resort: check current URL directly
            if not captured_token["value"]:
                current = page.url
                log.info(f"Final URL check: {current}")
                if "request_token=" in current:
                    captured_token["value"] = current.split("request_token=")[1].split("&")[0]

            if not captured_token["value"]:
                raise Exception(f"request_token not captured. Page URL: {page.url}")

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

    # Generate session
    request_token = captured_token["value"]
    log.info(f"🔑 Generating session with token: {request_token[:8]}...")
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
    raise TimeoutError("TOTP not received.")


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
