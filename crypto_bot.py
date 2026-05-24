import os, time, logging, requests
import pandas as pd
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = "8885318265:AAEeX5pmUucIkayTc-lIEMTmVbjrwx2MkF0"
TELEGRAM_CHAT_ID = "1479865309"
CHECK_INTERVAL   = 3600

TOKENS = {
    "BTC":  {"id": "bitcoin",            "pru": 122119.10},
    "ETH":  {"id": "ethereum",           "pru": 3440.24},
    "SOL":  {"id": "solana",             "pru": 205.74},
    "LINK": {"id": "chainlink",          "pru": 20.14},
    "TAO":  {"id": "bittensor",          "pru": 278.27},
    "FET":  {"id": "fetch-ai",           "pru": 0.9826},
    "ANKR": {"id": "ankr",               "pru": 0.03506},
    "ONDO": {"id": "ondo-finance",       "pru": 1.0896},
    "CKB":  {"id": "nervos-network",     "pru": 0.01097},
    "SUI":  {"id": "sui",                "pru": 3.737},
    "INJ":  {"id": "injective-protocol", "pru": 15.49},
}

SELL_TRANCHES = [
    {"level": "T3", "pct": 30, "min_score": 5, "emoji": "🔴"},
    {"level": "T2", "pct": 30, "min_score": 3, "emoji": "🟠"},
    {"level": "T1", "pct": 20, "min_score": 2, "emoji": "🟡"},
]

BUY_TRANCHES = [
    {"level": "DCA FORT", "pct": 15, "min_score": 4, "emoji": "💚"},
    {"level": "DCA",      "pct": 10, "min_score": 2, "emoji": "🟢"},
]

def fetch_ohlcv(coin_id, days=90):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    r = requests.get(url, params={"vs_currency": "usd", "days": days}, timeout=15)
    r.raise_for_status()
    return [c[4] for c in r.json()]

def fetch_fear_greed():
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    return int(r.json()["data"][0]["value"])

def compute_rsi(closes, period=14):
    s = pd.Series(closes)
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-9)
    return float(100 - (100 / (1 + rs.iloc[-1])))

def compute_macd_state(closes):
    s = pd.Series(closes)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]:
        return "bullish_cross"
    if macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2]:
        return "bearish_cross"
    if hist.iloc[-1] > hist.iloc[-2]:
        return "increasing"
    if hist.iloc[-1] < hist.iloc[-2]:
        return "decreasing"
    return "neutral"

def compute_sell_score(rsi, macd, greed):
    score, alerts = 0, []
    if rsi >= 85:      score += 3; alerts.append(f"RSI extrême ({rsi:.1f})")
    elif rsi >= 78:    score += 2; alerts.append(f"RSI fort ({rsi:.1f})")
    elif rsi >= 70:    score += 1; alerts.append(f"RSI alerte ({rsi:.1f})")
    if macd == "bearish_cross":  score += 2; alerts.append("MACD cross baissier")
    elif macd == "decreasing":   score += 1; alerts.append("MACD décroît")
    if greed >= 85:    score += 2; alerts.append(f"Greed extrême ({greed})")
    elif greed >= 75:  score += 1; alerts.append(f"Greed élevé ({greed})")
    return score, alerts

def compute_buy_score(rsi, macd, greed):
    score, alerts = 0, []
    if rsi <= 20:      score += 2; alerts.append(f"RSI survente extrême ({rsi:.1f})")
    elif rsi <= 30:    score += 1; alerts.append(f"RSI survente ({rsi:.1f})")
    if macd == "bullish_cross":  score += 2; alerts.append("MACD cross haussier")
    elif macd == "increasing":   score += 1; alerts.append("MACD remonte")
    if greed <= 15:    score += 2; alerts.append(f"Fear extrême ({greed})")
    elif greed <= 25:  score += 1; alerts.append(f"Fear élevé ({greed})")
    return score, alerts

def get_tranche(score, tranches):
    for t in tranches:
        if score >= t["min_score"]:
            return t
    return None

def send_telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=10
    )
    return r.ok

def run():
    log.info(f"BOT v2 DÉMARRÉ — {len(TOKENS)} positions | SELL + BUY signals")
    fired = {}

    while True:
        try:
            greed = fetch_fear_greed()
            log.info(f"Fear & Greed : {greed}")
        except:
            greed = 50

        for symbol, cfg in TOKENS.items():
            try:
                closes = fetch_ohlcv(cfg["id"])
                price  = closes[-1]
                rsi    = compute_rsi(closes)
                macd   = compute_macd_state(closes)
                mult   = (price / cfg["pru"]) if cfg["pru"] > 0 else 0

                # SELL
                sell_sc, sell_alerts = compute_sell_score(rsi, macd, greed)
                sell_t = get_tranche(sell_sc, SELL_TRANCHES)

                # BUY
                buy_sc, buy_alerts = compute_buy_score(rsi, macd, greed)
                buy_t = get_tranche(buy_sc, BUY_TRANCHES)

                log.info(f"  {symbol:6s} RSI={rsi:.0f} MACD={macd:14s} SELL={sell_sc} BUY={buy_sc}")

                # Envoyer signal SELL
                if sell_t and fired.get(f"{symbol}_sell") != sell_t["level"]:
                    msg = (f"{sell_t['emoji']} *{sell_t['level']} SELL — {symbol}*\n"
                           f"Prix: `${price:.6g}`\n"
                           f"Multiple PRU: `x{mult:.2f}`\n"
                           f"Action: Vendre *{sell_t['pct']}%* de la position\n"
                           f"Score: `{sell_sc}/7`\n"
                           + "\n".join(f"· {a}" for a in sell_alerts))
                    send_telegram(msg)
                    fired[f"{symbol}_sell"] = sell_t["level"]
                elif not sell_t:
                    fired.pop(f"{symbol}_sell", None)

                # Envoyer signal BUY
                if buy_t and fired.get(f"{symbol}_buy") != buy_t["level"]:
                    msg = (f"{buy_t['emoji']} *{buy_t['level']} — {symbol}*\n"
                           f"Prix: `${price:.6g}`\n"
                           f"Multiple PRU: `x{mult:.2f}`\n"
                           f"Action: DCA *{buy_t['pct']}%* du budget alloué\n"
                           f"Score: `{buy_sc}/6`\n"
                           + "\n".join(f"· {a}" for a in buy_alerts))
                    send_telegram(msg)
                    fired[f"{symbol}_buy"] = buy_t["level"]
                elif not buy_t:
                    fired.pop(f"{symbol}_buy", None)

                time.sleep(30)

            except Exception as e:
                log.error(f"  {symbol}: {e}")

        log.info(f"Pause {CHECK_INTERVAL}s")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
