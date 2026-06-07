import yfinance as yf
import pandas as pd
import datetime
import pytz
import telepot
import logging

# Configure logging
logging.basicConfig(
    filename="/home/tejraspberrypi12/yfinance/soxx.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ============================
# CONFIGURATION (EDIT THESE)
# ============================
TOKEN = "8809874320:AAHcUO2bjoAJedB8Z4ryGGilQjOn0IXUVs0"
CHAT_ID = "581156145"
SYMBOL = "SOXX"
TZ = pytz.timezone("America/Chicago")


bot = telepot.Bot(TOKEN)
print("Bot username:", bot.getMe()["username"])

# ============================
# RSI CALCULATION (EMA-based)
# ============================
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# ============================
# TELEGRAM SEND
# ============================
def send_message(msg):
    try:
        bot.sendMessage(CHAT_ID, msg)
        print("✅ Message sent successfully.")
    except Exception as e:
        print("❌ Telegram send error:", e)



# ============================
# READ LATEST TELEGRAM COMMAND
# ============================
def get_latest_command():
    updates = bot.getUpdates()
    print("DEBUG RAW UPDATES:", updates)  # <--- ADD THIS
    if not updates:
        return None

    last = updates[-1]
    last_id = last["update_id"]

    # Acknowledge all updates so Telegram clears them
    bot.getUpdates(offset=last_id + 1)

    if "message" in last and "text" in last["message"]:
        text = last["message"]["text"].strip().lower()
        print("Received command:", text)  # Debug line
        return text
    return None


# ============================
# RSI TREND
# ============================
def get_rsi_trend(current_rsi, previous_rsi):
    if hasattr(current_rsi, "iloc"):
        current_rsi = float(current_rsi.iloc[-1].item())
    if hasattr(previous_rsi, "iloc"):
        previous_rsi = float(previous_rsi.iloc[-1].item())

    diff = current_rsi - previous_rsi
    if diff > 0.2:
        trend_icon, trend_label = "📈", "Rising"
    elif diff < -0.2:
        trend_icon, trend_label = "📉", "Falling"
    else:
        trend_icon, trend_label = "➖", "Flat"

    if current_rsi < 35:
        phase = "🌊 Dip Phase"
    elif 35 <= current_rsi < 45:
        phase = "🌅 Recovery Phase"
    elif 45 <= current_rsi < 60:
        phase = "🌤️ Cooling Phase"
    elif current_rsi >= 70:
        phase = "🔥 Peak Phase"
    else:
        phase = "Neutral Phase"

    result = f"{trend_icon} {trend_label}, {phase}"
    logging.info("RSI trend result: %s (current=%.2f, previous=%.2f)", result, current_rsi, previous_rsi)
    return result



# ============================
# MAIN 30-MIN STATUS
# ============================
def monitor_soxx():
    now = datetime.datetime.now(TZ)
    logging.info("Starting monitor_soxx() at %s", now)

    try:
        # Download data
        data = yf.download(SYMBOL, period="5d", interval="30m")
        logging.info("Downloaded SOXX data: %d rows", len(data))

        if data.empty:
            send_message("⚠️ No SOXX data available from yfinance.")
            logging.warning("No data returned from yfinance.")
            return

        close = data["Close"]
        rsi = compute_rsi(close)

        # --- PRICE (clean float, no dtype leak) ---
        price_raw = close.iloc[-1]
        if hasattr(price_raw, "item"):
            price_raw = price_raw.item()
        price = round(float(price_raw), 2)

        # --- RSI (clean float, rounded) ---
        rsi_raw = rsi.iloc[-1]
        if hasattr(rsi_raw, "item"):
            rsi_raw = rsi_raw.item()
        rsi_val = round(float(rsi_raw), 2)

        # Previous RSI
        rsi_prev_raw = rsi.iloc[-2]
        if hasattr(rsi_prev_raw, "item"):
            rsi_prev_raw = rsi_prev_raw.item()
        prev_rsi = round(float(rsi_prev_raw), 2)

        # Trend + Phase
        trend = get_rsi_trend(rsi_val, prev_rsi)
        logging.info("RSI trend computed: %s", trend)

        # --- SOXX STATUS HEADER (unchanged logic) ---
        if rsi_val < 35:
            tag = "🌈 Oversold (Buy Now, Hurry!!)"
        elif rsi_val < 45:
            tag = "⬇️ Buy Zone (Safe to Buy)"
        elif rsi_val > 70:
            tag = "🔥 Overbought (Don’t Buy, Wait!!)"
        else:
            tag = "Neutral"

        header = f"🌤️ SOXX Status — {tag}"

        # --- FINAL MESSAGE ---
        msg = (
            f"{header}\n"
            f"RSI Trend: {trend}\n"
            f"Price: ${price}\n"
            f"RSI: {rsi_val}\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M CST')}"
        )

        send_message(msg)
        logging.info("Message sent successfully: %s", msg)

    except Exception as e:
        logging.exception("Error in monitor_soxx(): %s", e)


# ============================
# WEEKLY SUMMARY
# ============================
def weekly_summary(data, rsi):
    low_price = round(data["Low"].min(), 2)
    high_price = round(data["High"].max(), 2)
    closing_price = round(data["Close"].iloc[-1], 2)
    closing_rsi = round(rsi.iloc[-1], 2)
    now = datetime.datetime.now(TZ)

    msg = (
        "📅 Weekly Summary (SOXX)\n"
        f"Low Price: ${low_price}\n"
        f"High Price: ${high_price}\n"
        f"Closing Price: ${closing_price}\n"
        f"Closing RSI: {closing_rsi}\n"
        f"Time: {now.strftime('%I:%M %p CST')}"
    )
    send_message(msg)

# ============================
# MAIN ENTRY POINT
# ============================
if __name__ == "__main__":
    now = datetime.datetime.now(TZ)

    cmd = get_latest_command()
    print("Command read:", cmd)

    if cmd and "soxx" in cmd and "status" in cmd:
        print("Triggering monitor_soxx()...")
        monitor_soxx()
        #send_message("✅ Command processed successfully.")
    else:
        print("No command detected.")

    # Cron-based automation
    if now.hour == 8 and now.minute == 30:
        morning_ping()
    elif 9 <= now.hour < 15:
        monitor_soxx()
    elif now.hour == 15 and now.minute == 0:
        evening_summary()

