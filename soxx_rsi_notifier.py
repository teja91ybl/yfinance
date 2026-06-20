import yfinance as yf
import pandas as pd
import datetime
import pytz
import telepot
import logging
import time
import socket
import os, sys
import psutil
import json

# ============================
# USER CONFIGURATION
# ============================

CONFIG = {
    "monitor_interval_minutes": 30,
    "market_open_hour": 8,
    "market_close_hour": 15,
    "active_days": [0, 1, 2, 3, 4],
    "rsi_oversold": 35,
    "rsi_buy_zone": 45,
    "rsi_overbought": 70,
    "rsi_peak": 70,
    "tax_rate": 0.22,
}

SETTINGS_FILE = "/home/tejraspberrypi12/soxx_notifier_clean/soxx_user_settings.json"
PID_FILE = "/tmp/soxx_rsi_notifier.pid"

# ============================
# GLOBAL VARIABLES
# ============================

last_closed_day = None
user_avg_cost = None
user_shares = None
last_update_id = 0

# ============================
# SINGLE INSTANCE PROTECTION
# ============================

if os.path.exists(PID_FILE):
    print("[INIT] PID file exists, another instance may be running. Exiting.")
    sys.exit(0)

with open(PID_FILE, "w") as f:
    f.write(str(os.getpid()))

print("[INIT] Starting SOXX RSI Notifier...")

for proc in psutil.process_iter(['pid', 'cmdline']):
    try:
        if proc.info['cmdline'] and 'soxx_rsi_notifier.py' in ' '.join(proc.info['cmdline']) and proc.pid != os.getpid():
            print(f"[INIT] Another instance detected (PID {proc.pid}). Exiting.")
            sys.exit()
    except Exception:
        pass

socket.setdefaulttimeout(5)

logging.basicConfig(
    filename="/home/tejraspberrypi12/yfinance/soxx.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

TOKEN = "8809874320:AAHcUO2bjoAJedB8Z4ryGGilQjOn0IXUVs0"
CHAT_ID = "581156145"
SYMBOL = "SOXX"
TZ = pytz.timezone("America/Chicago")

bot = telepot.Bot(TOKEN)
print("[INIT] Bot username:", bot.getMe()["username"])

# ============================
# SETTINGS
# ============================

def load_settings():
    global user_avg_cost, user_shares, CONFIG
    print("[SETTINGS] Loading settings...")
    if not os.path.exists(SETTINGS_FILE):
        print("[SETTINGS] No settings file found.")
        return
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        user_avg_cost = data.get("user_avg_cost")
        user_shares = data.get("user_shares")
        interval = data.get("monitor_interval_minutes")
        if interval:
            CONFIG["monitor_interval_minutes"] = int(interval)
        print(f"[SETTINGS] Loaded avg={user_avg_cost}, shares={user_shares}, interval={CONFIG['monitor_interval_minutes']}")
    except Exception as e:
        print("[SETTINGS] Error loading:", e)

def save_settings():
    print("[SETTINGS] Saving settings...")
    try:
        data = {
            "user_avg_cost": user_avg_cost,
            "user_shares": user_shares,
            "monitor_interval_minutes": CONFIG["monitor_interval_minutes"],
        }
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f)
        print("[SETTINGS] Saved.")
    except Exception as e:
        print("[SETTINGS] Error saving:", e)

# ============================
# RSI CALCULATION
# ============================

def compute_rsi(series, period=14):
    print(f"[RSI] Computing RSI for {len(series)} candles...")
    try:
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]

        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        avg_gain = avg_gain.combine_first(gain.ewm(alpha=1/period, adjust=False).mean())
        avg_loss = avg_loss.combine_first(loss.ewm(alpha=1/period, adjust=False).mean())

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi.squeeze()
    except Exception as e:
        print("[RSI] Error:", e)
        return None

# ============================
# TELEGRAM SEND
# ============================

def send_message(msg):
    print("[TELEGRAM] Sending...")
    try:
        bot.sendMessage(CHAT_ID, msg)
        print("[TELEGRAM] Sent.")
    except Exception as e:
        print("[TELEGRAM] Error:", e)

# ============================
# PORTFOLIO BLOCK
# ============================

