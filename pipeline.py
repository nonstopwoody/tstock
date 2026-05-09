"""
Thai Stock AI Advisor — Backend Pipeline
=========================================
Run: python pipeline.py --mode pre_market   (08:30 ก่อนตลาดเปิด)
Run: python pipeline.py --mode post_close   (17:30 หลังตลาดปิด)

โครงสร้างนี้พร้อมพัฒนาต่อ — ส่วนที่เป็น TODO คือจุดที่ต้องเชื่อม
แหล่งข้อมูลจริงและ API key
"""

from __future__ import annotations
import argparse
import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# === ติดตั้ง dependencies ===
# pip install yfinance pandas numpy feedparser beautifulsoup4 requests
# pip install pythainlp transformers torch  (สำหรับ sentiment ภาษาไทย)
# pip install anthropic                      (สำหรับ Claude API)

import numpy as np
import pandas as pd

# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Cache TTL for price data (seconds) — 30 minutes
PRICE_CACHE_TTL = 30 * 60

# HTTP timeout / User-Agent for scraping
HTTP_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SCRAPE_DELAY = 2.0  # seconds between scraper requests

TZ_BKK = timezone(timedelta(hours=7))

# Weights สำหรับ Composite Custom Indicator (CCI) ตาม timeframe
CCI_WEIGHTS = {
    "short":  {"tech": 0.4, "sent": 0.6},
    "medium": {"tech": 0.5, "sent": 0.5},
    "long":   {"tech": 0.7, "sent": 0.3},
}

# Tech score weights สำหรับแต่ละ indicator ตาม timeframe
TECH_WEIGHTS = {
    "short":  {"rsi": 0.4, "macd": 0.3, "ema": 0.2, "sma": 0.1},
    "medium": {"rsi": 0.2, "macd": 0.3, "ema": 0.3, "sma": 0.2},
    "long":   {"rsi": 0.1, "macd": 0.2, "ema": 0.2, "sma": 0.5},
}

# === LLM config ===
# Override via env var ANTHROPIC_MODEL if needed (e.g. claude-sonnet-4-6 / claude-opus-4-6)
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
LLM_MAX_TOKENS = 2000
RATE_LIMIT_DELAY = 1.5  # seconds between API calls

_ANTHROPIC_CLIENT = None  # lazy-init singleton

# === Sentiment config ===
# Backend selection: "auto" (try WangchanBERTa → multilingual → keyword), "wangchanberta", "multilingual", "keyword"
SENTIMENT_BACKEND = os.environ.get("SENTIMENT_BACKEND", "auto").lower()

# Default fine-tuned WangchanBERTa for sentiment (override via env var)
WANGCHANBERTA_MODEL = os.environ.get(
    "WANGCHANBERTA_MODEL",
    "poom-sci/WangchanBERTa-base-att-spm-uncased-finetuned",
)
# Multilingual fallback (works without Thai-specific model, lighter download)
MULTILINGUAL_MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment"

# Custom Thai financial lexicon — boosts general sentiment score by ±0.2
THAI_FIN_LEXICON_POS = ["นิวไฮ", "ออลไทม์ไฮ", "เป้าหมายใหม่", "บวกแรง",
                        "กำไรพุ่ง", "กำไรสูงสุด", "ปันผลพิเศษ"]
THAI_FIN_LEXICON_NEG = ["ผิดนัดชำระหนี้", "ฟ้องล้มละลาย", "ติดลบหนัก",
                        "ขาดทุนหนัก", "ปรับลดประมาณการ", "ปรับลดเป้า",
                        "Death Cross", "กดดัน NIM"]
LEXICON_BOOST = 0.2  # adjustment per lexicon hit

# Cache: text hash → score (cleared between pipeline runs because module reloads)
_sentiment_cache: dict[int, float] = {}


