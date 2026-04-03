"""
╔══════════════════════════════════════════════════════════════════╗
║   KITE AUTO-LOGIN HELPER                                         ║
║   Automatically fetches Access Token every morning               ║
║   Uses: Selenium (headless browser) + Kite Connect API           ║
╚══════════════════════════════════════════════════════════════════╝

HOW IT WORKS:
  1. Opens Kite login page in headless Chrome
  2. Fills userid + password automatically
  3. Waits for you to enter TOTP (2FA) — or auto-fills if you provide secret
  4. Captures the request_token from redirect URL
  5. Exchanges it for access_token via Kite API
  6. Saves token to token.txt
  7. Sends Telegram confirmation
"""

import os
import time
import pyotp
import requests
import logging
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from kiteconnect import KiteConnect

# ─────────────────────────────────────────────
#  ⚙️  YOUR CREDENTIALS — Fill these once
# ─────────────────────────────────────────────
API_KEY        = "yj3cey9o0ho0gi1b"
API_SECRET     = "lfi1nsig48dhlv00ryowue0ey07b96nc"         # <-- Kite Developer Console se copy karo
KITE_USER_ID   = "OZ4378"         # <-- Your Zerodha Client ID (e.g. AB1234)
KITE_PASSWORD  = "Upen@2658$"         # <-- Your Zerodha login password
TOTP_SECRET    = "BXG2SWCEE5PONPTNDUP2COXCMHFW732X"         # <-- Authenticator app ka secret key (optional)
                             #     Agar blank rakho → manually TOTP enter karna hoga

TELEGRAM_TOKEN   = "8620220458:AAG-oxvhWhPio7iX9pWCk-0AFovl5KrUXxc"
TELEGRAM_CHAT_ID = "-1003780954866"

TOKEN_FILE     = "token.txt"   # Access token yahan save hoga

# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("autologin.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  TELEGRAM HELPER
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
#  SAVE / LOAD TOKEN
# ─────────────────────────────────────────────
def save_token(access_token: str):
    with open(TOKEN_FILE, "w") as f:
        f.write(f"{access_token}\n{datetime.now().strftime('%Y-%m-%d')}")
    log.info(f"✅ Token saved to {TOKEN_FILE}")


