import requests
import time
import telebot
import pandas as pd
import numpy as np
from datetime import datetime

# === KONFIGURACJA ===
API_KEY = "8330502624:AAEr5TliWy66wQm9EX02OUuGeWoslYjWeUY"
CHAT_ID = "7743162708"
bot = telebot.TeleBot(API_KEY)

PRICE_CHANGE_THRESHOLD = 20   # % zmiany ceny w 15 min
VOLUME_SPIKE_THRESHOLD = 300  # % wzrost wolumenu w 15 min
SCAN_INTERVAL = 300           # skanowanie co 5 min
API_ERROR_INTERVAL = 1800     # powiadomienie o błędzie API co 30 min max

last_api_error_time = 0  # kontrola powiadomień o błędach API

# === FUNKCJE TECHNICZNE ===
def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_ema(prices, period=20):
    return prices.ewm(span=period, adjust=False).mean()

def calculate_macd(prices, short=12, long=26, signal=9):
    ema_short = prices.ewm(span=short, adjust=False).mean()
    ema_long = prices.ewm(span=long, adjust=False).mean()
    macd = ema_short - ema_long
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

# === WYSYŁKA ALERTU ===
def send_alert(title, message):
    bot.send_message(CHAT_ID, f"🔔 *{title}*\n\n{message}", parse_mode="Markdown")

# === OGRANICZENIE POWIADOMIEŃ O BŁĘDACH API ===
def send_api_error(message_text):
    global last_api_error_time
    now = time.time()
    if now - last_api_error_time > API_ERROR_INTERVAL:
        bot.send_message(CHAT_ID, f"⚠️ Błąd API: {message_text}")
        last_api_error_time = now

# === ALERT WOLUMENU ===
def detect_volume_spike(volume_data):
    if len(volume_data) < 2:
        return False
    last_vol = volume_data[-1]
    prev_vol = volume_data[-2]
    if prev_vol > 0 and ((last_vol - prev_vol) / prev_vol) * 100 >= VOLUME_SPIKE_THRESHOLD:
        return True
    return False

# === COINMARKETCAL: SPRAWDZANIE EVENTÓW ===
def fetch_coinmarketcal_events():
    url = "https://developers.coinmarketcal.com/v1/events"
   import os
headers = {"x-api-key": os.getenv("CMC_API_KEY")}
  # Wymaga prawdziwego klucza API przy produkcji
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            events = []
            for ev in data.get("body", []):
                events.append(f"{ev['title']} - {ev['coins'][0]['symbol']} ({ev['date']})")
            if events:
                send_alert("Nowe wydarzenia (CoinMarketCal):", "\n".join(events[:5]))
    except Exception as e:
        print("❌ CoinMarketCal API error:", e)

# === ANALIZA CEX (Binance) ===
def scan_binance():
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
    except Exception as e:
        print("❌ Binance API error:", e)
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

        volume = float(coin['quoteVolume'])
        price = float(coin['lastPrice'])

        klines = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=100").json()
        closes = pd.Series([float(k[4]) for k in klines])
        volumes = [float(k[5]) for k in klines]

        rsi = calculate_rsi(closes).iloc[-1]
        ema20 = calculate_ema(closes, 20).iloc[-1]
        ema50 = calculate_ema(closes, 50).iloc[-1]
        macd, signal_line = calculate_macd(closes)
        macd_signal = macd.iloc[-1] > signal_line.iloc[-1]

        # Wykrycie wolumenu
        vol_spike = detect_volume_spike(volumes)

        if rsi < 70 and macd_signal:
            message = f"💎 {symbol}\n💰 Cena: ${price:.4f}\n📈 Zmiana: {price_change:.2f}%\n📊 RSI: {rsi:.2f}\n📊 EMA20: {ema20:.4f}\n📊 EMA50: {ema50:.4f}\n"
            if vol_spike:
                message += "🔥 Nagły wzrost wolumenu!\n"
            message += f"🔗 [Wykres](https://www.tradingview.com/symbols/{symbol})"
            signals.append(message)

    if signals:
        send_alert("Wybicia (CEX Binance)", "\n\n".join(signals))

# === ANALIZA DEX (DexScreener) ===
def scan_dex():
    url = "https://api.dexscreener.com/latest/dex/tokens"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            send_api_error(f"DexScreener HTTP {response.status_code}")
            return
        data = response.json()
    except Exception as e:
        print("❌ DexScreener API error:", e)
        send_api_error(f"DexScreener API: {e}")
        return

    signals = []
    for pair in data.get("pairs", []):
        price = float(pair["priceUsd"])
        change_15m = float(pair.get("priceChange", {}).get("m15", 0))
        volume_24h = float(pair.get("volume", {}).get("h24", 0))
        token = pair["baseToken"]["symbol"]

        if change_15m >= PRICE_CHANGE_THRESHOLD and volume_24h > 100_000:
            signals.append(f"💎 {token}\n💰 Cena: ${price:.4f}\n📈 Zmiana: {change_15m:.2f}%\n🔗 [Wykres]({pair['url']})")

    if signals:
        send_alert("Wybicia (DEX)", "\n\n".join(signals))

# === GŁÓWNA PĘTLA ===
print("🤖 Bot uruchomiony. Skanuję rynek CEX, DEX i eventy CoinMarketCal...")
bot.send_message(CHAT_ID, "✅ Bot został uruchomiony i działa poprawnie!")

while True:
    scan_binance()
    scan_dex()
    fetch_coinmarketcal_events()
    time.sleep(SCAN_INTERVAL)