def _load_dotenv() -> None:
    """Minimal .env loader — no python-dotenv dependency."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _get_anthropic_client():
    """Lazy-init Anthropic client. Raises if API key missing or placeholder."""
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        _load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key or api_key.startswith("sk-ant-your-"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY missing or placeholder. "
                "Copy .env.example to .env and fill in your key from https://console.anthropic.com"
            )
        import anthropic
        _ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=api_key)
    return _ANTHROPIC_CLIENT

# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class NewsItem:
    headline: str
    source: str
    url: str
    published_at: str  # ISO string
    sentiment: float = 0.0  # [-1.0, +1.0]
    
    def hours_ago(self, ref: datetime) -> float:
        published = datetime.fromisoformat(self.published_at)
        return (ref - published).total_seconds() / 3600


@dataclass
class StockData:
    symbol: str
    name: str
    sector: str
    df: pd.DataFrame   # OHLCV history
    news: list[NewsItem]


# ============================================================
# LAYER 1: DATA PERCEPTION
# ============================================================

def fetch_price_data(symbol: str, period: str = "1y") -> pd.DataFrame:
    """
    ดึง OHLCV จาก Yahoo Finance พร้อม cache (TTL 30 นาที)
    Cache file: data/cache/{symbol}.pkl
    """
    cache_file = CACHE_DIR / f"{symbol}.pkl"

    # 1) Try cache
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < PRICE_CACHE_TTL:
            try:
                df = pd.read_pickle(cache_file)
                if not df.empty:
                    return df
            except Exception as e:
                print(f"  [warn] cache read failed for {symbol}: {e}")

    # 2) Fetch fresh
    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        if df.empty:
            raise ValueError(f"yfinance returned empty for {symbol}")
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]]
        # Save cache
        try:
            df.to_pickle(cache_file)
        except Exception as e:
            print(f"  [warn] cache write failed for {symbol}: {e}")
        return df
    except Exception as e:
        # Fall back to stale cache if available
        if cache_file.exists():
            print(f"  [warn] {symbol} fetch failed ({e}); using stale cache")
            return pd.read_pickle(cache_file)
        raise


RSS_SOURCES = [
    ("Settrade",  "https://www.settrade.com/rss/news.xml"),
    ("SET",       "https://www.set.or.th/api/set/news/rss"),
    ("Kaohoon",   "https://www.kaohoon.com/feed"),
    ("Infoquest", "https://www.infoquest.co.th/feed"),
]

# Module-level cache: each RSS feed fetched once per pipeline run
_RSS_CACHE: dict[str, list[dict]] | None = None


def _parse_rss_date(entry) -> datetime | None:
    """Parse RSS date with python-dateutil; convert to TZ_BKK."""
    from dateutil import parser as dt_parser
    raw = entry.get("published") or entry.get("updated") or ""
    if not raw:
        return None
    try:
        dt = dt_parser.parse(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_BKK)
        return dt.astimezone(TZ_BKK)
    except Exception:
        return None


def _entry_text(entry) -> str:
    """Concatenate title + summary for symbol matching."""
    title = entry.get("title", "") or ""
    summary = entry.get("summary", "") or entry.get("description", "") or ""
    return f"{title}\n{summary}"


def _fetch_all_rss() -> list[dict]:
    """
    Fetch all RSS sources once, return normalized list of dicts:
        {source, headline, url, summary, published_at, dt}
    Each source wrapped in try/except — failure of one doesn't break others.
    """
    global _RSS_CACHE
    if _RSS_CACHE is not None:
        return _RSS_CACHE

    import feedparser
    all_entries: list[dict] = []

    for source_name, url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
            count_before = len(all_entries)
            for entry in feed.entries:
                dt = _parse_rss_date(entry)
                all_entries.append({
                    "source":       source_name,
                    "headline":     entry.get("title", "").strip(),
                    "url":          entry.get("link", "").strip(),
                    "summary":      entry.get("summary", "") or entry.get("description", "") or "",
                    "dt":           dt,  # may be None
                    "text":         _entry_text(entry),
                })
            print(f"  [rss] {source_name}: {len(all_entries) - count_before} entries")
        except Exception as e:
            print(f"  [warn] RSS fetch failed for {source_name}: {type(e).__name__}: {e}")

    _RSS_CACHE = all_entries
    return all_entries


def _matches_symbol(text: str, short_sym: str) -> bool:
    """Symbol present as standalone token in text (avoid 'BBL' matching 'BBLAMICA')."""
    import re
    pattern = r"(?<![A-Z0-9])" + re.escape(short_sym) + r"(?![A-Z0-9])"
    return re.search(pattern, text) is not None


def fetch_news_rss(symbol: str, hours_back: int = 168) -> list[NewsItem]:
    """
    Filter cached RSS entries for one symbol within the time window
    (RSS feeds are fetched once per pipeline run via _fetch_all_rss)
    """
    short_sym = symbol.replace(".BK", "").strip().upper()
    cutoff = datetime.now(TZ_BKK) - timedelta(hours=hours_back)

    seen_urls: set[str] = set()
    items: list[NewsItem] = []

    for entry in _fetch_all_rss():
        # Time filter (skip entries with unparseable date when filtering — keep if no date)
        if entry["dt"] is not None and entry["dt"] < cutoff:
            continue
        # Symbol filter
        if not _matches_symbol(entry["text"], short_sym):
            continue
        # Dedup by URL
        if entry["url"] in seen_urls:
            continue
        seen_urls.add(entry["url"])

        published_at = (entry["dt"] or datetime.now(TZ_BKK)).isoformat()
        items.append(NewsItem(
            headline=entry["headline"],
            source=entry["source"],
            url=entry["url"],
            published_at=published_at,
        ))

    # Sort newest first
    items.sort(key=lambda x: x.published_at, reverse=True)
    return items


def fetch_news_scrape(symbol: str, max_items: int = 10) -> list[NewsItem]:
    """
    Fallback scraper: Kaohoon search results page (https://www.kaohoon.com/?s=SYM)
    Selectors confirmed against live site: <h2 class="post-title"><a>title</a></h2>
    Returns up to max_items NewsItem; empty list on any failure (never raises).
    """
    short_sym = symbol.replace(".BK", "").strip()
    if not short_sym:
        return []

    import requests
    import html as html_mod
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    url = f"https://www.kaohoon.com/?s={short_sym}"
    items: list[NewsItem] = []

    try:
        time.sleep(SCRAPE_DELAY)
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Confirmed selector (Kaohoon WP theme)
        title_links = soup.select("h2.post-title a") or soup.select("h2 a")

        seen: set[str] = set()
        for a in title_links:
            href = a.get("href", "").strip()
            if not href or href in seen:
                continue
            seen.add(href)
            headline = html_mod.unescape(a.get_text(strip=True)).strip()
            if not headline:
                continue

            # Verify symbol still appears in title (defensive — search may include unrelated)
            if not _matches_symbol(headline, short_sym):
                continue

            # Date — best effort, look for nearby <time> or .post-date sibling
            dt = None
            container = a.find_parent(["article", "div", "li"]) or a.parent
            if container:
                date_el = (
                    container.find("time")
                    or container.find(class_=lambda c: c and "date" in str(c).lower())
                )
                if date_el is not None:
                    raw = date_el.get("datetime") or date_el.get_text(strip=True)
                    try:
                        from dateutil import parser as dt_parser
                        dt = dt_parser.parse(raw)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=TZ_BKK)
                        dt = dt.astimezone(TZ_BKK)
                    except Exception:
                        dt = None

            items.append(NewsItem(
                headline=headline,
                source="Kaohoon (scrape)",
                url=urljoin(url, href),
                published_at=(dt or datetime.now(TZ_BKK)).isoformat(),
            ))
            if len(items) >= max_items:
                break
    except Exception as e:
        print(f"  [warn] Kaohoon scrape failed for {short_sym}: {type(e).__name__}: {e}")

    return items


# ============================================================
# LAYER 2: TECHNICAL ANALYSIS
# ============================================================

def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def calc_macd(close: pd.Series) -> dict[str, pd.Series]:
    ema12 = calc_ema(close, 12)
    ema26 = calc_ema(close, 26)
    macd_line = ema12 - ema26
    signal = calc_ema(macd_line, 9)
    return {"macd": macd_line, "signal": signal, "hist": macd_line - signal}


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift()
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _safe_float(v) -> float:
    """Convert possibly-NaN value to float, returning NaN if conversion fails."""
    try:
        f = float(v)
        if f != f:  # NaN check
            return float("nan")
        return f
    except (TypeError, ValueError):
        return float("nan")


def compute_indicators(df: pd.DataFrame) -> dict[str, float]:
    """
    คำนวณ indicators ทั้งหมด คืนค่าล่าสุด
    ถ้า history สั้นเกิน → indicator ที่คำนวณไม่ได้จะเป็น NaN (เช่น SMA200 ของ IPO ใหม่)
    """
    close = df["close"]
    macd = calc_macd(close)
    n = len(close)

    sma_50 = close.rolling(50).mean().iloc[-1] if n >= 50 else float("nan")
    sma_200 = close.rolling(200).mean().iloc[-1] if n >= 200 else float("nan")

    return {
        "price": _safe_float(close.iloc[-1]),
        "rsi_14": _safe_float(calc_rsi(close).iloc[-1]),
        "ema_12": _safe_float(calc_ema(close, 12).iloc[-1]),
        "ema_26": _safe_float(calc_ema(close, 26).iloc[-1]),
        "macd": _safe_float(macd["macd"].iloc[-1]),
        "macd_signal": _safe_float(macd["signal"].iloc[-1]),
        "macd_hist": _safe_float(macd["hist"].iloc[-1]),
        "sma_50": _safe_float(sma_50),
        "sma_200": _safe_float(sma_200),
        "atr_14": _safe_float(calc_atr(df).iloc[-1]),
        "cdc_zone": "green" if _safe_float(calc_ema(close, 12).iloc[-1]) > _safe_float(calc_ema(close, 26).iloc[-1]) else "red",
        "history_days": n,
    }


def _is_nan(v) -> bool:
    """True if v is NaN or None — both treated as missing."""
    if v is None:
        return True
    return isinstance(v, float) and v != v


def _nan_to_none(d):
    """Recursively replace NaN floats with None so json.dump produces valid JSON."""
    if isinstance(d, dict):
        return {k: _nan_to_none(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_nan_to_none(x) for x in d]
    if isinstance(d, float) and d != d:
        return None
    return d


def normalize_tech_score(ind: dict[str, float]) -> dict[str, float]:
    """
    Normalize indicators เป็น [-1, +1]
    NaN inputs (เช่น SMA200 ของ IPO ใหม่) → คะแนน 0.0 (neutral) เพื่อไม่บิดเบือน
    """
    # RSI score
    rsi = ind["rsi_14"]
    if _is_nan(rsi):    rsi_s = 0.0
    elif rsi < 30:      rsi_s = 0.8
    elif rsi < 50:      rsi_s = 0.2
    elif rsi < 70:      rsi_s = -0.2
    else:               rsi_s = -0.8

    # MACD score
    macd_v, sig_v, hist_v = ind["macd"], ind["macd_signal"], ind["macd_hist"]
    if _is_nan(macd_v) or _is_nan(sig_v) or _is_nan(hist_v):
        macd_s = 0.0
    else:
        macd_above = macd_v > sig_v
        hist_growing = hist_v > 0
        if macd_above and hist_growing:    macd_s = 0.6
        elif macd_above:                    macd_s = 0.2
        elif hist_growing:                  macd_s = -0.2
        else:                                macd_s = -0.6

    # EMA / CDC zone
    ema_s = 0.5 if ind["cdc_zone"] == "green" else -0.5

    # SMA structure — handle NaN (short history)
    price, sma50, sma200 = ind["price"], ind["sma_50"], ind["sma_200"]
    if _is_nan(sma50) and _is_nan(sma200):
        sma_s = 0.0
    elif _is_nan(sma200):
        # Have SMA50 but not SMA200 — partial signal
        sma_s = 0.3 if price > sma50 else -0.3
    elif _is_nan(sma50):
        sma_s = 0.3 if price > sma200 else -0.5
    elif price > sma50 > sma200:
        sma_s = 0.7
    elif price > sma200:
        sma_s = 0.3
    elif sma50 < sma200:
        sma_s = -0.8
    else:
        sma_s = -0.5

    return {"rsi": rsi_s, "macd": macd_s, "ema": ema_s, "sma": sma_s}


def compute_tech_score(scores: dict[str, float], timeframe: str) -> float:
    w = TECH_WEIGHTS[timeframe]
    return sum(scores[k] * w[k] for k in w)


# ============================================================
# LAYER 2B: SENTIMENT ANALYSIS
# ============================================================

# Lazy-loaded sentiment backend
# Returns tuple ("backend_name", callable_or_pipeline) — callable accepts text → returns float in [-1, +1]
_sentiment_pipeline = None
_BACKEND_INIT_LOGGED = False


def _init_wangchanberta():
    """Try to load fine-tuned WangchanBERTa pipeline. Returns callable or None."""
    try:
        from transformers import pipeline as hf_pipeline
        pipe = hf_pipeline(
            "sentiment-analysis",
            model=WANGCHANBERTA_MODEL,
            truncation=True,
            max_length=512,
        )
        # Smoke test
        _ = pipe("ทดสอบ")
        return pipe
    except Exception as e:
        print(f"  [sentiment] wangchanberta unavailable: {type(e).__name__}: {str(e)[:120]}")
        return None


def _init_multilingual():
    """Try multilingual sentiment as a lighter alternative."""
    try:
        from transformers import pipeline as hf_pipeline
        pipe = hf_pipeline(
            "sentiment-analysis",
            model=MULTILINGUAL_MODEL,
            truncation=True,
            max_length=512,
        )
        _ = pipe("ทดสอบ")
        return pipe
    except Exception as e:
        print(f"  [sentiment] multilingual unavailable: {type(e).__name__}: {str(e)[:120]}")
        return None


def get_sentiment_pipeline():
    """
    Lazy-init sentiment backend. Resolution order based on SENTIMENT_BACKEND:
      auto:           wangchanberta → multilingual → keyword
      wangchanberta:  wangchanberta only
      multilingual:   multilingual only
      keyword:        skip ML, always use keyword + lexicon
    Returns ("backend_name", pipeline_or_None)
    """
    global _sentiment_pipeline, _BACKEND_INIT_LOGGED
    if _sentiment_pipeline is not None:
        return _sentiment_pipeline

    backend = SENTIMENT_BACKEND
    pipe = None
    chosen = None

    if backend in ("auto", "wangchanberta"):
        pipe = _init_wangchanberta()
        if pipe is not None:
            chosen = "wangchanberta"

    if pipe is None and backend in ("auto", "multilingual"):
        pipe = _init_multilingual()
        if pipe is not None:
            chosen = "multilingual"

    if pipe is None:
        chosen = "keyword"

    _sentiment_pipeline = (chosen, pipe)
    if not _BACKEND_INIT_LOGGED:
        print(f"  [sentiment] backend = {chosen}")
        _BACKEND_INIT_LOGGED = True
    return _sentiment_pipeline


def _label_to_score(label: str, confidence: float) -> float:
    """Map model label to [-1, +1] using confidence as magnitude."""
    label_lower = label.lower().strip()
    if label_lower in ("pos", "positive", "label_2"):
        return float(confidence)
    if label_lower in ("neg", "negative", "label_0"):
        return -float(confidence)
    # neutral / question / unknown
    return 0.0


def _apply_lexicon_boost(text: str, score: float) -> float:
    """Adjust score by ±LEXICON_BOOST per matched term, capped at ±1.0."""
    boost = 0.0
    for term in THAI_FIN_LEXICON_POS:
        if term in text:
            boost += LEXICON_BOOST
    for term in THAI_FIN_LEXICON_NEG:
        if term in text:
            boost -= LEXICON_BOOST
    return max(-1.0, min(1.0, score + boost))


def _keyword_baseline(text: str) -> float:
    """Original placeholder logic — used when no ML backend available."""
    positive = ["กำไร", "เพิ่ม", "ขึ้น", "บวก", "ดี", "เด่น", "นิวไฮ", "ปันผล"]
    negative = ["ขาดทุน", "ลด", "ลง", "ลบ", "แย่", "ปรับลด", "ผิดนัด", "กดดัน"]
    pos = sum(1 for w in positive if w in text)
    neg = sum(1 for w in negative if w in text)
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def analyze_sentiment(text: str) -> float:
    """
    Sentiment score in [-1, +1].
    Pipeline: backend (ML or keyword) → financial lexicon boost → cap [-1, 1]
    Caches by hash of text so identical headlines aren't reprocessed.
    """
    if not text:
        return 0.0

    cache_key = hash(text)
    if cache_key in _sentiment_cache:
        return _sentiment_cache[cache_key]

    t0 = time.time()
    backend_name, pipe = get_sentiment_pipeline()

    if pipe is not None:
        try:
            # Pipeline returns [{"label": ..., "score": ...}]
            result = pipe(text[:1000])
            if isinstance(result, list):
                result = result[0]
            label = result.get("label", "neutral")
            confidence = float(result.get("score", 0.0))
            base_score = _label_to_score(label, confidence)
        except Exception as e:
            print(f"  [warn] sentiment ML failed for text [{text[:40]}...]: {e}")
            base_score = _keyword_baseline(text)
    else:
        base_score = _keyword_baseline(text)

    final_score = _apply_lexicon_boost(text, base_score)
    elapsed_ms = (time.time() - t0) * 1000

    if elapsed_ms > 100:  # only log slow ones
        print(f"  [sentiment {backend_name} {elapsed_ms:.0f}ms] {final_score:+.2f}  {text[:50]}")

    _sentiment_cache[cache_key] = final_score
    return final_score


def compute_sentiment_score(news: list[NewsItem], ref: datetime) -> float:
    """
    Aggregate sentiment ของข่าวทั้งหมด ด้วย time decay
    half-life = 48 ชั่วโมง → ข่าวอายุ 48 ชม. มีน้ำหนักครึ่งหนึ่งของข่าวล่าสุด
    """
    if not news:
        return 0.0
    
    HALF_LIFE = 48.0
    weighted_sum = 0.0
    weight_total = 0.0
    
    for item in news:
        decay = 0.5 ** (item.hours_ago(ref) / HALF_LIFE)
        weighted_sum += item.sentiment * decay
        weight_total += decay
    
    return weighted_sum / weight_total if weight_total > 0 else 0.0


# ============================================================
# LAYER 2C: COMPOSITE FUSION INDICATOR
# ============================================================

def compute_cci(tech_scores_by_tf: dict[str, float], sent_score: float) -> dict[str, float]:
    """
    คำนวณ CCI สำหรับแต่ละ timeframe
    CCI = W_T × Tech + W_S × Sentiment
    """
    out = {}
    for tf in ("short", "medium", "long"):
        w = CCI_WEIGHTS[tf]
        out[tf] = w["tech"] * tech_scores_by_tf[tf] + w["sent"] * sent_score
    return out


def cci_to_action(cci: float) -> str:
    if cci > 0.6:    return "STRONG_BUY"
    if cci > 0.3:    return "BUY"
    if cci > -0.3:   return "HOLD"
    if cci > -0.6:   return "SELL"
    return "STRONG_SELL"


# ============================================================
# LAYER 3: REASONING ENGINE (LLM Agent)
# ============================================================

PROMPT_TEMPLATE = """\
You are a senior equity research analyst specializing in the Stock Exchange of Thailand (SET).

STOCK: {symbol} ({name}) — Sector: {sector}
CURRENT PRICE: {price} THB ({change_pct}%)

TECHNICAL INDICATORS:
- RSI(14): {rsi_14}
- EMA12 / EMA26: {ema_12} / {ema_26} (Zone: {cdc_zone})
- MACD / Signal: {macd} / {macd_signal}
- SMA50 / SMA200: {sma_50} / {sma_200}
- ATR(14): {atr_14}

COMPOSITE INDICATOR:
- CCI Short-term: {cci_short}
- CCI Medium-term: {cci_medium}
- CCI Long-term: {cci_long}

RECENT NEWS (last 7 days, {news_count} items):
{news_block}

INSTRUCTIONS:
Provide a JSON response with recommendations for THREE timeframes (short_term, medium_term, long_term).
Each must include: action (STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL), confidence (0.0-1.0),
target_price (number), stop_loss (number), reasoning (2-3 sentences in Thai).

Rules:
1. If ATR > 5% of price → downgrade confidence by 0.2
2. If news_count == 0 → cap confidence at 0.5
3. Reasoning MUST reference specific indicator values
4. Output ONLY valid JSON, no preamble or markdown fences

OUTPUT JSON:
"""


def _rule_based_recommendations(stock_summary: dict) -> dict:
    """Fallback ใช้เมื่อ LLM ไม่พร้อม — แปลง CCI เป็น action ตรงๆ"""
    out = {}
    for tf in ("short_term", "medium_term", "long_term"):
        key = tf.replace("_term", "")
        cci_val = stock_summary[f"cci_{key}"]
        action = cci_to_action(cci_val)
        confidence = min(0.5 + abs(cci_val) * 0.5, 0.95)
        price = stock_summary["price"]
        out[tf] = {
            "action": action,
            "confidence": round(confidence, 2),
            "target_price": round(price * (1 + cci_val * 0.1), 2),
            "stop_loss": round(price * (1 - 0.05), 2),
            "reasoning": (f"CCI {key}-term = {cci_val:+.2f}, RSI = {stock_summary['rsi_14']:.1f}, "
                          f"อยู่ในโซน {stock_summary['cdc_zone']} [rule-based fallback]"),
        }
    return out


def _extract_json(text: str) -> str:
    """Strip markdown fences and any prose around the JSON object."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        # Drop the opening fence line
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        text = text.replace("```json", "").replace("```", "").strip()
    # If model added prose before/after, slice between first { and last }
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    return text


def call_llm_agent(stock_summary: dict) -> dict:
    """
    ส่ง stock_summary เข้า Claude → คืน recommendations 3 timeframes
    หาก API/parse ล้มเหลว → fallback เป็น rule-based logic อัตโนมัติ
    Function signature เหมือนเดิม เพื่อไม่กระทบ pipeline ส่วนอื่น
    """
    # 1) Build news_block (max 5 รายการ ล่าสุดสุดก่อน)
    top_news = stock_summary.get("top_news") or []
    if top_news:
        news_block = "\n".join(
            f"- [{(n.get('sentiment', 0.0)):+.2f}] {n.get('headline','')}"
            for n in top_news[:5]
        )
    else:
        news_block = "(no recent news)"

    # 2) Prepare format args (PROMPT_TEMPLATE expects news_block + news_count)
    format_args = {
        **stock_summary,
        "news_block": news_block,
        "news_count": stock_summary.get("news_count", len(top_news)),
    }

    symbol = stock_summary.get("symbol", "?")

    try:
        client = _get_anthropic_client()
        prompt = PROMPT_TEMPLATE.format(**format_args)

        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=LLM_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )

        # response.content is list of content blocks; first one is the text
        raw_text = response.content[0].text
        cleaned = _extract_json(raw_text)
        result = json.loads(cleaned)

        # Sanity: ต้องมีครบ 3 timeframes
        for tf in ("short_term", "medium_term", "long_term"):
            if tf not in result:
                raise ValueError(f"missing key '{tf}' in LLM output")
            for required in ("action", "confidence", "target_price", "stop_loss", "reasoning"):
                if required not in result[tf]:
                    raise ValueError(f"missing '{required}' in {tf}")

        # Rate-limit: หน่วงระหว่าง call ป้องกันโดน throttle เวลา loop หลายหุ้น
        time.sleep(RATE_LIMIT_DELAY)
        return result

    except Exception as e:
        print(f"  [warn] LLM failed for {symbol}: {type(e).__name__}: {e}")
        print(f"         falling back to rule-based recommendations")
        return _rule_based_recommendations(stock_summary)


