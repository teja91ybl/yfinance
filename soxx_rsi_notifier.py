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

PID_FILE = "/tmp/soxx_rsi_notifier.pid"

# Prevent multiple instances
if os.path.exists(PID_FILE):
    print(f"[INIT] Another instance detected. Exiting.")
    sys.exit(0)

with open(PID_FILE, "w") as f:
    f.write(str(os.getpid()))

print("[INIT] Starting SOXX RSI Notifier...")

# ============================
# SINGLE INSTANCE PROTECTION
# ============================
for proc in psutil.process_iter(['pid', 'cmdline']):
    try:
        if proc.info['cmdline'] and 'soxx_rsi_notifier.py' in ' '.join(proc.info['cmdline']) and proc.pid != os.getpid():
            print(f"[INIT] Another instance detected (PID {proc.pid}). Exiting.")
            sys.exit()
    except Exception:
        pass

# ============================
# NETWORK TIMEOUT
# ============================
socket.setdefaulttimeout(5)

# ============================
# LOGGING
# ============================
logging.basicConfig(
    filename="/home/tejraspberrypi12/yfinance/soxx.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ============================
# CONFIGURATION
# ============================
TOKEN = "8809874320:AAHcUO2bjoAJedB8Z4ryGGilQjOn0IXUVs0"
CHAT_ID = "581156145"
SYMBOL = "SOXX"
TZ = pytz.timezone("America/Chicago")

bot = telepot.Bot(TOKEN)
print("[INIT] Bot username:", bot.getMe()["username"])

last_update_id = 0


# ============================
# RSI CALCULATION (EMA-based)
# ============================
def compute_rsi(series, period=14):
    print(f"[RSI] Computing RSI for {len(series)} candles...")
    try:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        print("[RSI] RSI calculation complete.")
        return rsi
    except Exception as e:
        print("[ERROR] RSI calculation failed:", e)
        return None


# ============================
# TELEGRAM SEND
# ============================
def send_message(msg):
    print("[TELEGRAM] Sending message...")
    try:
        bot.sendMessage(CHAT_ID, msg)
        print("✅ Message sent successfully.")
    except Exception as e:
        print("❌ Telegram send error:", e)


# ============================
# READ LATEST TELEGRAM COMMAND
# ============================
def get_latest_command():
    global last_update_id
    print("[TELEGRAM] Checking for new messages...")

    try:
        updates = bot.getUpdates(
            offset=last_update_id + 1 if last_update_id else None,
            timeout=10
        )

        if not updates:
            print("[TELEGRAM] No new messages.")
            return None

        last_update_id = updates[-1]["update_id"]

        message = updates[-1].get("message", {})
        text = message.get("text", "").lower()

        print(f"[TELEGRAM] Received command: {text}")
        return text

    except Exception as e:
        print("[ERROR] Telegram polling failed:", e)
        return None


# ============================
# MORNING PING
# ============================
def morning_ping():
    print("[PING] Sending morning ping...")
    msg = "📢 SOXX Monitoring Started — Market Prep Complete 📢"
    send_message(msg)


# ============================
# EVENING SUMMARY
# ============================
def evening_summary():
    print("[PING] Sending evening summary...")
    msg = "📢 SOXX Monitoring Ended — Market Closed 📢"
    send_message(msg)


# ============================
# WEEKLY INSIGHTS (WITH TIMESTAMPS)
# ============================
def weekly_insights():
    print("[WEEKLY] Generating weekly insights...")
    try:
        data = yf.download(SYMBOL, period="7d", interval="5m")

        if data.empty:
            send_message("📢 Weekly Insights: No data available 📢")
            return

        # Convert to US/Eastern for market hours
        data.index = data.index.tz_convert("US/Eastern")

        # Monday–Friday, 8 AM–4 PM EST
        data = data[
            (data.index.dayofweek <= 4) &
            (data.index.hour >= 8) &
            (data.index.hour <= 16)
        ]

        if data.empty:
            send_message("📢 Weekly Insights: No market-hours data 📢")
            return

        rsi = compute_rsi(data["Close"])
        data["RSI"] = rsi

        best_buy_ts = data["RSI"].idxmin()
        best_buy_row = data.loc[best_buy_ts]
        best_buy_price = round(float(best_buy_row["Close"]), 2)
        best_buy_rsi = round(float(best_buy_row["RSI"]), 2)

        best_sell_ts = data["RSI"].idxmax()
        best_sell_row = data.loc[best_sell_ts]
        best_sell_price = round(float(best_sell_row["Close"]), 2)
        best_sell_rsi = round(float(best_sell_row["RSI"]), 2)

        def fmt(ts):
            return ts.strftime("%d%b %I:%M%p")

        msg = (
            "📢 Weekly Insights (SOXX) 📢\n"
            f"Best Buy : ${best_buy_price} @ RSI {best_buy_rsi} on {fmt(best_buy_ts)}\n"
            f"Best Sell: ${best_sell_price} @ RSI {best_sell_rsi} on {fmt(best_sell_ts)}"
        )
        send_message(msg)

    except Exception as e:
        print("[ERROR] weekly_insights failed:", e)
        logging.exception("Error in weekly_insights(): %s", e)
        send_message("📢 Weekly Insights failed 📢")


# ============================
# RSI TREND (MACRO PHASE)
# ============================
def get_rsi_trend(current_rsi):
    print(f"[TREND] Determining RSI trend for {current_rsi}...")
    if current_rsi <= 30:
        return "🚨🔥 Oversold Dip Phase 🔥🚨"
    elif current_rsi < 45:
        return "💚 Recovery Phase 💚"
    elif current_rsi < 60:
        return "Cooling Phase"
    elif current_rsi >= 70:
        return "🚨🔥 Peak Phase 🔥🚨"
    return "Neutral Phase"


# ============================
# RSI VELOCITY (MICRO MOVEMENT)
# ============================
def get_rsi_velocity(current_rsi, previous_rsi):
    diff = current_rsi - previous_rsi
    print(f"[VELOCITY] RSI diff = {diff}")

    if diff <= -2.0:
        return "📉⬇️ Dropping Fast 📉⬇️"
    elif diff <= -0.5:
        return "📉 Dropping Slowly 📉"
    elif diff < 0.5:
        return "➖ Staying Flat ➖"
    elif diff < 2.0:
        return "📈 Rising Slowly 📈"
    return "📈⬆️ Rising Fast 📈⬆️"


# ============================
# MAIN 30-MIN STATUS
# ============================
def monitor_soxx():
    now = datetime.datetime.now(TZ)
    print("[MONITOR] Fetching SOXX data...")

    try:
        data = yf.download(SYMBOL, period="5d", interval="30m")

        if data.empty:
            print("[MONITOR] No data returned from yfinance.")
            send_message("⚠️ No SOXX data available from yfinance.")
            return

        print(f"[MONITOR] Raw data columns: {list(data.columns)}")
        print(f"[MONITOR] Data type: {type(data)} | Shape: {data.shape}")

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            print("[MONITOR] 'Close' is a DataFrame — extracting first column.")
            close = close.iloc[:, 0]

        print(f"[MONITOR] Close series type: {type(close)} | Length: {len(close)}")

        rsi = compute_rsi(close)
        if isinstance(rsi, pd.DataFrame):
            print("[MONITOR] RSI is a DataFrame — extracting first column.")
            rsi = rsi.iloc[:, 0]

        print(f"[MONITOR] RSI series type: {type(rsi)} | Length: {len(rsi)}")

        rsi_val = round(float(rsi.iloc[-1]), 2)
        prev_rsi = round(float(rsi.iloc[-2]), 2)
        price = round(float(close.iloc[-1]), 2)

        print(f"[MONITOR] Extracted values -> RSI: {rsi_val}, Prev RSI: {prev_rsi}, Price: {price}")

        trend = get_rsi_trend(rsi_val)
        velocity = get_rsi_velocity(rsi_val, prev_rsi)

        # Tag (text) same as before
        if rsi_val < 35:
            tag = "🌈 Oversold (Buy Now, Hurry!!)"
        elif rsi_val < 45:
            tag = "⬇️ Buy Zone (Safe to Buy)"
        elif rsi_val > 70:
            tag = "🔥 Overbought (Don’t Buy, Wait!!)"
        else:
            tag = "Neutral"

        # Dynamic header icons
        if rsi_val <= 30 or rsi_val >= 70:
            header = f"🚨🔥 SOXX Status — {tag} 🔥🚨"
        elif rsi_val < 45:
            header = f"💚 SOXX Status — {tag} 💚"
        elif rsi_val > 60:
            header = f"💔 SOXX Status — {tag} 💔"
        else:
            header = f"🌤️ SOXX Status — {tag}"

        msg = (
            f"{header}\n\n"
            f"RSI Trend: {trend}\n"
            f"RSI Velocity: {velocity}\n\n"
            f"RSI: {rsi_val}\n"
            f"Price: ${price}\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M CST')}"
        )

        print("[MONITOR] Sending SOXX update...")
        send_message(msg)

    except Exception as e:
        print("[ERROR] monitor_soxx failed:", e)
        logging.exception("Error in monitor_soxx(): %s", e)
        send_message("⚠️ monitor_soxx failed")


# ============================
# MAIN ENTRY POINT
# ============================
if __name__ == "__main__":
    print("[MAIN] Entering main loop...")

    last_run_minute = None  # initialize before loop

    while True:
        now = datetime.datetime.now(TZ)
        print("[LOOP] Alive at:", now.strftime("%H:%M:%S"))

        cmd = get_latest_command()
        if cmd and "soxx" in cmd and "status" in cmd:
            print("[LOOP] Triggering monitor_soxx() from Telegram command.")
            monitor_soxx()

        # Scheduled 30‑minute trigger
        if 8 <= now.hour < 15 and now.minute % 30 == 0:
            if last_run_minute != now.minute:
                print("[LOOP] Scheduled monitor_soxx() triggered.")
                monitor_soxx()
                last_run_minute = now.minute

        if now.hour == 8 and now.minute == 0:
            print("[LOOP] Morning ping triggered.")
            morning_ping()

        if now.hour == 15 and now.minute == 0:
            print("[LOOP] Evening summary triggered.")
            evening_summary()
            # Weekly insights on Friday after close
            if now.weekday() == 4:
                print("[LOOP] Weekly insights triggered.")
                weekly_insights()

        print(f"[LOOP] Cycle complete at {now.strftime('%H:%M:%S')} — sleeping for 20 seconds...\n")
        time.sleep(20)

# Cleanup PID file on exit
if os.path.exists(PID_FILE):
    os.remove(PID_FILE)