def build_portfolio_block(price):
    global user_avg_cost, user_shares
    if user_avg_cost is None or user_shares is None:
        return ""

    try:
        avg = float(user_avg_cost)
        shares = float(user_shares)

        profit = (price - avg) * shares
        pct_gain = ((price - avg) / avg) * 100 if avg != 0 else 0

        tax_rate = CONFIG["tax_rate"]

        if profit > 0:
            tax = profit * tax_rate
            tax_display = -tax
        else:
            tax = 0
            tax_display = 0

        in_pocket = profit - tax

        block = (
            "\n📊 Portfolio:\n"
            f"Avg Cost: ${avg:.2f}\n"
            f"Shares: {shares:.2f}\n"
            f"P/L: {profit:+.2f}".replace("+", "+$").replace("-", "-$") + f" ({pct_gain:+.2f}%)\n"
            f"Tax ({int(tax_rate*100)}%): {tax_display:+.2f}".replace("+", "+$").replace("-", "-$") + "\n"
            f"In-Pocket: {in_pocket:+.2f}".replace("+", "+$").replace("-", "-$") + "\n"
        )
        return block

    except Exception as e:
        print("[PORTFOLIO] Error:", e)
        return ""

# ============================
# SOXX STATUS (30m)
# ============================

def monitor_soxx():
    now = datetime.datetime.now(TZ)
    print("[MONITOR] Fetching SOXX...")

    try:
        data = yf.download(SYMBOL, period="5d", interval="30m")
        if data.empty:
            send_message("⚠️ No SOXX data available.")
            return

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        rsi = compute_rsi(close)
        if isinstance(rsi, pd.DataFrame):
            rsi = rsi.iloc[:, 0]

        rsi = rsi.dropna()
        rsi_val = float(rsi.iloc[-1])
        price = float(close.iloc[-1])

        if rsi_val < CONFIG["rsi_oversold"]:
            status = "Oversold (Buy Now, Hurry!!)"
        elif rsi_val < CONFIG["rsi_buy_zone"]:
            status = "Buy Zone (Safe to Buy)"
        elif rsi_val > CONFIG["rsi_overbought"]:
            status = "Overbought (Don't Buy, Wait!!)"
        else:
            status = "Neutral"

        if rsi_val >= CONFIG["rsi_peak"]:
            header = f"🚨🔥 SOXX Status — {status} 🔥🚨"
        elif rsi_val < CONFIG["rsi_buy_zone"]:
            header = f"💚 SOXX Status — {status} 💚"
        else:
            header = f"🌤️ SOXX Status — {status}"

        portfolio_block = build_portfolio_block(price)

        msg = (
            f"{header}\n\n"
            f"RSI: {rsi_val:.2f}\n"
            f"Price: ${price:.2f}\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M CST')}\n\n"
            f"{portfolio_block}"
        )

        send_message(msg)

    except Exception as e:
        print("[MONITOR] Error:", e)
        send_message("⚠️ monitor_soxx failed")

# ============================
# WEEKLY INSIGHTS (30m RSI)
# ============================

def send_weekly_insights():
    try:
        data = yf.download(SYMBOL, period="7d", interval="30m")
        if data.empty:
            send_message("⚠️ No data available for weekly insights.")
            return

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        rsi = compute_rsi(close)
        rsi = rsi.dropna()

        best_buy_idx = rsi.idxmin()
        best_buy_rsi = rsi.loc[best_buy_idx]
        best_buy_price = close.loc[best_buy_idx]

        best_sell_idx = rsi.idxmax()
        best_sell_rsi = rsi.loc[best_sell_idx]
        best_sell_price = close.loc[best_sell_idx]

        pl = best_sell_price - best_buy_price
        pl_pct = (pl / best_buy_price) * 100

        msg = (
            "📢 Weekly Insights (SOXX, 30m RSI) 📢\n"
            f"Best Buy : ${best_buy_price:.2f} @ RSI {best_buy_rsi:.2f} on {best_buy_idx.strftime('%d%b %H:%M')}\n"
            f"Best Sell: ${best_sell_price:.2f} @ RSI {best_sell_rsi:.2f} on {best_sell_idx.strftime('%d%b %H:%M')}\n"
            f"P/L : {pl:+.2f}".replace("+", "+$").replace("-", "-$") +
            f" ({pl_pct:+.2f}%)\n"
        )

        send_message(msg)

    except Exception as e:
        print("[WEEKLY] Error:", e)
        send_message("⚠️ Weekly Insights failed")

