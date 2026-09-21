cat << 'EOF' > us_earnings_monitor.py
import os
import sys
import json
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("[❌ ERROR] TELEGRAM_TOKEN or CHAT_ID is missing from environment variables.")
    sys.exit(1)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
CONFIG_FILE = "watchlist.json"

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[⚠️] Could not load {CONFIG_FILE}: {e}. Using fallback defaults.")
        return {
            "watchlist": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD"],
            "window_min_days": 7,
            "window_max_days": 15
        }

def get_trading_exit_date(earnings_dt):
    """Calculates T-1 trading day (shifts back to Friday if release is on Monday/Sunday)."""
    exit_dt = earnings_dt - timedelta(days=1)
    while exit_dt.weekday() >= 5:  # 5=Saturday, 6=Sunday
        exit_dt -= timedelta(days=1)
    return exit_dt.strftime("%a, %b %d")

def analyze_ticker(ticker, min_days, max_days):
    stock = yf.Ticker(ticker)
    now = pd.Timestamp.now(tz="America/New_York")
    
    # 1. Upcoming Earnings Date & Surprises
    try:
        earnings_df = stock.get_earnings_dates(limit=12)
    except Exception:
        return None

    if earnings_df is None or earnings_df.empty:
        return None

    if earnings_df.index.tz is None:
        earnings_df.index = earnings_df.index.tz_localize("UTC").tz_convert("America/New_York")
    else:
        earnings_df.index = earnings_df.index.tz_convert("America/New_York")

    future_dates = earnings_df[earnings_df.index > now].sort_index()
    past_dates = earnings_df[earnings_df.index <= now].sort_index(ascending=False)

    if future_dates.empty:
        return None

    next_earnings_dt = future_dates.index[0]
    days_left = (next_earnings_dt.date() - now.date()).days

    # Filter strictly for the 7-15 day window
    if not (min_days <= days_left <= max_days):
        return None

    # 2. Historical Beat Rates
    past_3_beats = 0
    if len(past_dates) >= 3 and "Surprise(%)" in past_dates.columns:
        past_3 = past_dates.head(3)
        past_3_beats = int((past_3["Surprise(%)"] > 0).sum())

    yoy_beat = False
    if len(past_dates) >= 4 and "Surprise(%)" in past_dates.columns:
        yoy_row = past_dates.iloc[3]
        if pd.notna(yoy_row["Surprise(%)"]):
            yoy_beat = bool(yoy_row["Surprise(%)"] > 0)

    # 3. Price Momentum & Consecutive Streak
    hist = stock.history(period="2mo")
    if hist.empty or len(hist) < 25:
        return None

    closes = hist["Close"]
    recent_close = closes.iloc[-1]
    sma_20 = closes.rolling(20).mean().iloc[-1]
    above_sma20 = recent_close > sma_20

    diffs = closes.diff().dropna()
    streak_up = 0
    streak_down = 0

    for change in reversed(diffs.tail(8).tolist()):
        if change > 0:
            if streak_down > 0:
                break
            streak_up += 1
        elif change < 0:
            if streak_up > 0:
                break
            streak_down += 1

    # 4. Multi-Factor Scoring Formula
    long_score = streak_up + past_3_beats + (1 if yoy_beat else 0) + (1 if above_sma20 else 0)
    short_score = streak_down + (3 - past_3_beats) + (1 if not yoy_beat else 0) + (1 if not above_sma20 else 0)

    return {
        "ticker": ticker,
        "price": round(float(recent_close), 2),
        "days_left": days_left,
        "earnings_date": next_earnings_dt.strftime("%b %d"),
        "exit_date": get_trading_exit_date(next_earnings_dt.date()),
        "streak_up": streak_up,
        "streak_down": streak_down,
        "above_sma20": above_sma20,
        "past_3_beats": past_3_beats,
        "yoy_beat": yoy_beat,
        "long_score": long_score,
        "short_score": short_score
    }

def send_telegram(html_text):
    payload = {
        "chat_id": CHAT_ID,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(TELEGRAM_API, json=payload, timeout=12)
        if res.status_code != 200:
            print(f"[!] Telegram rejected message: {res.text}")
        else:
            print("[✓] Alert successfully sent to Telegram.")
    except Exception as e:
        print(f"[!] Network error sending to Telegram: {e}")

def main():
    config = load_config()
    watchlist = config.get("watchlist", [])
    min_days = config.get("window_min_days", 7)
    max_days = config.get("window_max_days", 15)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scanning {len(watchlist)} tickers...")
    results = []

    for sym in watchlist:
        try:
            data = analyze_ticker(sym.strip().upper(), min_days, max_days)
            if data:
                results.append(data)
        except Exception as e:
            print(f"[⚠️] Error analyzing {sym}: {e}")

    if not results:
        print("[i] No stocks found reporting within the 7-15 day window today.")
        return

    # Select Top 3-4 Longs and Shorts based on scores
    top_longs = sorted([r for r in results if r["streak_up"] >= 1], key=lambda x: x["long_score"], reverse=True)[:4]
    top_shorts = sorted([r for r in results if r["streak_down"] >= 1], key=lambda x: x["short_score"], reverse=True)[:4]

    if not top_longs and not top_shorts:
        print("[i] No tickers met minimum streak thresholds.")
        return

    # Build Telegram HTML Alert
    lines = [
        "<b>📊 US PRE-EARNINGS MOMENTUM RADAR</b>",
        f"<i>Scan Time: {datetime.now().strftime('%d %b %Y | %H:%M ET')}</i>\n",
        "⚡ <b>Strategy:</b> Ride the pre-announcement momentum. Close positions <b>prior</b> to earnings release to avoid gap risk.\n"
    ]

    if top_longs:
        lines.append("🟢 <b>TOP LONG (BUY) CANDIDATES</b>")
        for item in top_longs:
            sma_tag = "Above SMA20" if item['above_sma20'] else "Below SMA20"
            lines.append(
                f"• <b>${item['ticker']}</b> | Price: ${item['price']}\n"
                f"   Earnings: <b>{item['earnings_date']}</b> (In {item['days_left']}d)\n"
                f"   Momentum: <b>{item['streak_up']}d UP</b> | {sma_tag}\n"
                f"   EPS Beats: <b>{item['past_3_beats']}/3</b> Recent | YoY Beat: <b>{'Yes' if item['yoy_beat'] else 'No'}</b>\n"
                f"   🚨 <b>Mandatory Exit:</b> On/Before <u>{item['exit_date']} (Market Close)</u>\n"
            )

    if top_shorts:
        lines.append("🔴 <b>TOP SHORT (SELL) CANDIDATES</b>")
        for item in top_shorts:
            sma_tag = "Below SMA20" if not item['above_sma20'] else "Above SMA20"
            lines.append(
                f"• <b>${item['ticker']}</b> | Price: ${item['price']}\n"
                f"   Earnings: <b>{item['earnings_date']}</b> (In {item['days_left']}d)\n"
                f"   Momentum: <b>{item['streak_down']}d DOWN</b> | {sma_tag}\n"
                f"   EPS Misses: <b>{3 - item['past_3_beats']}/3</b> Recent | YoY Miss: <b>{'Yes' if not item['yoy_beat'] else 'No'}</b>\n"
                f"   🚨 <b>Mandatory Exit:</b> On/Before <u>{item['exit_date']} (Market Close)</u>\n"
            )

    send_telegram("\n".join(lines))

if __name__ == "__main__":
    main()
EOF
