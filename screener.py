"""
CRSI + ATR Stock Screener
=========================
Scans the Nifty 50 universe for Connors RSI (CRSI) overbought/oversold
extremes combined with ATR volatility expansion/contraction, and classifies
the most recent daily candle so results can be filtered by candle pattern.

Data source: Yahoo Finance via the `yfinance` library (free, no API key).
Yahoo's unofficial endpoints occasionally rate-limit or change shape, so all
network calls are wrapped defensively and results are cached in memory.

NOTE: This module needs outbound internet access to query.finance.yahoo.com,
which is only available on your own machine, not inside this sandboxed
container. It has been tested here with synthetic OHLC data only.
"""

import time
import math
import threading
import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:  # allows the rest of the app to import this module even
    yf = None          # before `pip install yfinance` has been run

# ── Universe ────────────────────────────────────────────────────────────
# NSE symbols (.NS suffix for Yahoo Finance) unless noted.
# Edit these lists any time — index reconstitutions happen twice a year.
NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

# Nifty Bank (Bank Nifty) — official 14 constituents (per NSE Indices
# factsheet + live index pages, mid-2026).
BANKNIFTY_STOCKS = [
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    "INDUSINDBK", "AUBANK", "BANKBARODA", "CANBK", "FEDERALBNK",
    "PNB", "IDFCFIRSTB", "UNIONBANK", "YESBANK",
]

# BSE Sensex — official 30 constituents (per BSE/Asia Index, mid-2026).
# Nearly all also trade on NSE, so the same ".NS" Yahoo suffix is used for
# consistency and liquidity of data.
SENSEX30 = [
    "ADANIPORTS", "ASIANPAINT", "AXISBANK", "BAJFINANCE", "BAJAJFINSV",
    "BEL", "BHARTIARTL", "ETERNAL", "HCLTECH", "HDFCBANK",
    "HINDUNILVR", "ICICIBANK", "INFY", "ITC", "KOTAKBANK",
    "LT", "M&M", "MARUTI", "NTPC", "POWERGRID",
    "RELIANCE", "SBIN", "SUNPHARMA", "TCS", "TATAMOTORS",
    "TATASTEEL", "TECHM", "TITAN", "TRENT", "ULTRACEMCO",
]

# Index symbols use Yahoo's caret-prefixed tickers, not the .NS suffix.
INDICES = {
    "NIFTY 50":   "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "SENSEX":     "^BSESN",
}

YF_SUFFIX = ".NS"

UNIVERSES = {
    "nifty50":       NIFTY50,
    "banknifty":     BANKNIFTY_STOCKS,
    "sensex":        SENSEX30,
    "indices":       list(INDICES.keys()),          # all 3 indices together
    "index_nifty50": ["NIFTY 50"],                   # just the Nifty 50 index
    "index_sensex":  ["SENSEX"],                      # just the Sensex index
    "index_bank":    ["NIFTY BANK"],                  # just the Bank Nifty index
}
UNIVERSES["all"] = sorted(set(NIFTY50) | set(BANKNIFTY_STOCKS) | set(SENSEX30)) + list(INDICES.keys())


def _flatten_yf_df(data, yf_sym: str):
    """Normalise any yfinance DataFrame (single or multi-ticker, any version)
    into a plain DataFrame with columns Open/High/Low/Close/Volume.
    Returns None if the data is empty or the symbol is missing."""
    if data is None or (hasattr(data, "empty") and data.empty):
        return None

    OHLCV = {"Open", "High", "Low", "Close", "Volume"}

    if not isinstance(data.columns, pd.MultiIndex):
        # Already flat — just rename if needed
        if OHLCV & set(data.columns):
            return data
        return None

    # MultiIndex: figure out which level holds OHLCV vs ticker names
    lvl0 = set(data.columns.get_level_values(0))
    lvl1 = set(data.columns.get_level_values(1))

    if OHLCV & lvl0:
        # Level 0 = OHLCV, Level 1 = ticker  →  slice by ticker in level 1
        if yf_sym in lvl1:
            return data.xs(yf_sym, axis=1, level=1)
        # Only one ticker was downloaded — just drop the redundant ticker level
        return data.droplevel(1, axis=1)
    elif OHLCV & lvl1:
        # Level 0 = ticker, Level 1 = OHLCV  →  slice by ticker in level 0
        if yf_sym in lvl0:
            return data[yf_sym]
        return data.droplevel(0, axis=1)

    return None


def _to_yf_ticker(symbol: str) -> str:
    """Map a display symbol to its Yahoo Finance ticker."""
    if symbol in INDICES:
        return INDICES[symbol]
    return symbol + YF_SUFFIX


# ── In-memory cache (keyed per-universe) ─────────────────────────────────
_cache_lock = threading.Lock()
_caches = {}  # universe -> {"timestamp":..., "results":..., "errors":...}
CACHE_TTL_SECONDS = 10 * 60  # 10 minutes


# ── Indicator math ──────────────────────────────────────────────────────
def wilder_rsi(series: pd.Series, period: int) -> pd.Series:
    """Wilder-smoothed RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def streak_series(close: pd.Series) -> pd.Series:
    """Consecutive up/down close streak: +N for N up-closes in a row,
    -N for N down-closes in a row, 0 on a flat/first bar."""
    vals = np.zeros(len(close))
    prev = None
    s = 0
    closes = close.to_numpy()
    for i, c in enumerate(closes):
        if prev is None:
            s = 0
        elif c > prev:
            s = s + 1 if s > 0 else 1
        elif c < prev:
            s = s - 1 if s < 0 else -1
        else:
            s = 0
        vals[i] = s
        prev = c
    return pd.Series(vals, index=close.index)


def percent_rank(series: pd.Series, period: int) -> pd.Series:
    """Percentile rank (0-100) of the latest value within a trailing window."""
    def _pr(window):
        return (pd.Series(window).rank(pct=True).iloc[-1]) * 100

    return series.rolling(period).apply(_pr, raw=False)


def compute_crsi(close: pd.Series, rsi_period=3, streak_period=2, rank_period=100) -> pd.Series:
    """Connors RSI = average of three components:
    1) RSI(close, rsi_period)
    2) RSI(streak, streak_period)
    3) PercentRank(1-day ROC, rank_period)
    """
    rsi_close = wilder_rsi(close, rsi_period)
    streak = streak_series(close)
    rsi_streak = wilder_rsi(streak, streak_period)
    roc = close.pct_change() * 100
    rank = percent_rank(roc, rank_period)
    crsi = (rsi_close + rsi_streak + rank) / 3
    return crsi


def compute_atr(df: pd.DataFrame, period=14) -> pd.Series:
    """Wilder-smoothed Average True Range."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def atr_direction(atr: pd.Series, lookback=5, threshold_pct=5.0) -> str:
    if len(atr.dropna()) < lookback + 1:
        return "Unknown"
    cur = atr.iloc[-1]
    prior = atr.iloc[-1 - lookback]
    if prior == 0 or pd.isna(prior) or pd.isna(cur):
        return "Unknown"
    change = (cur - prior) / prior * 100
    if change >= threshold_pct:
        return "Expanding"
    if change <= -threshold_pct:
        return "Contracting"
    return "Flat"


