import os, time, logging, requests
import pandas as pd
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = "8885318265:AAEeX5pmUucIkayTc-lIEMTmVbjrwx2MkF0"
TELEGRAM_CHAT_ID = "1479865309"
NEWS_API_KEY     = "9c2611f74bef40d7a46d5c608d61b74c"
CHECK_INTERVAL   = 3600  # 1h

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

# ── DATA FETCH ────────────────────────────────────────────────────────────────
def fetch_ohlcv(coin_id, days=90):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    r = requests.get(url, params={"vs_currency": "usd", "days": days}, timeout=15)
    r.raise_for_status()
    return [c[4] for c in r.json()]

def fetch_fear_greed():
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    d = r.json()["data"][0]
    return int(d["value"]), d["value_classification"]

def fetch_global_market():
    r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
    d = r.json()["data"]
    btc_dom = round(d["market_cap_percentage"]["btc"], 1)
    vol_24h = round(d["total_volume"]["usd"] / 1e9, 1)
    mcap = round(d["total_market_cap"]["usd"] / 1e12, 2)
    return btc_dom, vol_24h, mcap

def fetch_news():
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "crypto OR bitcoin OR fed OR inflation OR war OR market",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 5,
        "apiKey": NEWS_API_KEY,
    }
    r = requests.get(url, params=params, timeout=10)
    articles = r.json().get("articles", [])
    headlines = []
    for a in articles[:4]:
        title = a.get("title", "")
        if title and "[Removed]" not in title:
            # Truncate to 60 chars
            headlines.append(title[:60] + ("..." if len(title) > 60 else ""))
    return headlines

# ── INDICATORS ────────────────────────────────────────────────────────────────
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

# ── SCORING ───────────────────────────────────────────────────────────────────
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

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=10
    )
    return r.ok

# ── EXEC SUMMARY ─────────────────────────────────────────────────────────────
def send_exec_summary(token_results, greed, greed_label, btc_dom, vol_24h, mcap, news):
    now = datetime.now().strftime("%d/%m %H:%M")

    # Macro section
    greed_emoji = "🔴" if greed <= 25 else "🟠" if greed <= 45 else "🟡" if greed <= 60 else "🟢"
    lines = [
        f"📊 *EXEC SUMMARY — {now}*",
        f"",
        f"🌍 *MACRO*",
        f"Fear & Greed : {greed} {greed_emoji} _{greed_label}_",
        f"BTC Dominance : {btc_dom}%",
        f"Volume 24h : ${vol_24h}B",
        f"Market Cap : ${mcap}T",
        f"",
    ]

    # News section
    if news:
        lines.append("📰 *NEWS*")
        for h in news:
            lines.append(f"· {h}")
        lines.append("")

    # Portfolio section
    lines.append("📈 *PORTFOLIO*")
    total_value = 0
    alerts_summary = []

    for symbol, data in token_results.items():
        price = data["price"]
        rsi = data["rsi"]
        macd = data["macd"]
        sell_sc = data["sell_score"]
        buy_sc = data["buy_score"]
        pru = TOKENS[symbol]["pru"]
        mult = price / pru if pru > 0 else 0

        # Status indicator
        if sell_sc >= 3:
            status = "🔴 SELL"
            alerts_summary.append(f"⚠ {symbol} signal SELL {sell_sc}/7")
        elif sell_sc >= 2:
            status = "🟡 T1"
        elif buy_sc >= 4:
            status = "💚 DCA FORT"
            alerts_summary.append(f"✅ {symbol} signal DCA FORT")
        elif buy_sc >= 2:
            status = "🟢 DCA"
        else:
            status = "⚪ HOLD"

        macd_short = {"bullish_cross": "↑x", "bearish_cross": "↓x",
                      "increasing": "↑", "decreasing": "↓", "neutral": "→"}.get(macd, "→")

        lines.append(f"`{symbol:5s}` ${price:.4g} | RSI={rsi:.0f} MACD={macd_short} | {status}")

    lines.append("")

    # Alerts summary
    if alerts_summary:
        lines.append("⚡ *SIGNAUX ACTIFS*")
        for a in alerts_summary:
            lines.append(f"· {a}")
        lines.append("")

    lines.append(f"_Prochaine update dans 1h_")

    send_telegram("\n".join(lines))