# ============================================================
# LAYER 4: GUARDRAILS
# ============================================================

def apply_guardrails(stock_signal: dict) -> dict:
    """
    ตรวจสอบ + ปรับ signal ก่อนส่งออก
    """
    ind = stock_signal["indicators"]
    flags = []
    
    # 1. Volatility check (ATR > 5% ของราคา → ตลาดผันผวนสูง)
    atr_pct = (ind["atr_14"] / ind["price"]) * 100
    if atr_pct > 5:
        flags.append("HIGH_VOLATILITY")
        for tf in stock_signal["recommendations"].values():
            tf["confidence"] = max(0.0, tf["confidence"] - 0.2)
    
    # 2. ไม่มีข่าวเลย → cap confidence
    if stock_signal["news_count_7d"] == 0:
        flags.append("NO_NEWS_DATA")
        for tf in stock_signal["recommendations"].values():
            tf["confidence"] = min(tf["confidence"], 0.5)
    
    # 3. Death cross detection
    if ind["sma_50"] < ind["sma_200"]:
        flags.append("DEATH_CROSS_REGIME")
    
    # 4. Conflict check: tech vs sentiment ต่างกันมาก → force HOLD
    if abs(stock_signal["technical_score"] - stock_signal["sentiment_score"]) > 0.7:
        flags.append("SIGNAL_CONFLICT")
        for tf in stock_signal["recommendations"].values():
            if tf["action"] not in ("HOLD",):
                tf["action"] = "HOLD"
                tf["confidence"] = min(tf["confidence"], 0.4)
                tf["reasoning"] += " [Guardrail: signal conflict between technical and sentiment]"

    stock_signal["risk_flags"] = flags
    return stock_signal