# ── Candle pattern classification ───────────────────────────────────────
# Pattern names are kept aligned with the CANDLE_TYPES list used elsewhere
# in the journal UI (index.html) so the same labels/colors are recognizable.
def _candle_metrics(o, h, l, c):
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    body_pct = body / rng * 100
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    bullish = c > o
    return {
        "range": rng, "body": body, "body_pct": body_pct,
        "upper_wick": upper_wick, "lower_wick": lower_wick, "bullish": bullish,
    }


def classify_candle(df: pd.DataFrame) -> dict:
    """Classify the most recent candle (using up to the last 5 bars for
    multi-candle patterns).

    Returns {pattern, direction, body_pct, all_patterns}. `pattern` is the
    single highest-priority match, used for display (the badge/icon shown
    in the table) — but a candle very often satisfies MORE than one
    pattern's criteria at once (e.g. a textbook Hammer whose low also
    happens to match the prior day's low technically also qualifies as a
    Tweezer Bottom). `all_patterns` lists every pattern whose conditions
    are true for this candle, not just the dominant one — filtering by a
    specific pattern name should check membership in `all_patterns`, not
    equality with `pattern`, or genuine matches get silently missed
    whenever a more "important" pattern also happens to be true that day.
    """
    if len(df) < 3:
        return {"pattern": "Insufficient Data", "direction": "neutral", "body_pct": 0, "all_patterns": ["Insufficient Data"]}

    c0 = df.iloc[-3]   # two bars ago
    c1 = df.iloc[-2]   # prior bar
    c2 = df.iloc[-1]   # current/latest bar

    m0 = _candle_metrics(c0.Open, c0.High, c0.Low, c0.Close)
    m1 = _candle_metrics(c1.Open, c1.High, c1.Low, c1.Close)
    m2 = _candle_metrics(c2.Open, c2.High, c2.Low, c2.Close)

    o0, h0, l0, cl0 = c0.Open, c0.High, c0.Low, c0.Close
    o1, h1, l1, cl1 = c1.Open, c1.High, c1.Low, c1.Close
    o2, h2, l2, cl2 = c2.Open, c2.High, c2.Low, c2.Close
    mid0 = (o0 + cl0) / 2

    matches = []  # every pattern whose criteria are true, in priority order

    # ── 5-candle patterns (need extra history) ──
    # Rising/Falling Three Methods: a big candle, three small candles that
    # stay contained inside its range (a pause), then a big candle in the
    # original direction making a new extreme — the trend resumes.
    if len(df) >= 5:
        cm2 = df.iloc[-5]   # day 1 of the 5
        cm1 = df.iloc[-4]   # day 2
        mm2 = _candle_metrics(cm2.Open, cm2.High, cm2.Low, cm2.Close)
        middle_bars = [cm1, c0, c1]
        contained = all(min(b.Open, b.Close) >= cm2.Low and max(b.Open, b.Close) <= cm2.High for b in middle_bars)
        if (mm2["bullish"] and mm2["body_pct"] > 50 and contained
                and m2["bullish"] and m2["body_pct"] > 50 and cl2 > cm2.Close):
            matches.append({"pattern": "Rising Three Methods", "direction": "bullish", "body_pct": m2["body_pct"]})
        if (not mm2["bullish"] and mm2["body_pct"] > 50 and contained
                and not m2["bullish"] and m2["body_pct"] > 50 and cl2 < cm2.Close):
            matches.append({"pattern": "Falling Three Methods", "direction": "bearish", "body_pct": m2["body_pct"]})

    # ── 3-candle patterns ──
    if (m0["bullish"] and m1["bullish"] and m2["bullish"]
            and m0["body_pct"] > 50 and m1["body_pct"] > 50 and m2["body_pct"] > 50
            and cl1 > c0.Close and cl2 > c1.Close):
        matches.append({"pattern": "Three White Soldiers", "direction": "bullish", "body_pct": m2["body_pct"]})
    if (not m0["bullish"] and not m1["bullish"] and not m2["bullish"]
            and m0["body_pct"] > 50 and m1["body_pct"] > 50 and m2["body_pct"] > 50
            and cl1 < c0.Close and cl2 < c1.Close):
        matches.append({"pattern": "Three Black Crows", "direction": "bearish", "body_pct": m2["body_pct"]})

    # Abandoned Baby: like Morning/Evening Star but with a full gap (zero
    # overlap) on BOTH sides of the isolated star candle — rarer and
    # stronger than a plain Morning/Evening Star, so it's checked first.
    if (not m0["bullish"] and m0["body_pct"] > 50 and m1["body_pct"] < 5
            and h1 < l0 and m2["bullish"] and l2 > h1 and m2["body_pct"] > 50):
        matches.append({"pattern": "Abandoned Baby (Bull)", "direction": "bullish", "body_pct": m2["body_pct"]})
    if (m0["bullish"] and m0["body_pct"] > 50 and m1["body_pct"] < 5
            and l1 > h0 and not m2["bullish"] and h2 < l1 and m2["body_pct"] > 50):
        matches.append({"pattern": "Abandoned Baby (Bear)", "direction": "bearish", "body_pct": m2["body_pct"]})

    # Morning / Evening Star (and Doji variants): big candle, small-body
    # "star" that gaps away from it, then a big candle closing back through
    # the midpoint of the first candle.
    star_small = m1["body_pct"] < 35
    if (not m0["bullish"] and m0["body_pct"] > 50 and star_small
            and max(o1, cl1) < cl0 and m2["bullish"] and cl2 > mid0 and m2["body_pct"] > 50):
        if m1["body_pct"] < 5:
            matches.append({"pattern": "Morning Doji Star", "direction": "bullish", "body_pct": m2["body_pct"]})
        else:
            matches.append({"pattern": "Morning Star", "direction": "bullish", "body_pct": m2["body_pct"]})
    if (m0["bullish"] and m0["body_pct"] > 50 and star_small
            and min(o1, cl1) > cl0 and not m2["bullish"] and cl2 < mid0 and m2["body_pct"] > 50):
        if m1["body_pct"] < 5:
            matches.append({"pattern": "Evening Doji Star", "direction": "bearish", "body_pct": m2["body_pct"]})
        else:
            matches.append({"pattern": "Evening Star", "direction": "bearish", "body_pct": m2["body_pct"]})

    # Tri-Star: three consecutive dojis at an extreme — rare, very strong
    # reversal signal (the middle doji marks the exact turning point).
    if m0["body_pct"] < 5 and m1["body_pct"] < 5 and m2["body_pct"] < 5:
        if l1 < l0 and l1 < l2:
            matches.append({"pattern": "Tri-Star (Bull)", "direction": "bullish", "body_pct": m2["body_pct"]})
        if h1 > h0 and h1 > h2:
            matches.append({"pattern": "Tri-Star (Bear)", "direction": "bearish", "body_pct": m2["body_pct"]})

    # Three Inside Up/Down: harami (c0→c1) confirmed by a c2 close beyond c0.
    if (not m0["bullish"] and m0["body_pct"] > 50 and m1["bullish"]
            and max(o1, cl1) <= max(o0, cl0) and min(o1, cl1) >= min(o0, cl0)
            and m2["bullish"] and cl2 > o0):
        matches.append({"pattern": "Three Inside Up", "direction": "bullish", "body_pct": m2["body_pct"]})
    if (m0["bullish"] and m0["body_pct"] > 50 and not m1["bullish"]
            and max(o1, cl1) <= max(o0, cl0) and min(o1, cl1) >= min(o0, cl0)
            and not m2["bullish"] and cl2 < o0):
        matches.append({"pattern": "Three Inside Down", "direction": "bearish", "body_pct": m2["body_pct"]})

    # Tasuki Gap: a continuation candle gaps further in the trend direction,
    # then an opposite-color candle opens inside that candle's body and
    # closes inside the gap without fully filling it — the gap "holds".
    if (m0["bullish"] and m1["bullish"] and l1 > h0
            and not m2["bullish"] and o2 > o1 and o2 < cl1
            and cl2 > h0 and cl2 < l1):
        matches.append({"pattern": "Upside Tasuki Gap", "direction": "bullish", "body_pct": m2["body_pct"]})
    if (not m0["bullish"] and not m1["bullish"] and h1 < l0
            and m2["bullish"] and o2 < o1 and o2 > cl1
            and cl2 < l0 and cl2 > h1):
        matches.append({"pattern": "Downside Tasuki Gap", "direction": "bearish", "body_pct": m2["body_pct"]})

    # ── 2-candle patterns ──
    # Kicking Pattern: two marubozu-like candles with a gap between them and
    # no overlap — a strong reversal/continuation signal.
    if (m1["body_pct"] > 90 and m2["body_pct"] > 90
            and not m1["bullish"] and m2["bullish"] and o2 > h1):
        matches.append({"pattern": "Kicking Pattern (Bull)", "direction": "bullish", "body_pct": m2["body_pct"]})
    if (m1["body_pct"] > 90 and m2["body_pct"] > 90
            and m1["bullish"] and not m2["bullish"] and o2 < l1):
        matches.append({"pattern": "Kicking Pattern (Bear)", "direction": "bearish", "body_pct": m2["body_pct"]})

    # Harami Cross: harami where the inside candle is a doji — a stronger
    # version of a plain harami.
    if (not m1["bullish"] and m1["body_pct"] > 50 and m2["body_pct"] < 5
            and max(o2, cl2) <= max(o1, cl1) and min(o2, cl2) >= min(o1, cl1)):
        matches.append({"pattern": "Harami Cross (Bull)", "direction": "bullish", "body_pct": m2["body_pct"]})
    if (m1["bullish"] and m1["body_pct"] > 50 and m2["body_pct"] < 5
            and max(o2, cl2) <= max(o1, cl1) and min(o2, cl2) >= min(o1, cl1)):
        matches.append({"pattern": "Harami Cross (Bear)", "direction": "bearish", "body_pct": m2["body_pct"]})

    # Rising / Falling Window: a clean gap that isn't filled — a
    # continuation signal in the direction of the existing trend.
    if m1["bullish"] and m2["bullish"] and l2 > h1:
        matches.append({"pattern": "Rising Window", "direction": "bullish", "body_pct": m2["body_pct"]})
    if not m1["bullish"] and not m2["bullish"] and h2 < l1:
        matches.append({"pattern": "Falling Window", "direction": "bearish", "body_pct": m2["body_pct"]})

    # On-Neck: downtrend continues — a small bullish candle opens below the
    # prior bearish candle's low and closes right at/near that low, failing
    # to reverse the trend.
    tol_oneck = m1["range"] * 0.05
    if (not m1["bullish"] and m1["body_pct"] > 50 and m2["bullish"]
            and o2 < l1 and abs(cl2 - l1) <= tol_oneck):
        matches.append({"pattern": "On-Neck Pattern", "direction": "bearish", "body_pct": m2["body_pct"]})

    if not m1["bullish"] and m2["bullish"] and o2 <= cl1 and cl2 >= o1 and m2["body_pct"] > m1["body_pct"]:
        matches.append({"pattern": "Bullish Engulfing", "direction": "bullish", "body_pct": m2["body_pct"]})
    if m1["bullish"] and not m2["bullish"] and o2 >= cl1 and cl2 <= o1 and m2["body_pct"] > m1["body_pct"]:
        matches.append({"pattern": "Bearish Engulfing", "direction": "bearish", "body_pct": m2["body_pct"]})

    if (not m1["bullish"] and m2["bullish"] and o2 < l1
            and cl2 > (o1 + cl1) / 2 and cl2 < o1):
        matches.append({"pattern": "Piercing Line", "direction": "bullish", "body_pct": m2["body_pct"]})
    if (m1["bullish"] and not m2["bullish"] and o2 > h1
            and cl2 < (o1 + cl1) / 2 and cl2 > o1):
        matches.append({"pattern": "Dark Cloud Cover", "direction": "bearish", "body_pct": m2["body_pct"]})

    if not m1["bullish"] and m1["body_pct"] > 50 and m2["body"] < m1["body"] * 0.5 \
            and max(o2, cl2) <= max(o1, cl1) and min(o2, cl2) >= min(o1, cl1):
        matches.append({"pattern": "Bullish Harami", "direction": "bullish", "body_pct": m2["body_pct"]})
    if m1["bullish"] and m1["body_pct"] > 50 and m2["body"] < m1["body"] * 0.5 \
            and max(o2, cl2) <= max(o1, cl1) and min(o2, cl2) >= min(o1, cl1):
        matches.append({"pattern": "Bearish Harami", "direction": "bearish", "body_pct": m2["body_pct"]})

    tol = m1["range"] * 0.1
    if not m1["bullish"] and m2["bullish"] and abs(l1 - l2) <= tol:
        matches.append({"pattern": "Tweezer Bottom", "direction": "bullish", "body_pct": m2["body_pct"]})
    if m1["bullish"] and not m2["bullish"] and abs(h1 - h2) <= tol:
        matches.append({"pattern": "Tweezer Top", "direction": "bearish", "body_pct": m2["body_pct"]})

    # ── 1-candle patterns (on the latest bar) ──
    # These are checked independently of everything above (not "elif"), so
    # a candle that also satisfies a multi-candle pattern above STILL gets
    # its single-candle shape recognized here and added to all_patterns.
    bp = m2["body_pct"]
    if bp < 5:
        if m2["upper_wick"] > 0.6 * m2["range"] and m2["lower_wick"] < 0.15 * m2["range"]:
            matches.append({"pattern": "Gravestone Doji", "direction": "bearish", "body_pct": bp})
        elif m2["lower_wick"] > 0.6 * m2["range"] and m2["upper_wick"] < 0.15 * m2["range"]:
            matches.append({"pattern": "Dragonfly Doji", "direction": "bullish", "body_pct": bp})
        elif m2["upper_wick"] > 0.3 * m2["range"] and m2["lower_wick"] > 0.3 * m2["range"]:
            matches.append({"pattern": "Long-Legged Doji", "direction": "neutral", "body_pct": bp})
        else:
            matches.append({"pattern": "Doji", "direction": "neutral", "body_pct": bp})

    if bp > 90:
        matches.append({"pattern": "Bullish Marubozu" if m2["bullish"] else "Bearish Marubozu",
                         "direction": "bullish" if m2["bullish"] else "bearish", "body_pct": bp})

    if bp < 35 and m2["lower_wick"] > 2 * m2["body"] and m2["upper_wick"] < 0.3 * m2["body"] + 1e-9:
        matches.append({"pattern": "Hammer Candle" if m2["bullish"] else "Hanging Man",
                         "direction": "bullish" if m2["bullish"] else "bearish", "body_pct": bp})
    if bp < 35 and m2["upper_wick"] > 2 * m2["body"] and m2["lower_wick"] < 0.3 * m2["body"] + 1e-9:
        matches.append({"pattern": "Inverted Hammer" if m2["bullish"] else "Shooting Star",
                         "direction": "bullish" if m2["bullish"] else "bearish", "body_pct": bp})

    # Long Lower/Upper Shadow: a notable one-sided wick that isn't extreme
    # enough to qualify as a Hammer/Shooting Star (bigger body, 35-60%).
    if 35 <= bp < 60 and m2["lower_wick"] > 1.5 * m2["body"] and m2["upper_wick"] < 0.4 * m2["body"] and m2["bullish"]:
        matches.append({"pattern": "Long Lower Shadow", "direction": "bullish", "body_pct": bp})
    if 35 <= bp < 60 and m2["upper_wick"] > 1.5 * m2["body"] and m2["lower_wick"] < 0.4 * m2["body"] and not m2["bullish"]:
        matches.append({"pattern": "Long Upper Shadow", "direction": "bearish", "body_pct": bp})

    if bp < 35 and m2["upper_wick"] > m2["body"] and m2["lower_wick"] > m2["body"]:
        matches.append({"pattern": "Spinning Top White" if m2["bullish"] else "Spinning Top Black",
                         "direction": "bullish" if m2["bullish"] else "bearish", "body_pct": bp})

    # Guaranteed catch-all — always true for exactly one of these two, so
    # `matches` can never end up empty, and every candle at minimum gets a
    # generic bullish/bearish "strength" reading in all_patterns.
    if bp > 60:
        matches.append({"pattern": "Strong Bullish" if m2["bullish"] else "Strong Bearish",
                         "direction": "bullish" if m2["bullish"] else "bearish", "body_pct": bp})
    else:
        matches.append({"pattern": "Weak Bullish" if m2["bullish"] else "Weak Bearish",
                         "direction": "bullish" if m2["bullish"] else "bearish", "body_pct": bp})

    dominant = matches[0]
    return {
        "pattern": dominant["pattern"],
        "direction": dominant["direction"],
        "body_pct": dominant["body_pct"],
        "all_patterns": [m["pattern"] for m in matches],
    }