# ── MAIN ──────────────────────────────────────────────────────────────────────
def run():
    log.info(f"BOT v3 DÉMARRÉ — {len(TOKENS)} positions | signals + exec summary")
    fired = {}

    while True:
        log.info("─── Nouvelle vérification ───")

        # Fetch macro
        try:
            greed, greed_label = fetch_fear_greed()
            log.info(f"Fear & Greed : {greed} ({greed_label})")
        except Exception as e:
            log.error(f"Fear & Greed error: {e}"); greed, greed_label = 50, "Neutral"

        try:
            btc_dom, vol_24h, mcap = fetch_global_market()
        except Exception as e:
            log.error(f"Global market error: {e}"); btc_dom, vol_24h, mcap = 0, 0, 0

        try:
            news = fetch_news()
        except Exception as e:
            log.error(f"News error: {e}"); news = []

        token_results = {}

        for symbol, cfg in TOKENS.items():
            try:
                closes = fetch_ohlcv(cfg["id"])
                price  = closes[-1]
                rsi    = compute_rsi(closes)
                macd   = compute_macd_state(closes)
                mult   = (price / cfg["pru"]) if cfg["pru"] > 0 else 0

                sell_sc, sell_alerts = compute_sell_score(rsi, macd, greed)
                buy_sc,  buy_alerts  = compute_buy_score(rsi, macd, greed)

                token_results[symbol] = {
                    "price": price, "rsi": rsi, "macd": macd,
                    "sell_score": sell_sc, "buy_score": buy_sc,
                    "mult": mult
                }

                log.info(f"  {symbol:6s} ${price:.4g} RSI={rsi:.0f} SELL={sell_sc} BUY={buy_sc}")

                # Signal SELL
                sell_t = get_tranche(sell_sc, SELL_TRANCHES)
                if sell_t and fired.get(f"{symbol}_sell") != sell_t["level"]:
                    msg = (f"{sell_t['emoji']} *{sell_t['level']} SELL — {symbol}*\n"
                           f"Prix: `${price:.6g}` | PRU: `${cfg['pru']:.6g}`\n"
                           f"Multiple: `x{mult:.2f}`\n"
                           f"Action: Vendre *{sell_t['pct']}%*\n"
                           f"Score: `{sell_sc}/7`\n"
                           + "\n".join(f"· {a}" for a in sell_alerts))
                    send_telegram(msg)
                    fired[f"{symbol}_sell"] = sell_t["level"]
                elif not sell_t:
                    fired.pop(f"{symbol}_sell", None)

                # Signal BUY
                buy_t = get_tranche(buy_sc, BUY_TRANCHES)
                if buy_t and fired.get(f"{symbol}_buy") != buy_t["level"]:
                    msg = (f"{buy_t['emoji']} *{buy_t['level']} — {symbol}*\n"
                           f"Prix: `${price:.6g}` | PRU: `${cfg['pru']:.6g}`\n"
                           f"Multiple: `x{mult:.2f}`\n"
                           f"Action: DCA *{buy_t['pct']}%* du budget\n"
                           f"Score: `{buy_sc}/6`\n"
                           + "\n".join(f"· {a}" for a in buy_alerts))
                    send_telegram(msg)
                    fired[f"{symbol}_buy"] = buy_t["level"]
                elif not buy_t:
                    fired.pop(f"{symbol}_buy", None)

                time.sleep(30)

            except Exception as e:
                log.error(f"  {symbol}: {e}")

        # Envoyer exec summary
        if token_results:
            try:
                send_exec_summary(token_results, greed, greed_label,
                                  btc_dom, vol_24h, mcap, news)
                log.info("Exec summary envoyé")
            except Exception as e:
                log.error(f"Exec summary error: {e}")

        log.info(f"Pause {CHECK_INTERVAL}s")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
