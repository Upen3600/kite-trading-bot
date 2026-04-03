"""
╔══════════════════════════════════════════════════════════════════╗
║   KITE AUTO-LOGIN — DEBUG VERSION                                ║
║   Sends screenshots to Telegram at every step                    ║
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
#  TELEGRAM — Text + Photo
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


def send_screenshot(path: str, caption: str = ""):
    """Send screenshot image to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(path, "rb") as f:
            requests.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption
            }, files={"photo": f}, timeout=15)
        log.info(f"📸 Screenshot sent: {caption}")
    except Exception as e:
        log.error(f"Screenshot send error: {e}")


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
    log.info("🌐 Starting auto-login (DEBUG mode)...")
    kite      = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()

    captured_token   = {"value": None}
    intercepted_urls = []

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

        # ── Intercept ALL requests — log every URL ──
        def handle_request(route, request):
            url = request.url
            intercepted_urls.append(url)

            if "request_token=" in url:
                token = url.split("request_token=")[1].split("&")[0]
                captured_token["value"] = token
                log.info(f"✅ request_token INTERCEPTED: {token[:8]}...")
                route.abort()
            elif url.startswith("http://127.0.0.1") or url.startswith("https://127.0.0.1"):
                log.info(f"🔍 Redirect to 127: {url[:100]}")
                route.abort()
            else:
                route.continue_()

        context.route("**/*", handle_request)
        page = context.new_page()

        try:
            # Step 1: Login page
            log.info("Opening Kite login page...")
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            page.screenshot(path="/tmp/step1_login.png")
            send_screenshot("/tmp/step1_login.png", "Step 1: Login page loaded")

            # Step 2: Fill credentials
            page.wait_for_selector("#userid", timeout=15000)
            page.fill("#userid", KITE_USER_ID)
            page.wait_for_timeout(400)
            page.fill("#password", KITE_PASSWORD)
            page.wait_for_timeout(400)
            page.click("button[type='submit']")
            log.info("🔐 Credentials submitted...")
            page.wait_for_timeout(3000)

            page.screenshot(path="/tmp/step2_after_login.png")
            send_screenshot("/tmp/step2_after_login.png", "Step 2: After login submit")

            # Step 3: TOTP
            log.info("Looking for TOTP field...")
            try:
                page.wait_for_selector("input[type='number']", timeout=10000)
                totp_found = True
            except Exception:
                totp_found = False
                log.warning("TOTP number input not found, trying other selectors...")
                try:
                    page.wait_for_selector("input[autocomplete='one-time-code']", timeout=5000)
                    totp_found = True
                except Exception:
                    pass

            page.screenshot(path="/tmp/step3_totp_screen.png")
            send_screenshot("/tmp/step3_totp_screen.png", f"Step 3: TOTP screen (found={totp_found})")

            # Send current URL info
            send_telegram(f"🔍 <b>Debug Info</b>\nURL after login: <code>{page.url[:100]}</code>")

            if totp_found:
                if TOTP_SECRET:
                    totp_code = pyotp.TOTP(TOTP_SECRET).now()
                    log.info(f"🔑 Auto TOTP: {totp_code}")
                else:
                    send_telegram("🔐 <b>TOTP Required</b>\n6-digit code reply karo (60s)")
                    totp_code = wait_for_telegram_totp()

                # Try filling TOTP
                try:
                    page.fill("input[type='number']", totp_code)
                except Exception:
                    page.fill("input[autocomplete='one-time-code']", totp_code)

                page.wait_for_timeout(500)

                try:
                    page.click("button[type='submit']")
                    log.info("TOTP submitted.")
                except Exception:
                    log.warning("Submit button not found after TOTP.")

                page.wait_for_timeout(3000)
                page.screenshot(path="/tmp/step4_after_totp.png")
                send_screenshot("/tmp/step4_after_totp.png", "Step 4: After TOTP submit")
                send_telegram(f"🔍 URL after TOTP: <code>{page.url[:150]}</code>")

            # Step 5: Wait for token (15 sec)
            log.info("⏳ Waiting for request_token intercept...")
            for i in range(30):
                if captured_token["value"]:
                    break
                time.sleep(0.5)
                if i % 10 == 9:
                    page.screenshot(path=f"/tmp/wait_{i}.png")
                    send_screenshot(f"/tmp/wait_{i}.png", f"⏳ Waiting... {i+1}/30\nURL: {page.url[:80]}")

            if not captured_token["value"]:
                # Send all intercepted URLs for debug
                url_log = "\n".join(intercepted_urls[-10:])
                send_telegram(f"🔍 <b>Last 10 intercepted URLs:</b>\n<code>{url_log[:500]}</code>")
                raise Exception(f"request_token not captured. Last URL: {page.url}")

        except Exception as e:
            log.error(f"Login error: {e}")
            try:
                page.screenshot(path="/tmp/login_error.png")
                send_screenshot("/tmp/login_error.png", f"❌ Error: {str(e)[:100]}")
            except Exception:
                pass
            send_telegram(f"❌ <b>Auto-Login Failed</b>\n{str(e)[:200]}")
            raise
        finally:
            browser.close()

    request_token = captured_token["value"]
    log.info(f"🔑 Generating session...")
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
        f"🌅 <b>Auto-Login Starting (Debug)</b>\n"
        f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}"
    )
    access_token = auto_login()
    save_token(access_token)
    send_telegram("✅ <b>Login Successful!</b>\n🤖 Bot starting now...")
    return access_token


if __name__ == "__main__":
    token = get_access_token()
    print(f"✅ Token: {token[:8]}...{token[-4:]}")