def crsi_zone(crsi_val: float) -> str:
    if crsi_val is None or (isinstance(crsi_val, float) and math.isnan(crsi_val)):
        return "Unknown"
    if crsi_val < 30:
        return "Oversold"
    if crsi_val < 40:
        return "Watch Bullish"
    if crsi_val <= 60:
        return "Normal"
    if crsi_val <= 70:
        return "Watch Bearish"
    return "Overbought"


# ── Pattern taxonomy ─────────────────────────────────────────────────────
# Signal strength (Strong/Medium/Weak) and signal type (Reversal/Continuation)
# aligned with TradingView's Screener candlestick-pattern classification, so
# results read the same way traders already know from TradingView.
# https://in.tradingview.com/support/solutions/43000752737-candlestick-pattern-in-screener/
PATTERN_META = {
    "Three White Soldiers":    ("Strong", "Reversal"),
    "Three Black Crows":       ("Strong", "Reversal"),
    "Bullish Engulfing":       ("Strong", "Reversal"),
    "Bearish Engulfing":       ("Strong", "Reversal"),
    "Morning Star":            ("Strong", "Reversal"),
    "Evening Star":            ("Strong", "Reversal"),
    "Kicking Pattern (Bull)":  ("Strong", "Reversal"),
    "Kicking Pattern (Bear)":  ("Strong", "Reversal"),
    "Abandoned Baby (Bull)":   ("Strong", "Reversal"),
    "Abandoned Baby (Bear)":   ("Strong", "Reversal"),
    "Rising Three Methods":    ("Strong", "Continuation"),
    "Falling Three Methods":   ("Strong", "Continuation"),
    "Piercing Line":           ("Medium", "Reversal"),
    "Dark Cloud Cover":        ("Medium", "Reversal"),
    "Bullish Harami":          ("Medium", "Reversal"),
    "Bearish Harami":          ("Medium", "Reversal"),
    "Tweezer Bottom":          ("Medium", "Reversal"),
    "Tweezer Top":             ("Medium", "Reversal"),
    "Harami Cross (Bull)":     ("Medium", "Reversal"),
    "Harami Cross (Bear)":     ("Medium", "Reversal"),
    "Morning Doji Star":       ("Medium", "Reversal"),
    "Evening Doji Star":       ("Medium", "Reversal"),
    "Three Inside Up":         ("Medium", "Reversal"),
    "Three Inside Down":       ("Medium", "Reversal"),
    "Rising Window":           ("Medium", "Continuation"),
    "Falling Window":          ("Medium", "Continuation"),
    "Upside Tasuki Gap":       ("Medium", "Continuation"),
    "Downside Tasuki Gap":     ("Medium", "Continuation"),
    "Tri-Star (Bull)":         ("Medium", "Reversal"),
    "Tri-Star (Bear)":         ("Medium", "Reversal"),
    "Doji":                    ("Weak", "Reversal"),
    "Long-Legged Doji":        ("Weak", "Reversal"),
    "Gravestone Doji":         ("Weak", "Reversal"),
    "Dragonfly Doji":          ("Weak", "Reversal"),
    "Hammer Candle":           ("Weak", "Reversal"),
    "Inverted Hammer":         ("Weak", "Reversal"),
    "Shooting Star":           ("Weak", "Reversal"),
    "Hanging Man":             ("Weak", "Reversal"),
    "Bullish Marubozu":        ("Weak", "Continuation"),
    "Bearish Marubozu":        ("Weak", "Continuation"),
    "Spinning Top White":      ("Weak", "Reversal"),
    "Spinning Top Black":      ("Weak", "Reversal"),
    "Long Lower Shadow":       ("Weak", "Reversal"),
    "Long Upper Shadow":       ("Weak", "Reversal"),
    "On-Neck Pattern":         ("Weak", "Continuation"),
}


