import requests
import time
import telebot
import pandas as pd
import numpy as np

# === KONFIGURACJA ===
API_KEY = "8330502624:AAEr5TliWy66wQm9EX02OUuGeWoslYjWeUY"
CHAT_ID = "7743162708"
bot = telebot.TeleBot(API_KEY)

PRICE_CHANGE_THRESHOLD = 30   # % zmiany ceny w 15 min
VOLUME_CHANGE_THRESHOLD = 200 # % wzrost wolumenu w 30 min
SCAN_INTERVAL = 300           # co ile sekund skanować (300s = 5 min)

# === FUNKCJE TECHNICZNE ===
def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_macd(prices, short=12, long=26, signal=9):
    ema_short = prices.ewm(span=short, adjust=False).mean()
    ema_long = prices.ewm(span=long, adjust=False).mean()
    macd = ema_short - ema_long
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

# === WYSYŁKA ALERTU ===
def send_alert(coin, price, change, volume, rsi, macd_signal, link):
    message = f"🚀 *ALERT WYBICIA!*\n\n" \
              f"💎 Coin: {coin}\n" \
              f"💰 Cena: ${price:.4f}\n" \
              f"📈 Zmiana: {change:.2f}%\n" \
              f"📊 Wolumen (24h): ${volume/1_000_000:.2f}M\n" \
              f"📉 RSI: {rsi:.2f}\n" \
              f"📈 MACD: {'Bullish' if macd_signal else 'Bearish'}\n\n" \
              f"🔗 Wykres: {link}"
    bot.send_message(CHAT_ID, message, parse_mode="Markdown")

# === ANALIZA CEX (Binance) ===
def scan_binance():
    url = "https://api.binance.com/api/v3/ticker/24hr"
    response = requests.get(url)
    data = response.json()

    for coin in data:
        symbol = coin['symbol']
        if not symbol.endswith("USDT"):
            continue
        price_change = float(coin['priceChangePercent'])
        volume = float(coin['quoteVolume'])
        price = float(coin['lastPrice'])

        # Pobranie świec do RSI/MACD
        klines = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=100").json()
        closes = pd.Series([float(k[4]) for k in klines])
        rsi = calculate_rsi(closes).iloc[-1]
        macd, signal_line = calculate_macd(closes)
        macd_signal = macd.iloc[-1] > signal_line.iloc[-1]

        if price_change >= PRICE_CHANGE_THRESHOLD and rsi < 70 and macd_signal:
            send_alert(symbol.replace("USDT", ""), price, price_change, volume, rsi, macd_signal,
                       f"https://www.tradingview.com/symbols/{symbol}/")

# === ANALIZA DEX (DexScreener) ===
def scan_dex():
    url = "https://api.dexscreener.com/latest/dex/tokens"
    response = requests.get(url).json()
    for pair in response.get("pairs", []):
        price = float(pair["priceUsd"])
        change_15m = float(pair.get("priceChange", {}).get("m15", 0))
        volume_24h = float(pair.get("volume", {}).get("h24", 0))
        token = pair["baseToken"]["symbol"]

        if change_15m >= PRICE_CHANGE_THRESHOLD and volume_24h > 100_000:
            send_alert(token, price, change_15m, volume_24h, 50, True, pair["url"])

print("🤖 Bot uruchomiony. Skanuję rynek CEX i DEX...")

while True:
    scan_binance()
    scan_dex()
    time.sleep(SCAN_INTERVAL)
