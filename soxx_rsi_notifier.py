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
# FIDELITY-STYLE RSI (WILDER)
# ============================
def compute_rsi(series, period=14):
    print(f"[RSI] Computing Fidelity-style RSI for {len(series)} candles...")

    try:
        delta = series.diff()

        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        # Wilder seed (SMA)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        # Wilder smoothing (EMA with alpha=1/period)
        avg_gain = avg_gain.combine_first(
            gain.ewm(alpha=1/period, adjust=False).mean()
        )
        avg_loss = avg_loss.combine_first(
            loss.ewm(alpha=1/period, adjust=False).mean()
        )

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        print("[RSI] Fidelity-style RSI calculation complete.")
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
# WEEKLY INSIGHTS (FULL DEBUG)
# ============================
def weekly_insights():
    print("\n================ WEEKLY INSIGHTS (1D RSI) ================")
    print("[WEEKLY] Starting Weekly Insights (1D)...")

    try:
        # 1) Fetch 3 months of daily candles
        data = yf.download(SYMBOL, period="3mo", interval="1d")

        if data is None or len(data) == 0:
            raise Exception("No data returned from yfinance")

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        # 2) Compute RSI on daily closes
        rsi_series = compute_rsi(data["Close"])
        data["RSI"] = rsi_series
        data = data.dropna(subset=["RSI"])

        # 3) Only last 7 trading days
        last_week = data.tail(7)

        best_buy_ts = last_week["RSI"].idxmin()
        best_sell_ts = last_week["RSI"].idxmax()

        best_buy_row = last_week.loc[best_buy_ts]
        best_sell_row = last_week.loc[best_sell_ts]

        best_buy_price = round(float(best_buy_row["Close"]), 2)
        best_sell_price = round(float(best_sell_row["Close"]), 2)

        best_buy_rsi = round(float(best_buy_row["RSI"]), 2)
        best_sell_rsi = round(float(best_sell_row["RSI"]), 2)

        def fmt(ts):
            return ts.strftime("%d%b")

        msg = (
            "📢 Weekly Insights (SOXX, Daily RSI) 📢\n"
            f"Best Buy : ${best_buy_price} @ RSI {best_buy_rsi} on {fmt(best_buy_ts)}\n"
            f"Best Sell: ${best_sell_price} @ RSI {best_sell_rsi} on {fmt(best_sell_ts)}"
        )

        send_message(msg)

    except Exception as e:
        send_message(f"📢 Weekly Insights failed: {str(e)} 📢")

    print("================ END WEEKLY INSIGHTS (1D RSI) ================\n")


# ============================
# RSI TREND (MACRO)
# ============================
def get_rsi_trend(current_rsi):
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
# RSI VELOCITY (MICRO)
# ============================
def get_rsi_velocity(current_rsi, previous_rsi):
    diff = current_rsi - previous_rsi

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
            send_message("⚠️ No SOXX data available from yfinance.")
            return

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        rsi = compute_rsi(close)
        if isinstance(rsi, pd.DataFrame):
            rsi = rsi.iloc[:, 0]

        rsi_val = round(float(rsi.iloc[-1]), 2)
        prev_rsi = round(float(rsi.iloc[-2]), 2)
        price = round(float(close.iloc[-1]), 2)

        trend = get_rsi_trend(rsi_val)
        velocity = get_rsi_velocity(rsi_val, prev_rsi)

        if rsi_val < 35:
            tag = "🌈 Oversold (Buy Now, Hurry!!)"
        elif rsi_val < 45:
            tag = "⬇️ Buy Zone (Safe to Buy)"
        elif rsi_val > 70:
            tag = "🔥 Overbought (Don’t Buy, Wait!!)"
        else:
            tag = "Neutral"

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

        send_message(msg)

    except Exception as e:
        logging.exception("Error in monitor_soxx(): %s", e)
        send_message("⚠️ monitor_soxx failed")


# ============================
# MAIN LOOP
# ============================
if __name__ == "__main__":
    print("[MAIN] Entering main loop...")

    last_run_minute = None

    while True:
        now = datetime.datetime.now(TZ)
        print("[LOOP] Alive at:", now.strftime("%H:%M:%S"))

        cmd = get_latest_command()

        if cmd and "soxx" in cmd and "status" in cmd:
            monitor_soxx()

        if cmd and "soxx" in cmd and "weekly" in cmd:
            weekly_insights()

        if 8 <= now.hour < 15 and now.minute % 30 == 0:
            if last_run_minute != now.minute:
                monitor_soxx()
                last_run_minute = now.minute

        if now.hour == 8 and now.minute == 0:
            morning_ping()

        if now.hour == 15 and now.minute == 0:
            evening_summary()
            if now.weekday() == 4:
                weekly_insights()

        time.sleep(20)

# Cleanup PID file
if os.path.exists(PID_FILE):
    os.remove(PID_FILE)