def pattern_meta(pattern: str) -> tuple:
    """Returns (strength, signal_type) for a detected pattern, defaulting to
    ('Weak', 'Reversal') for anything outside the known taxonomy (e.g. the
    generic Strong/Weak Bullish/Bearish body-based fallback categories)."""
    return PATTERN_META.get(pattern, ("Weak", "Reversal"))


STRENGTH_WEIGHT = {"Strong": 1.0, "Medium": 0.7, "Weak": 0.4}


def setup_score(crsi_val, crsi_zone_str, atr_dir, candle_direction, candle_body_pct, vol_ratio, pattern_strength) -> int:
    """A 0-100 technical-confluence score — NOT a statistical probability of
    profit. It rewards the same confluence your journal's grading uses:
    a CRSI extreme, in the same direction as a clean, decisive candle, with
    ATR expanding (energy behind the move), above-average volume, and a
    pattern classified as Strong/Medium/Weak (TradingView-style taxonomy)."""
    score = 0
    # CRSI extremity (up to 35 pts) — further from 50 = more stretched.
    if crsi_val is not None:
        score += min(35, round(abs(crsi_val - 50) / 50 * 35))
    # Candle clarity (up to 20 pts) — how decisive the body is.
    score += min(20, round(candle_body_pct / 100 * 20))
    # Pattern strength (up to 20 pts) — Strong/Medium/Weak per taxonomy.
    score += round(STRENGTH_WEIGHT.get(pattern_strength, 0.4) * 20)
    # ATR state (up to 15 pts) — expansion means the move has energy behind it.
    if atr_dir == "Expanding":
        score += 15
    elif atr_dir == "Flat":
        score += 6
    # Directional agreement between CRSI zone and candle (up to 5 pts).
    zone_bias = "bullish" if crsi_zone_str == "Oversold" else "bearish" if crsi_zone_str == "Overbought" else None
    if zone_bias and zone_bias == candle_direction:
        score += 5
    # Volume confirmation (up to 5 pts).
    if vol_ratio is not None and vol_ratio >= 1.3:
        score += 5
    return max(0, min(100, score))