# ============================
# MONTHLY INSIGHTS (30m RSI)
# ============================

def send_monthly_insights():
    try:
        data = yf.download(SYMBOL, period="1mo", interval="30m")
        if data.empty:
            send_message("⚠️ No data available for monthly insights.")
            return

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        rsi = compute_rsi(close)
        rsi = rsi.dropna()

        best_buy_idx = rsi.idxmin()
        best_buy_rsi = rsi.loc[best_buy_idx]
        best_buy_price = close.loc[best_buy_idx]

        best_sell_idx = rsi.idxmax()
        best_sell_rsi = rsi.loc[best_sell_idx]
        best_sell_price = close.loc[best_sell_idx]

        pl = best_sell_price - best_buy_price
        pl_pct = (pl / best_buy_price) * 100

        msg = (
            "📢 Monthly Insights (SOXX, 30m RSI) 📢\n"
            f"Best Buy : ${best_buy_price:.2f} @ RSI {best_buy_rsi:.2f} on {best_buy_idx.strftime('%d%b %H:%M')}\n"
            f"Best Sell: ${best_sell_price:.2f} @ RSI {best_sell_rsi:.2f} on {best_sell_idx.strftime('%d%b %H:%M')}\n"
            f"P/L : {pl:+.2f}".replace("+", "+$").replace("-", "-$") +
            f" ({pl_pct:+.2f}%)\n"
        )

        send_message(msg)

    except Exception as e:
        print("[MONTHLY] Error:", e)
        send_message("⚠️ Monthly Insights failed")

# ============================
# COMMAND HANDLERS
# ============================
def get_latest_command():
    global last_update_id
    try:
        updates = bot.getUpdates(offset=last_update_id + 1 if last_update_id else None, timeout=10)
        if not updates:
            return None
        last_update_id = updates[-1]["update_id"]
        msg = updates[-1].get("message", {})
        text = msg.get("text", "").lower().strip()
        print("[CMD] Received:", text)
        return text
    except Exception:
        return None


def handle_set_avg(cmd):
    global user_avg_cost, user_shares
    try:
        parts = cmd.replace("add buy average", "").strip()
        avg_str, shares_str = parts.split(",", 1)
        user_avg_cost = float(avg_str.strip())
        user_shares = float(shares_str.strip())
        save_settings()
        send_message(f"📌 Updated.\nAvg: ${user_avg_cost:.4f}\nShares: {user_shares:.4f}")
    except Exception:
        send_message("❌ Use: add buy average 603.75,20.46")

def handle_clear_avg():
    global user_avg_cost, user_shares
    user_avg_cost = None
    user_shares = None
    save_settings()
    send_message("🧹 Cleared average and shares.")

def handle_set_interval(cmd):
    try:
        parts = cmd.split()
        minutes = int(parts[2])
        CONFIG["monitor_interval_minutes"] = minutes
        save_settings()
        send_message(f"⏱️ Interval updated to {minutes} minutes.")
    except Exception:
        send_message("❌ Use: set interval 15")

# ============================
# MAIN LOOP
# ============================

if __name__ == "__main__":
    load_settings()
    last_run_minute = None

    try:
        while True:
            now = datetime.datetime.now(TZ)

            if now.weekday() not in CONFIG["active_days"]:
                time.sleep(300)
                continue

            cmd = get_latest_command()

            if cmd:
                if cmd.startswith("add buy average"):
                    handle_set_avg(cmd)
                elif cmd.startswith("clear average"):
                    handle_clear_avg()
                elif cmd.startswith("set interval"):
                    handle_set_interval(cmd)
                elif "soxx" in cmd and "status" in cmd:
                    monitor_soxx()
                elif "soxx" in cmd and "weekly" in cmd:
                    send_weekly_insights()
                elif "soxx" in cmd and "monthly" in cmd:
                    send_monthly_insights()

            if CONFIG["market_open_hour"] <= now.hour < CONFIG["market_close_hour"]:
                if now.minute % CONFIG["monitor_interval_minutes"] == 0:
                    if last_run_minute != now.minute:
                        monitor_soxx()
                        last_run_minute = now.minute
                else:
                    last_run_minute = None

            time.sleep(20)

    finally:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
