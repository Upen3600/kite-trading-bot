"""
╔══════════════════════════════════════════════════════════════════╗
║   KITE AUTO-LOGIN HELPER                                         ║
║   Works on: Local PC + Railway.app cloud                        ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import time
import subprocess
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
#  FIND CHROME + CHROMEDRIVER PATHS
# ─────────────────────────────────────────────
def find_chrome_paths():
    """
    Railway/Nix pe Chrome alag jagah hota hai.
    'which' command se dhundho.
    """
    chrome_names = ["chromium", "chromium-browser", "google-chrome", "chrome"]
    driver_names = ["chromedriver"]

    chrome_bin = None
    driver_bin = None

    for name in chrome_names:
        try:
            result = subprocess.run(["which", name], capture_output=True, text=True)
            if result.returncode == 0:
                chrome_bin = result.stdout.strip()
                log.info(f"✅ Chrome found: {chrome_bin}")
                break
        except Exception:
            pass

    for name in driver_names:
        try:
            result = subprocess.run(["which", name], capture_output=True, text=True)
            if result.returncode == 0:
                driver_bin = result.stdout.strip()
                log.info(f"✅ ChromeDriver found: {driver_bin}")
                break
        except Exception:
            pass

    # Nix store fallback — find in /nix
    if not chrome_bin:
        try:
            result = subprocess.run(
                ["find", "/nix", "-name", "chromium", "-type", "f"],
                capture_output=True, text=True, timeout=10
            )
            paths = [p for p in result.stdout.strip().split("\n") if p and "bin" in p]
            if paths:
                chrome_bin = paths[0]
                log.info(f"✅ Chrome found in nix store: {chrome_bin}")
        except Exception:
            pass

    if not driver_bin:
        try:
            result = subprocess.run(
                ["find", "/nix", "-name", "chromedriver", "-type", "f"],
                capture_output=True, text=True, timeout=10
            )
            paths = [p for p in result.stdout.strip().split("\n") if p and "bin" in p]
            if paths:
                driver_bin = paths[0]
                log.info(f"✅ ChromeDriver found in nix store: {driver_bin}")
        except Exception:
            pass

    return chrome_bin, driver_bin


def get_driver():
    chrome_opts = Options()
    chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--window-size=1280,800")
    chrome_opts.add_argument("--remote-debugging-port=9222")

    # Railway / Linux
    if os.name != "nt":
        chrome_bin, driver_bin = find_chrome_paths()

        if chrome_bin:
            chrome_opts.binary_location = chrome_bin

        if driver_bin:
            log.info(f"Using ChromeDriver: {driver_bin}")
            return webdriver.Chrome(
                service=Service(driver_bin),
                options=chrome_opts
            )
        else:
            log.warning("ChromeDriver not found via which/nix. Trying webdriver-manager...")

    # Local Windows / fallback
    from webdriver_manager.chrome import ChromeDriverManager
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_opts
    )


# ─────────────────────────────────────────────
#  AUTO LOGIN
# ─────────────────────────────────────────────
def auto_login() -> str:
    log.info("🌐 Starting auto-login...")
    driver = get_driver()
    wait   = WebDriverWait(driver, 30)
    kite   = KiteConnect(api_key=API_KEY)

    try:
        driver.get(kite.login_url())
        time.sleep(2)

        # User ID
        wait.until(EC.presence_of_element_located((By.ID, "userid"))).send_keys(KITE_USER_ID)
        time.sleep(0.4)

        # Password
        driver.find_element(By.ID, "password").send_keys(KITE_PASSWORD)
        time.sleep(0.4)

        # Submit
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        log.info("🔐 Credentials submitted...")
        time.sleep(3)

        # TOTP
        totp_field = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//input[@type='number']")
        ))

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

        totp_field.send_keys(totp_code)
        time.sleep(0.5)

        try:
            driver.find_element(By.XPATH, "//button[@type='submit']").click()
        except Exception:
            pass

        time.sleep(5)
        current_url = driver.current_url

        if "request_token=" not in current_url:
            time.sleep(4)
            current_url = driver.current_url

        if "request_token=" not in current_url:
            raise Exception(f"request_token not found. URL: {current_url}")

        request_token = current_url.split("request_token=")[1].split("&")[0]
        log.info(f"✅ request_token: {request_token[:8]}...")

    except Exception as e:
        log.error(f"Login error: {e}")
        try:
            driver.save_screenshot("/tmp/login_error.png")
        except Exception:
            pass
        send_telegram(f"❌ <b>Auto-Login Failed</b>\n{str(e)[:200]}")
        raise
    finally:
        driver.quit()

    data = kite.generate_session(request_token, api_secret=API_SECRET)
    return data["access_token"]


# ─────────────────────────────────────────────
#  TELEGRAM TOTP LISTENER
# ─────────────────────────────────────────────
def wait_for_telegram_totp(timeout=90) -> str:
    url      = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    deadline = time.time() + timeout
    try:
        r      = requests.get(url, params={"timeout": 0}, timeout=5)
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
                    log.info(f"📲 TOTP received.")
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