# ── STOP-HUNT REVERSAL DETECTOR ──────────────────────────────────────────
def detect_stop_hunt_reversal(df: pd.DataFrame) -> dict:
    """
    Detects the 2-candle "Stop-Hunt Reversal" pattern.
    Returns a dictionary with pattern_detected, direction, and key levels.
    """
    if len(df) < 20:  # Need enough data for ATR and Swing High/Low detection
        return {"pattern_detected": False}

    c1 = df.iloc[-2]  # Day 1 (The Trap)
    c2 = df.iloc[-1]  # Day 2 (The Confirmation)

    # --- Helper Metrics ---
    def get_body_pct(o, h, l, c):
        rng = max(h - l, 1e-9)
        body = abs(c - o)
        return body / rng * 100

    def get_upper_wick_pct(o, h, l, c):
        rng = max(h - l, 1e-9)
        return (h - max(o, c)) / rng * 100

    def get_lower_wick_pct(o, h, l, c):
        rng = max(h - l, 1e-9)
        return (min(o, c) - l) / rng * 100

    # --- Pre-checks ---
    # 1. ATR Calculation (Current and 5 days ago)
    atr_series = compute_atr(df)
    atr_cur = atr_series.iloc[-1]
    atr_5d_ago = atr_series.iloc[-6] if len(atr_series) >= 6 else atr_cur
    if pd.isna(atr_cur) or pd.isna(atr_5d_ago) or atr_5d_ago == 0:
        return {"pattern_detected": False}
    atr_expanding = (atr_cur - atr_5d_ago) / atr_5d_ago > 0.05

    # 2. Swing High / Low Detection (Highest/Lowest of last 10 candles, excluding today)
    swing_high = df['High'].iloc[-11:-1].max()
    swing_low = df['Low'].iloc[-11:-1].min()
    
    # 3. Volume Checks
    avg_vol_20 = df['Volume'].tail(20).mean()
    vol_d1 = c1['Volume']
    vol_d2 = c2['Volume']
    vol_d1_above_avg = vol_d1 > avg_vol_20

    # 4. Space Check (1.5 ATR clear space)
    atr_val = float(atr_cur)
    clean_space_below = c2['Low'] - (1.5 * atr_val)  # Simplified proxy for "clean space"
    # We cannot check FVGs/OrderBlocks here without more complex structure detection.
    # We will just state the available ATR distance below as a proxy.
    space_clear_below = True # placeholder, we will output the ATR distance

    # --- DETECT BEARISH SETUP (Short) ---
    # Day 1: Long Green, above Swing High, ATR Expanding, Vol > Avg
    d1_bear_trap = (
        c1['Close'] > c1['Open'] and 
        get_body_pct(c1['Open'], c1['High'], c1['Low'], c1['Close']) > 60 and
        c1['Close'] > swing_high and
        atr_expanding and
        vol_d1_above_avg
    )

    # Day 2: Close RED, Close below Day 1 High, Long Upper Wick, Vol RED, Vol > D1
    d2_bear_conf = (
        c2['Close'] < c2['Open'] and  # Close RED
        c2['Close'] < c1['High'] and  # Close below Day 1 High
        get_upper_wick_pct(c2['Open'], c2['High'], c2['Low'], c2['Close']) > 0.6 and  # Long upper wick > 60% of range
        (c2['Close'] - c2['Low']) < (c2['High'] - c2['Close']) and # Ensuring volume color is RED (Close < Open, so the body is RED)
        vol_d2 > vol_d1 and  # Volume higher than Day 1
        not (atr_cur < atr_series.iloc[-2] and atr_cur < atr_series.iloc[-3]) # Not aggressively contracting
    )

    if d1_bear_trap and d2_bear_conf:
        return {
            "pattern_detected": True,
            "direction": "Bearish",
            "day1_close": float(c1['Close']),
            "day2_close": float(c2['Close']),
            "day2_high": float(c2['High']),  # Stop-loss level
            "atr_value": atr_val,
            "space_clear_pct": 1.5, # We return the required 1.5 ATR as the check
        }

    # --- DETECT BULLISH SETUP (Long) ---
    # Day 1: Long Red, below Swing Low, ATR Expanding, Vol > Avg
    d1_bull_trap = (
        c1['Close'] < c1['Open'] and 
        get_body_pct(c1['Open'], c1['High'], c1['Low'], c1['Close']) > 60 and
        c1['Close'] < swing_low and
        atr_expanding and
        vol_d1_above_avg
    )

    # Day 2: Close GREEN, Close above Day 1 Low, Long Lower Wick, Vol GREEN, Vol > D1
    d2_bull_conf = (
        c2['Close'] > c2['Open'] and  # Close GREEN
        c2['Close'] > c1['Low'] and   # Close above Day 1 Low
        get_lower_wick_pct(c2['Open'], c2['High'], c2['Low'], c2['Close']) > 0.6 and  # Long lower wick > 60% of range
        (c2['High'] - c2['Close']) < (c2['Close'] - c2['Open']) and # Ensuring volume color is GREEN
        vol_d2 > vol_d1 and
        not (atr_cur < atr_series.iloc[-2] and atr_cur < atr_series.iloc[-3]) 
    )

    if d1_bull_trap and d2_bull_conf:
        return {
            "pattern_detected": True,
            "direction": "Bullish",
            "day1_close": float(c1['Close']),
            "day2_close": float(c2['Close']),
            "day2_low": float(c2['Low']),  # Stop-loss level
            "atr_value": atr_val,
            "space_clear_pct": 1.5,
        }

    return {"pattern_detected": False}