# ============================================================
# MAIN PIPELINE
# ============================================================

def analyze_stock(stock: StockData, ref_time: datetime) -> dict:
    """วิเคราะห์หุ้น 1 ตัว → ได้ signal dict"""
    indicators = compute_indicators(stock.df)

    # Sentiment
    for n in stock.news:
        n.sentiment = analyze_sentiment(n.headline)
    sent_score = compute_sentiment_score(stock.news, ref_time)

    # Tech scores
    norm = normalize_tech_score(indicators)
    tech_scores = {tf: compute_tech_score(norm, tf) for tf in ("short", "medium", "long")}

    # CCI
    cci = compute_cci(tech_scores, sent_score)

    # Compute pct change (last bar vs prev)
    close = stock.df["close"]
    change_pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else 0.0

    # Build summary for LLM
    summary = {
        "symbol": stock.symbol,
        "name": stock.name,
        "sector": stock.sector,
        "change_pct": round(change_pct, 2),
        **{k: (round(v, 4) if isinstance(v, float) and v == v else v) for k, v in indicators.items()},
        "cci_short": round(cci["short"], 3),
        "cci_medium": round(cci["medium"], 3),
        "cci_long": round(cci["long"], 3),
        "news_count": len(stock.news),
        "top_news": [asdict(n) for n in sorted(stock.news,
                     key=lambda x: x.published_at, reverse=True)[:5]],
    }

    recommendations = call_llm_agent(summary)

    signal = {
        "symbol": stock.symbol,
        "name": stock.name,
        "sector": stock.sector,
        "price": indicators["price"],
        "change_pct": round(change_pct, 2),
        "indicators": indicators,
        "technical_score": round(sum(tech_scores.values()) / 3, 3),
        "sentiment_score": round(sent_score, 3),
        "cci_short": round(cci["short"], 3),
        "cci_medium": round(cci["medium"], 3),
        "cci_long": round(cci["long"], 3),
        "news_count_7d": len(stock.news),
        "top_news": [asdict(n) for n in sorted(stock.news,
                     key=lambda x: x.published_at, reverse=True)[:5]],
        "recommendations": recommendations,
        "risk_flags": [],
    }

    return apply_guardrails(signal)


