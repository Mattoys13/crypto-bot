import requests
import time
import telebot
import pandas as pd
import numpy as np
from datetime import datetime
import os
from flask import Flask, jsonify, render_template_string
import threading

# === KONFIGURACJA ===
API_KEY = "8330502624:AAEr5TliWy66wQm9EX02OUuGeWoslYjWeUY"
CHAT_ID = "7743162708"
bot = telebot.TeleBot(API_KEY)

PRICE_CHANGE_THRESHOLD = 20
VOLUME_SPIKE_THRESHOLD = 300
SCAN_INTERVAL = 300
API_ERROR_INTERVAL = 1800
MUTE_DEX_ERRORS = True
DAILY_REPORT_HOUR = 20  # godzina raportu dziennego (0-23)

last_api_error_time = 0
signals_list = []
last_sent_events = []
last_daily_report = None

# === FUNKCJE TECHNICZNE ===
def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_ema(prices, period=20):
    return prices.ewm(span=period, adjust=False).mean()

def calculate_bollinger(prices, period=20):
    sma = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper_band = sma + (std * 2)
    lower_band = sma - (std * 2)
    return upper_band, lower_band

def detect_candle_pattern(prices):
    if len(prices) < 2:
        return None
    last = prices.iloc[-1]
    prev = prices.iloc[-2]
    if last['close'] > last['open'] and prev['close'] < prev['open'] and last['close'] > prev['open']:
        return "Bullish Engulfing"
    if last['close'] < last['open'] and prev['close'] > prev['open'] and last['close'] < prev['open']:
        return "Bearish Engulfing"
    return None

def detect_volume_spike(volume_data):
    if len(volume_data) < 2:
        return False
    return ((volume_data[-1] - volume_data[-2]) / volume_data[-2]) * 100 >= VOLUME_SPIKE_THRESHOLD

def send_alert(title, message):
    global signals_list
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    signals_list.append({"time": timestamp, "title": title, "message": message})
    if len(signals_list) > 50:
        signals_list.pop(0)
    bot.send_message(CHAT_ID, f"🔔 *{title}*\n\n{message}", parse_mode="Markdown")

def send_api_error(message_text, mute=False):
    global last_api_error_time
    now = time.time()
    if mute:
        print(f"⚠️ [MUTED] {message_text}")
        return
    if now - last_api_error_time > API_ERROR_INTERVAL:
        bot.send_message(CHAT_ID, f"⚠️ Błąd API: {message_text}")
        last_api_error_time = now

# === RAPORT DZIENNY ===
def generate_daily_report():
    try:
        url = "https://api.binance.com/api/v3/ticker/24hr"
        data = requests.get(url, timeout=10).json()
        usdt_pairs = [c for c in data if c['symbol'].endswith("USDT")]

        # --- TOP GAINERS / LOSERS ---
        sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x['priceChangePercent']), reverse=True)
        top_gainers = sorted_pairs[:5]
        top_losers = sorted_pairs[-5:]

        # --- TOP VOLUME ---
        top_volume = sorted(usdt_pairs, key=lambda x: float(x['quoteVolume']), reverse=True)[:5]

        # --- BTC i ETH ---
        btc = next(x for x in usdt_pairs if x['symbol'] == "BTCUSDT")
        eth = next(x for x in usdt_pairs if x['symbol'] == "ETHUSDT")

        # --- Fear & Greed Index ---
        try:
            fng = requests.get("https://api.alternative.me/fng/", timeout=10).json()
            fng_value = fng["data"][0]["value"]
            fng_class = fng["data"][0]["value_classification"]
        except:
            fng_value, fng_class = "?", "Brak danych"

        # --- Trend rynku (EMA i RSI dla BTC) ---
        klines = requests.get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=100").json()
        df = pd.DataFrame(klines, columns=["time","open","high","low","close","volume","c1","c2","c3","c4","c5","c6"])
        df["close"] = df["close"].astype(float)
        ema20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
        rsi = calculate_rsi(df["close"]).iloc[-1]
        trend = "🐂 Bullish" if ema20 > ema50 and rsi < 70 else "🐻 Bearish" if ema20 < ema50 and rsi > 30 else "⚖ Neutral"

        # --- Raport ---
        msg = f"📊 *RAPORT DZIENNY – {datetime.now().strftime('%d.%m.%Y')}*\n\n"
        msg += f"💰 *BTC:* ${float(btc['lastPrice']):,.2f} | 24h: {btc['priceChangePercent']}%\n"
        msg += f"💰 *ETH:* ${float(eth['lastPrice']):,.2f} | 24h: {eth['priceChangePercent']}%\n"
        msg += f"📈 *Trend rynku:* {trend}\n"
        msg += f"😨 *Fear & Greed Index:* {fng_value} ({fng_class})\n\n"

        msg += "🚀 *TOP GAINERS:*\n"
        for g in top_gainers:
            msg += f"{g['symbol']} | {g['priceChangePercent']}% | Cena: ${float(g['lastPrice']):.4f}\n"

        msg += "\n🔻 *TOP LOSERS:*\n"
        for l in top_losers:
            msg += f"{l['symbol']} | {l['priceChangePercent']}% | Cena: ${float(l['lastPrice']):.4f}\n"

        msg += "\n📊 *TOP VOLUME:*\n"
        for v in top_volume:
            vol = float(v['quoteVolume'])/1_000_000
            msg += f"{v['symbol']} | Vol: ${vol:.2f}M | Cena: ${float(v['lastPrice']):.4f}\n"

        send_alert("Raport dzienny", msg)
    except Exception as e:
        print(f"❌ Błąd raportu dziennego: {e}")