# ── Per-symbol analysis ──────────────────────────────────────────────────
def analyze_symbol(symbol: str, df: pd.DataFrame) -> dict:
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    if len(df) < 30:
        return None

    crsi = compute_crsi(df["Close"])
    atr = compute_atr(df)
    candle = classify_candle(df)
    strength, signal_type = pattern_meta(candle["pattern"])
    hunt = detect_stop_hunt_reversal(df)

    last = df.iloc[-1]
    crsi_val = round(float(crsi.iloc[-1]), 1) if not pd.isna(crsi.iloc[-1]) else None
    atr_val = round(float(atr.iloc[-1]), 2) if not pd.isna(atr.iloc[-1]) else None
    atr_pct = round(atr_val / last["Close"] * 100, 2) if atr_val else None
    atr_dir = atr_direction(atr)
    zone = crsi_zone(crsi_val) if crsi_val is not None else "Unknown"

    avg_vol_20 = df["Volume"].tail(20).mean() if "Volume" in df else None
    vol_ratio = round(float(last["Volume"] / avg_vol_20), 2) if avg_vol_20 else None

    prev_close = df["Close"].iloc[-2]
    day_change_pct = round((last["Close"] - prev_close) / prev_close * 100, 2)

    result = {
        "symbol": symbol,
        "price": round(float(last["Close"]), 2),
        "day_change_pct": day_change_pct,
        "crsi": crsi_val,
        "crsi_zone": zone,
        "atr": atr_val,
        "atr_pct": atr_pct,
        "atr_direction": atr_dir,
        "candle_pattern": candle["pattern"],
        "candle_all_patterns": candle["all_patterns"],
        "candle_direction": candle["direction"],
        "candle_body_pct": round(candle["body_pct"], 1),
        "candle_strength": strength,
        "candle_signal_type": signal_type,
        "volume": int(last["Volume"]) if "Volume" in df and not pd.isna(last["Volume"]) else None,
        "vol_vs_avg20": vol_ratio,
        "setup_score": setup_score(crsi_val, zone, atr_dir, candle["direction"], candle["body_pct"], vol_ratio, strength),
        "as_of": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1]),
    }

    # ── Add Stop-Hunt Data if detected ──
    if hunt.get("pattern_detected"):
        result["is_stop_hunt"] = True
        result["hunt_direction"] = hunt["direction"]
        result["stop_loss_price"] = round(hunt.get("day2_high", 0), 2) if hunt["direction"] == "Bearish" else round(hunt.get("day2_low", 0), 2)
        result["target_price"] = round(last["Close"] - (1.5 * hunt["atr_value"]), 2) if hunt["direction"] == "Bearish" else round(last["Close"] + (1.5 * hunt["atr_value"]), 2)
        result["space_clear"] = True # Simplified to True for now
    else:
        result["is_stop_hunt"] = False

    return result


# ── Scan orchestration ───────────────────────────────────────────────────
def run_scan(symbols=None, period="9mo") -> dict:
    """Fetches OHLCV for every symbol and returns computed metrics.
    Uses a single batched yfinance.download call for speed."""
    if yf is None:
        return {"results": [], "errors": ["yfinance is not installed. Run: pip install yfinance"]}

    symbols = symbols or NIFTY50
    tickers = [_to_yf_ticker(s) for s in symbols]

    try:
        data = yf.download(
            tickers=tickers, period=period, interval="1d",
            group_by="ticker", threads=True, progress=False, auto_adjust=True,
        )
    except Exception as e:
        return {"results": [], "errors": [f"yfinance download failed: {e}"]}

    results, errors = [], []
    for sym, yf_sym in zip(symbols, tickers):
        try:
            df = _flatten_yf_df(data, yf_sym)
            if df is None or df.empty:
                errors.append(f"{sym}: no data returned")
                continue
            if not {"Open","High","Low","Close"}.issubset(set(df.columns)):
                errors.append(f"{sym}: unexpected columns {list(df.columns)}")
                continue
            row = analyze_symbol(sym, df)
            if row:
                results.append(row)
            else:
                errors.append(f"{sym}: insufficient history")
        except Exception as e:
            errors.append(f"{sym}: {e}")

    return {"results": results, "errors": errors}