def load_token() -> str | None:
    """Load token if it was generated today."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r") as f:
            lines = f.read().strip().split("\n")
        token = lines[0]
        saved_date = lines[1] if len(lines) > 1 else ""
        today = datetime.now().strftime("%Y-%m-%d")
        if saved_date == today and token:
            log.info("✅ Valid token found for today.")
            return token
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
#  SELENIUM AUTO-LOGIN
# ─────────────────────────────────────────────
def auto_login() -> str:
    """
    Automates Kite web login and returns access_token.
    Steps:
      1. Open login.zerodha.com
      2. Fill userid + password
      3. Handle TOTP (auto or manual)
      4. Capture request_token from redirect
      5. Generate access_token
    """
    log.info("🌐 Starting auto-login...")

    # ── Chrome Options ──
    chrome_opts = Options()
    chrome_opts.add_argument("--headless=new")       # invisible browser
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--window-size=1280,800")
    chrome_opts.add_argument("--log-level=3")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_opts
    )
    wait = WebDriverWait(driver, 30)

    kite = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()

    try:
        # ── Step 1: Open login page ──
        log.info(f"Opening: {login_url}")
        driver.get(login_url)
        time.sleep(2)

        # ── Step 2: Enter User ID ──
        user_field = wait.until(EC.presence_of_element_located((By.ID, "userid")))
        user_field.clear()
        user_field.send_keys(KITE_USER_ID)
        time.sleep(0.5)

        # ── Step 3: Enter Password ──
        pass_field = driver.find_element(By.ID, "password")
        pass_field.clear()
        pass_field.send_keys(KITE_PASSWORD)
        time.sleep(0.5)

        # ── Step 4: Click Login ──
        login_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        login_btn.click()
        log.info("🔐 Login submitted, waiting for 2FA...")
        time.sleep(3)

        # ── Step 5: Handle TOTP ──
        totp_field = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//input[@type='number' or @label='External TOTP' or @placeholder]")
        ))

        if TOTP_SECRET:
            # Auto-generate TOTP
            totp = pyotp.TOTP(TOTP_SECRET)
            totp_code = totp.now()
            log.info(f"🔑 Auto TOTP: {totp_code}")
        else:
            # Manual TOTP — ask via Telegram then wait
            send_telegram(
                "🔐 <b>Kite Login — TOTP Required</b>\n"
                "Apna 6-digit authenticator code reply karo\n"
                "<i>(60 seconds me karo)</i>"
            )
            totp_code = wait_for_telegram_totp()

        totp_field.clear()
        totp_field.send_keys(totp_code)
        time.sleep(0.5)

        # ── Step 6: Submit TOTP ──
        try:
            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
            submit_btn.click()
        except Exception:
            pass  # Some flows auto-submit

        log.info("⏳ Waiting for redirect with request_token...")
        time.sleep(4)

        # ── Step 7: Capture request_token from URL ──
        current_url = driver.current_url
        log.info(f"Redirected to: {current_url}")

        if "request_token=" not in current_url:
            # Wait a bit more
            time.sleep(4)
            current_url = driver.current_url

        if "request_token=" not in current_url:
            raise Exception(f"request_token not found in URL: {current_url}")

        request_token = current_url.split("request_token=")[1].split("&")[0]
        log.info(f"✅ request_token captured: {request_token[:8]}...")

    except Exception as e:
        log.error(f"Login failed: {e}")
        driver.save_screenshot("login_error.png")
        send_telegram(f"❌ <b>Auto-Login Failed</b>\nError: {str(e)[:200]}")
        raise
    finally:
        driver.quit()

    # ── Step 8: Generate Access Token ──
    try:
        data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = data["access_token"]
        log.info(f"✅ Access token generated: {access_token[:8]}...")
        return access_token
    except Exception as e:
        log.error(f"Session generation failed: {e}")
        raise


# ─────────────────────────────────────────────
#  TELEGRAM TOTP LISTENER (manual fallback)
# ─────────────────────────────────────────────
def wait_for_telegram_totp(timeout=90) -> str:
    """Poll Telegram for a 6-digit reply (manual TOTP input)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 30, "allowed_updates": ["message"]}
    deadline = time.time() + timeout

    # Get current offset
    try:
        r = requests.get(url, params={**params, "timeout": 0}, timeout=5)
        updates = r.json().get("result", [])
        offset = updates[-1]["update_id"] + 1 if updates else 0
    except Exception:
        offset = 0

    log.info("⏳ Waiting for TOTP from Telegram...")
    while time.time() < deadline:
        try:
            r = requests.get(url, params={**params, "offset": offset}, timeout=35)
            updates = r.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {}).get("text", "").strip()
                if msg.isdigit() and len(msg) == 6:
                    log.info(f"📲 TOTP received from Telegram: {msg}")
                    return msg
        except Exception as e:
            log.warning(f"Telegram poll error: {e}")
            time.sleep(2)

    raise TimeoutError("TOTP not received within timeout.")


# ─────────────────────────────────────────────
#  MAIN — Get Token (cached or fresh)
# ─────────────────────────────────────────────
def get_access_token(force_refresh: bool = False) -> str:
    """
    Returns today's access token.
    Uses cached token if already generated today.
    """
    if not force_refresh:
        cached = load_token()
        if cached:
            return cached

    log.info("🔄 No valid token found. Starting fresh login...")
    send_telegram(
        f"🌅 <b>Auto-Login Starting</b>\n"
        f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}\n"
        f"⏳ Please wait..."
    )

    access_token = auto_login()
    save_token(access_token)

    send_telegram(
        f"✅ <b>Login Successful!</b>\n"
        f"🔑 Access Token generated\n"
        f"🤖 Trading bot will start now..."
    )
    return access_token


# ─────────────────────────────────────────────
#  STANDALONE RUN (test karne ke liye)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*50)
    print("  KITE AUTO-LOGIN HELPER")
    print("="*50)

    if not API_SECRET:
        print("\n⚠️  ERROR: API_SECRET blank hai!")
        print("   → Kite Developer Console pe jao")
        print("   → API_SECRET copy karo aur is file me paste karo")
        exit(1)

    if not KITE_USER_ID or not KITE_PASSWORD:
        print("\n⚠️  ERROR: KITE_USER_ID ya KITE_PASSWORD blank hai!")
        exit(1)

    token = get_access_token()
    print(f"\n✅ Access Token: {token[:8]}...{token[-4:]}")
    print(f"📁 Saved to: {TOKEN_FILE}")