def run_pipeline(mode: str):
    """Main entry point"""
    ref_time = datetime.now(TZ_BKK)
    print(f"[{ref_time}] Starting pipeline (mode={mode})")

    # Load watchlist
    with open(DATA_DIR / "watchlist.json", encoding="utf-8") as f:
        watchlist = json.load(f)

    signals = []
    for stock_meta in watchlist["stocks"]:
        try:
            print(f"  Processing {stock_meta['symbol']}...")
            df = fetch_price_data(stock_meta["symbol"])
            news = fetch_news_rss(stock_meta["symbol"])
            # Fallback: if RSS yielded zero, try scraping Kaohoon search
            if not news:
                news = fetch_news_scrape(stock_meta["symbol"])

            stock = StockData(
                symbol=stock_meta["symbol"],
                name=stock_meta["name"],
                sector=stock_meta["sector"],
                df=df,
                news=news,
            )
            signal = analyze_stock(stock, ref_time)
            signals.append(signal)
        except Exception as e:
            print(f"  [ERROR] {stock_meta['symbol']}: {type(e).__name__}: {e}")

    # Build output
    output = {
        "generated_at": ref_time.isoformat(),
        "run_type": mode,
        "market_summary": {
            "set_index": 0.0,  # TODO: fetch SET index
            "set_change_pct": 0.0,
            "market_sentiment": "neutral",
            "volatility_regime": "normal",
        },
        "signals": signals,
    }

    # Save current
    with open(DATA_DIR / "signals.json", "w", encoding="utf-8") as f:
        json.dump(_nan_to_none(output), f, ensure_ascii=False, indent=2)

    # Save snapshot
    snap_name = ref_time.strftime("%Y%m%d_%H%M") + ".json"
    with open(SNAPSHOT_DIR / snap_name, "w", encoding="utf-8") as f:
        json.dump(_nan_to_none(output), f, ensure_ascii=False, indent=2)

    print(f"[done] {len(signals)} signals written to signals.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["pre_market", "post_close"], required=True)
    args = parser.parse_args()
    run_pipeline(args.mode)