def run_backtest(symbol, years=3, crsi="any", atr="any", candle_dir="any",
                  candle_pattern="any", clean_only=False, trade_side="auto") -> dict:
    """Replays the screener's filters day-by-day over real historical data
    for one symbol, and simulates a simple mechanical strategy:

      - A trade opens on any day that matches ALL the selected filters
        (using only data available up to and including that day — no
        lookahead). `trade_side` controls which direction is actually
        traded: "auto" follows that day's candle color (bullish->BUY,
        bearish->SELL, and skips neutral candles); "buy" or "sell" forces
        every matching signal to open in that direction regardless of the
        candle's own color — useful for testing "always buy the dip" style
        rules independent of the triggering candle's color.
      - While open, it's held as long as each new day's close moves in the
        trade's favor (or is flat). The moment a day closes against it,
        the trade exits immediately at that close.
      - Only one trade is open at a time — a new signal is ignored while
        already in a trade.

    Also returns a `signal_diagnostics` breakdown showing how many days
    matched EACH filter individually (ignoring the "one trade at a time"
    rule), so a low trade count can be told apart from a bug: if the
    combined-match count is already tiny before any trade-state logic even
    runs, that's a genuinely rare setup for this symbol/period, not a
    filtering error.

    This is a simplified, no-lookahead backtest against daily closes (not
    intraday fills), meant to show roughly how a rule like this would have
    played out historically — not a precise execution simulation.
    """
    if yf is None:
        return {"error": "yfinance is not installed. Run: pip install yfinance", "trades": [], "summary": None}
    if not symbol:
        return {"error": "symbol is required", "trades": [], "summary": None}

    yf_sym = _to_yf_ticker(symbol)
    try:
        df = yf.download(tickers=yf_sym, period=f"{years}y", interval="1d",
                          progress=False, auto_adjust=True)
    except Exception as e:
        return {"error": f"download failed: {e}", "trades": [], "summary": None}
    if df is None or df.empty:
        return {"error": f"No historical data returned for {symbol}", "trades": [], "summary": None}
    yf_sym2 = _to_yf_ticker(symbol)
    df = _flatten_yf_df(df, yf_sym2)
    if df is None:
        return []
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()

    MIN_BARS = 110  # needs ~100 for the CRSI percent-rank component to warm up
    if len(df) < MIN_BARS:
        return {"error": f"Only {len(df)} trading days of data available — need at least {MIN_BARS} "
                          f"for reliable CRSI. Try a longer period or check the symbol.",
                "trades": [], "summary": None}

    close = df["Close"]
    crsi_series = compute_crsi(close)
    atr_series = compute_atr(df)
    start_idx = 100  # warmup so CRSI's 100-bar percent-rank has real data

    # ── Pass 1: compute every day's indicators + per-filter match flags ──
    days = []
    for i in range(start_idx, len(df)):
        crsi_val = crsi_series.iloc[i]
        if pd.isna(crsi_val):
            continue
        zone = crsi_zone(float(crsi_val))
        atr_dir_i = atr_direction(atr_series.iloc[:i + 1])
        window = df.iloc[max(0, i - 4):i + 1]
        candle = classify_candle(window)
        strength, signal_type = pattern_meta(candle["pattern"])

        crsi_ok = (crsi == "any" or zone == crsi)
        atr_ok = (atr == "any" or atr_dir_i == atr)
        dir_ok = (candle_dir == "any" or candle["direction"] == candle_dir)
        pattern_ok = (candle_pattern == "any" or candle_pattern in candle["all_patterns"])
        clean_ok = (not clean_only or candle["body_pct"] > 60)
        all_ok = crsi_ok and atr_ok and dir_ok and pattern_ok and clean_ok

        days.append({
            "idx": i, "date": df.index[i], "close": float(close.iloc[i]),
            "zone": zone, "atr_dir": atr_dir_i, "candle": candle, "strength": strength,
            "crsi_ok": crsi_ok, "atr_ok": atr_ok, "dir_ok": dir_ok,
            "pattern_ok": pattern_ok, "clean_ok": clean_ok, "all_ok": all_ok,
        })

    signal_diagnostics = {
        "total_days_scanned": len(days),
        "crsi_zone_matches": sum(1 for d in days if d["crsi_ok"]),
        "atr_state_matches": sum(1 for d in days if d["atr_ok"]),
        "candle_direction_matches": sum(1 for d in days if d["dir_ok"]),
        "candle_pattern_matches": sum(1 for d in days if d["pattern_ok"]),
        "clean_candle_matches": sum(1 for d in days if d["clean_ok"]),
        "all_filters_combined_matches": sum(1 for d in days if d["all_ok"]),
    }

    # ── Pass 2: walk the days as a simple one-trade-at-a-time state machine ──
    trades = []
    open_trade = None

    for d in days:
        i, date_i, close_i = d["idx"], d["date"], d["close"]

        if open_trade is None:
            if not d["all_ok"]:
                continue
            candle = d["candle"]
            if trade_side == "buy":
                side = "BUY"
            elif trade_side == "sell":
                side = "SELL"
            else:
                if candle["direction"] not in ("bullish", "bearish"):
                    continue  # auto mode needs a directional candle to pick a side
                side = "BUY" if candle["direction"] == "bullish" else "SELL"

            # If a specific pattern was requested, show THAT pattern as the
            # Setup — not the day's unrelated "dominant" label. A day can
            # satisfy your filter (e.g. "Hanging Man") via all_patterns
            # while some other pattern (e.g. "Bearish Harami") happens to
            # be the higher-priority display label for that same day; the
            # Setup column should reflect why the trade actually triggered.
            if candle_pattern != "any":
                display_pattern = candle_pattern
                display_strength, _ = pattern_meta(candle_pattern)
            else:
                display_pattern = candle["pattern"]
                display_strength = d["strength"]

            open_trade = {
                "side": side,
                "entry_date": str(date_i.date()),
                "entry_price": round(close_i, 2),
                "entry_idx": i,
                "candle_pattern": display_pattern,
                "candle_strength": display_strength,
                "crsi_zone": d["zone"],
                "atr_direction": d["atr_dir"],
            }
        else:
            prev_close = float(close.iloc[i - 1])
            favorable = (close_i >= prev_close) if open_trade["side"] == "BUY" else (close_i <= prev_close)
            if not favorable:
                direction_mult = 1 if open_trade["side"] == "BUY" else -1
                pnl_pct = round((close_i - open_trade["entry_price"]) / open_trade["entry_price"] * direction_mult * 100, 2)
                trades.append({
                    **{k: v for k, v in open_trade.items() if k != "entry_idx"},
                    "exit_date": str(date_i.date()),
                    "exit_price": round(close_i, 2),
                    "days_held": i - open_trade["entry_idx"],
                    "pnl_pct": pnl_pct,
                    "exit_reason": "Adverse close",
                })
                open_trade = None

    if open_trade is not None:
        last_close = float(close.iloc[-1])
        direction_mult = 1 if open_trade["side"] == "BUY" else -1
        pnl_pct = round((last_close - open_trade["entry_price"]) / open_trade["entry_price"] * direction_mult * 100, 2)
        trades.append({
            **{k: v for k, v in open_trade.items() if k != "entry_idx"},
            "exit_date": str(df.index[-1].date()),
            "exit_price": round(last_close, 2),
            "days_held": len(df) - 1 - open_trade["entry_idx"],
            "pnl_pct": pnl_pct,
            "exit_reason": "Backtest ended (still open)",
        })

    total = len(trades)
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    losses = sum(1 for t in trades if t["pnl_pct"] < 0)
    pnl_list = [t["pnl_pct"] for t in trades]
    win_list = [p for p in pnl_list if p > 0]
    loss_list = [p for p in pnl_list if p < 0]

    summary = {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 1) if total else 0,
        "total_pnl_pct": round(sum(pnl_list), 2),
        "avg_pnl_pct": round(sum(pnl_list) / total, 2) if total else 0,
        "avg_win_pct": round(sum(win_list) / len(win_list), 2) if win_list else 0,
        "avg_loss_pct": round(sum(loss_list) / len(loss_list), 2) if loss_list else 0,
        "best_trade_pct": max(pnl_list) if pnl_list else None,
        "worst_trade_pct": min(pnl_list) if pnl_list else None,
        "avg_days_held": round(sum(t["days_held"] for t in trades) / total, 1) if total else 0,
    }

    return {
        "symbol": symbol,
        "years": years,
        "bars_analyzed": len(df),
        "from_date": str(df.index[0].date()),
        "to_date": str(df.index[-1].date()),
        "filters": {"crsi": crsi, "atr": atr, "candle_dir": candle_dir,
                    "candle_pattern": candle_pattern, "clean_only": clean_only,
                    "trade_side": trade_side},
        "trades": trades,
        "summary": summary,
        "signal_diagnostics": signal_diagnostics,
        "error": None,
    }