# === ANALIZA BINANCE ===
def scan_binance():
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
    except Exception as e:
        send_api_error(f"Binance API: {e}")
        return

    signals = []
    for coin in data:
        symbol = coin['symbol']
        if not symbol.endswith("USDT"):
            continue
        price_change = float(coin['priceChangePercent'])
        if price_change < PRICE_CHANGE_THRESHOLD:
            continue

        price = float(coin['lastPrice'])
        klines = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=50").json()
        df = pd.DataFrame(klines, columns=["time","open","high","low","close","volume","c1","c2","c3","c4","c5","c6"])
        df["open"] = df["open"].astype(float)
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["volume"] = df["volume"].astype(float)

        rsi = calculate_rsi(df["close"]).iloc[-1]
        ema20 = calculate_ema(df["close"], 20).iloc[-1]
        ema50 = calculate_ema(df["close"], 50).iloc[-1]
        upper_band, lower_band = calculate_bollinger(df["close"])
        candle_pattern = detect_candle_pattern(df[["open","close","high","low"]].tail(2))

        msg = f"💎 {symbol}\n💰 Cena: ${price:.4f}\n📈 Zmiana: {price_change:.2f}%\n📊 RSI: {rsi:.2f}\n📊 EMA20: {ema20:.4f}\n📊 EMA50: {ema50:.4f}\n📊 Bollinger: [{upper_band.iloc[-1]:.4f} / {lower_band.iloc[-1]:.4f}]\n"
        if candle_pattern:
            msg += f"🕯 Formacja: {candle_pattern}\n"

        signals.append(msg)

    if signals:
        send_alert("Sygnały techniczne (CEX Binance)", "\n\n".join(signals))

# === DASHBOARD ===
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <title>Crypto Bot Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; background: #121212; color: #fff; text-align: center; }
        h1 { color: #00e676; }
        table { margin: auto; border-collapse: collapse; width: 80%; background: #1e1e1e; }
        th, td { border: 1px solid #333; padding: 10px; }
        th { background: #00e676; color: black; }
        tr:nth-child(even) { background: #2a2a2a; }
        a { color: #00e676; text-decoration: none; }
    </style>
</head>
<body>
    <h1>📊 Crypto Alert Bot Dashboard</h1>
    <table>
        <tr>
            <th>Czas</th>
            <th>Tytuł</th>
            <th>Wiadomość</th>
        </tr>
        {% for signal in signals %}
        <tr>
            <td>{{ signal.time }}</td>
            <td>{{ signal.title }}</td>
            <td>{{ signal.message | safe }}</td>
        </tr>
        {% endfor %}
    </table>
    <p>🔄 Automatyczne odświeżanie co 60s</p>
    <script>setTimeout(() => location.reload(), 60000);</script>
</body>
</html>
"""

@app.route('/')
def dashboard():
    return render_template_string(HTML_TEMPLATE, signals=signals_list)

def run_flask():
    app.run(host="0.0.0.0", port=8080)

# === GŁÓWNA PĘTLA ===
print("🤖 Bot uruchomiony...")
bot.send_message(CHAT_ID, "✅ Bot został uruchomiony i działa poprawnie!")
threading.Thread(target=run_flask).start()

while True:
    now = datetime.now()

    scan_binance()

    if last_daily_report != now.date() and now.hour == DAILY_REPORT_HOUR:
        generate_daily_report()
        last_daily_report = now.date()

    time.sleep(SCAN_INTERVAL)