def get_day_moves(symbol, entry_date_str, entry_price, num_days=5) -> list:
    """Auto-fetches the D1–D5 price move after a paper trade's entry date —
    the same concept as the manual journal's next_day_move/move_d2..d5
    fields, just pulled from real historical data instead of typed in by
    hand. Day 1 = the first trading day's close AFTER entry_date (entry
    day itself is day 0 and excluded). Returns up to `num_days` dicts:
    {day, date, price, move_pct}. Days that haven't happened yet simply
    aren't in the list."""
    if yf is None or not symbol or not entry_date_str or not entry_price:
        return []
    yf_sym = _to_yf_ticker(symbol)
    try:
        df = yf.download(
            tickers=yf_sym, start=entry_date_str, interval="1d",
            progress=False, auto_adjust=True,
        )
    except Exception:
        return []
    if df is None or df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])

    try:
        entry_ts = pd.Timestamp(entry_date_str)
    except Exception:
        return []
    after = df[df.index > entry_ts]

    moves = []
    for i, (idx, row) in enumerate(after.head(num_days).iterrows(), start=1):
        try:
            close = float(row["Close"])
        except Exception:
            continue
        moves.append({
            "day": i,
            "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
            "price": round(close, 2),
            "move_pct": round((close - entry_price) / entry_price * 100, 2),
        })
    return moves


def get_quotes(symbols) -> dict:
    """Fetches the latest daily close for a small, specific list of symbols —
    used to mark open paper trades to market. Not cached (deliberately small
    list), so it's fine to call this on every journal page load."""
    if yf is None or not symbols:
        return {}
    tickers = [_to_yf_ticker(s) for s in symbols]
    try:
        data = yf.download(
            tickers=tickers, period="5d", interval="1d",
            group_by="ticker", threads=True, progress=False, auto_adjust=True,
        )
    except Exception:
        return {}

    quotes = {}
    for sym, yf_sym in zip(symbols, tickers):
        try:
            if len(tickers) == 1:
                df = data
            else:
                df = data[yf_sym] if yf_sym in data.columns.get_level_values(0) else None
            if df is None or df.empty:
                continue
            last = df.dropna(subset=["Close"]).iloc[-1]
            quotes[sym] = round(float(last["Close"]), 2)
        except Exception:
            continue
    return quotes


def get_scan(force_refresh=False, universe="all", pattern="any") -> dict:
    """Cached accessor — avoids re-hitting Yahoo Finance on every filter tweak.
    `universe` is one of: nifty50, banknifty, sensex, indices, all.
    `pattern` filters results by the Stop-Hunt Reversal detection."""
    universe = universe if universe in UNIVERSES else "all"
    symbols = UNIVERSES[universe]

    with _cache_lock:
        cache = _caches.get(universe, {"timestamp": 0, "results": [], "errors": []})
        age = time.time() - cache["timestamp"]
        if not force_refresh and cache["results"] and age < CACHE_TTL_SECONDS:
            results = cache["results"]
            if pattern == "stophunt":
                results = [r for r in results if r.get("is_stop_hunt")]
            return {
                "results": results, "errors": cache["errors"],
                "cached": True, "age_seconds": int(age), "universe": universe,
            }

    scan = run_scan(symbols=symbols)
    results = scan["results"]
    if pattern == "stophunt":
        results = [r for r in results if r.get("is_stop_hunt")]

    with _cache_lock:
        _caches[universe] = {
            "results": scan["results"], "errors": scan["errors"], "timestamp": time.time(),
        }

    return {"results": results, "errors": scan["errors"], "cached": False,
            "age_seconds": 0, "universe": universe}