# Thai Stock AI Advisor — System Architecture

ระบบวิเคราะห์และแนะนำหุ้นไทยอัตโนมัติด้วย AI พร้อม Dashboard แสดงผล
อัปเดตวันละ 2 ครั้ง (เช้า 08:30 ก่อนตลาดเปิด, เย็น 17:30 หลังตลาดปิด)

---

## 1. ภาพรวมสถาปัตยกรรม (High-Level Architecture)

```
┌─────────────────────────────────────────────────────────────────┐
│                    SCHEDULER (Cron / Task Scheduler)            │
│              08:30 น. (Pre-market)  •  17:30 น. (Post-close)   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1: DATA PERCEPTION  (ชั้นรับข้อมูล)                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ Price Fetcher│  │ News Scraper │  │ RSS Parser   │           │
│  │ yfinance/    │  │ Kaohoon/     │  │ Settrade RSS │           │
│  │ Settrade API │  │ Infoquest/   │  │ feedparser   │           │
│  │              │  │ SET news     │  │              │           │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘           │
│         └─────────────────┼────────────────┘                    │
│                           ▼                                     │
│                   raw_data.json                                 │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2: ANALYSIS ENGINE  (ชั้นประมวลผล)                       │
│  ┌─────────────────────────┐  ┌────────────────────────────┐    │
│  │ Technical Analyzer      │  │ Sentiment Analyzer         │    │
│  │ • RSI(14)               │  │ • PyThaiNLP tokenize       │    │
│  │ • EMA(12,26)            │  │ • WangchanBERTa classify   │    │
│  │ • MACD                  │  │ • Sentiment Score [-1,+1]  │    │
│  │ • SMA(50,200)           │  │ • Time decay weighting     │    │
│  │ • CDC Action Zone       │  │                            │    │
│  │ • ATR (volatility)      │  │                            │    │
│  └────────────┬────────────┘  └─────────────┬──────────────┘    │
│               │                              │                  │
│               └──────────────┬───────────────┘                  │
│                              ▼                                  │
│            COMPOSITE FUSION INDICATOR (CCI)                     │
│         CCI = W_T × Tech_Score + W_S × Sent_Score               │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3: REASONING ENGINE  (ชั้นใช้เหตุผล - LLM Agent)         │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  Claude / GPT-4 Agent with structured prompt           │     │
│  │  Input: CCI score + raw indicators + top 5 news        │     │
│  │  Output: {action, confidence, reasoning, risk_level}   │     │
│  │  for each timeframe (short/medium/long term)           │     │
│  └────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 4: GUARDRAIL & EXECUTION  (ชั้นป้องกันความเสี่ยง)        │
│  • ATR check → ถ้าผันผวนสูงผิดปกติ → force "HOLD"               │
│  • News conflict check → ถ้าข่าวขัดแย้ง → ลด confidence         │
│  • Rate limit / sanity check                                    │
│  • Output: signals.json + history.json                          │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 5: PRESENTATION  (Dashboard - HTML)                      │
│  • Auto-load signals.json on page load                          │
│  • Filter by timeframe / action / sector                        │
│  • Show reasoning + supporting evidence                         │
│  • History chart of past signals                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. โครงสร้างข้อมูล (Data Schema)

### 2.1 Watchlist (`watchlist.json`)
รายชื่อหุ้นที่จะวิเคราะห์ — เริ่มจาก SET50

```json
{
  "stocks": [
    { "symbol": "PTT.BK",  "name": "ปตท.",          "sector": "Energy" },
    { "symbol": "AOT.BK",  "name": "ท่าอากาศยานไทย", "sector": "Transport" },
    { "symbol": "CPALL.BK","name": "ซีพี ออลล์",    "sector": "Commerce" }
  ],
  "last_updated": "2026-05-09"
}
```

### 2.2 Output Signal (`signals.json`)
ไฟล์หลักที่ Dashboard ใช้ — สร้างใหม่ทุกๆ การ run

```json
{
  "generated_at": "2026-05-09T17:30:00+07:00",
  "run_type": "post_close",
  "market_summary": {
    "set_index": 1387.42,
    "set_change_pct": -0.62,
    "market_sentiment": "neutral",
    "volatility_regime": "normal"
  },
  "signals": [
    {
      "symbol": "PTT.BK",
      "name": "ปตท.",
      "sector": "Energy",
      "price": 32.50,
      "change_pct": 1.56,
      "indicators": {
        "rsi_14": 58.3,
        "ema_12": 32.18,
        "ema_26": 31.95,
        "macd": 0.23,
        "macd_signal": 0.18,
        "sma_50": 31.40,
        "sma_200": 30.85,
        "atr_14": 0.42,
        "cdc_zone": "green"
      },
      "technical_score": 0.42,
      "sentiment_score": 0.31,
      "cci_short": 0.35,
      "cci_medium": 0.39,
      "cci_long": 0.36,
      "news_count_7d": 12,
      "top_news": [
        {
          "headline": "ปตท. รายงานกำไร Q1 เพิ่มขึ้น 18% YoY",
          "source": "Kaohoon",
          "url": "https://...",
          "published_at": "2026-05-08T09:15:00+07:00",
          "sentiment": 0.78
        }
      ],
      "recommendations": {
        "short_term": {
          "action": "BUY",
          "confidence": 0.72,
          "target_price": 33.50,
          "stop_loss": 31.80,
          "reasoning": "RSI ที่ 58 อยู่ในโซนเป็นกลาง MACD ตัดขึ้นเหนือ Signal line ในวันนี้ ประกอบกับข่าวกำไร Q1 เป็นบวก คาดเป้าหมายระยะ 1-2 สัปดาห์"
        },
        "medium_term": {
          "action": "BUY",
          "confidence": 0.68,
          "target_price": 35.00,
          "stop_loss": 30.50,
          "reasoning": "อยู่ใน CDC Green Zone (EMA12 > EMA26) แนวโน้มขาขึ้น ราคาน้ำมัน spread เป็นบวก"
        },
        "long_term": {
          "action": "HOLD",
          "confidence": 0.55,
          "target_price": 38.00,
          "stop_loss": 28.00,
          "reasoning": "ยืนเหนือ SMA200 (30.85) แต่ยังไม่เกิด Golden Cross ระหว่าง SMA50/SMA200 รอสะสม"
        }
      },
      "risk_flags": []
    }
  ]
}
```

### 2.3 History (`history.json`)
เก็บ signals ย้อนหลังเพื่อตรวจสอบความแม่นยำ — append-only

```json
{
  "runs": [
    {
      "run_id": "20260509_1730",
      "generated_at": "2026-05-09T17:30:00+07:00",
      "snapshot_path": "snapshots/20260509_1730.json"
    }
  ]
}
```

---

## 3. แหล่งข้อมูล (Data Sources)

### 3.1 ราคา/ปริมาณ (Quantitative)

| แหล่ง | API/Library | ความถี่ | ฟรี | หมายเหตุ |
|---|---|---|---|---|
| Yahoo Finance | `yfinance` | EOD + delayed intraday | ✅ | ใช้ `.BK` suffix เช่น `PTT.BK` |
| Settrade Open API | `settrade-v2` SDK | Real-time + EOD | ✅ Sandbox | ต้องสมัคร dev account |
| EODHD | REST API | EOD | ✅ Free tier 20 calls/day | ตลาด BK รองรับ |
| InvestPy / pandas-datareader | Python lib | EOD | ✅ | Backup source |

**คำแนะนำ:** ใช้ `yfinance` เป็นหลัก, `Settrade Sandbox` เป็น secondary (verification)

### 3.2 ข่าว (Qualitative)

| แหล่ง | วิธีดึง | ความถี่อัปเดต | คุณภาพข่าวการเงินไทย |
|---|---|---|---|
| Settrade RSS | `feedparser` | ทุก 15 นาที | ⭐⭐⭐⭐⭐ IAA Consensus |
| SET News (set.or.th) | RSS / scrape | ทุก 30 นาที | ⭐⭐⭐⭐⭐ ทางการ |
| Kaohoon (ข่าวหุ้น) | `BeautifulSoup` | ทุก 30 นาที | ⭐⭐⭐⭐ |
| Infoquest | scrape | ทุก 30 นาที | ⭐⭐⭐⭐ |
| ThunHoon | scrape | ทุก 1 ชม. | ⭐⭐⭐ |

**ลำดับความสำคัญ:** RSS feeds มาก่อนเสมอ (low latency, ไม่โดน block) → fallback เป็น scraping เฉพาะกรณีข่าวสำคัญ

### 3.3 เครื่องมือวิเคราะห์ภาษาไทย

| เครื่องมือ | บทบาท | จุดแข็ง |
|---|---|---|
| **PyThaiNLP** (`newmm` engine) | Tokenize + clean | ตัดคำไทย maximum matching ได้ดี |
| **WangchanBERTa** | Sentiment classification | ฝึกบนคลังข้อมูลไทยขนาดใหญ่ accuracy >0.92 |
| **Wisesight Sentiment Corpus** | Dataset reference | 26,000 ข้อความ ใช้ fine-tune ได้ |
| **FinBERT** (English) | Sentiment สำหรับข่าว Eng | ใช้กรณีข่าวต่างประเทศที่กระทบหุ้นไทย |

---

## 4. สูตรการคำนวณ Indicators (สำคัญ!)

### 4.1 Technical Indicators

```python
# RSI(14)
delta = close.diff()
gain = delta.where(delta > 0, 0).rolling(14).mean()
loss = -delta.where(delta < 0, 0).rolling(14).mean()
rs = gain / loss
rsi = 100 - (100 / (1 + rs))

# EMA
ema = close.ewm(span=period, adjust=False).mean()

# MACD
macd_line = ema(close, 12) - ema(close, 26)
signal_line = ema(macd_line, 9)
macd_hist = macd_line - signal_line

# SMA
sma = close.rolling(period).mean()

# CDC Action Zone
cdc_zone = 'green' if ema(close,12) > ema(close,26) else 'red'

# ATR(14) - สำหรับ volatility guardrail
tr = max(high-low, abs(high-close.shift()), abs(low-close.shift()))
atr = tr.rolling(14).mean()
```

### 4.2 Technical Score (Normalize ทุก indicator เป็น [-1, +1])

```
RSI score:
  RSI < 30  → +0.8 (oversold = buy signal)
  RSI 30-50 → +0.2
  RSI 50-70 → -0.2
  RSI > 70  → -0.8 (overbought = sell)

MACD score:
  macd > signal AND macd_hist increasing → +0.6
  macd > signal AND macd_hist decreasing → +0.2
  macd < signal AND macd_hist increasing → -0.2
  macd < signal AND macd_hist decreasing → -0.6

EMA Cross (CDC):
  EMA12 > EMA26 (green zone)  → +0.5
  EMA12 < EMA26 (red zone)    → -0.5

SMA Long-term:
  Price > SMA50 > SMA200 (perfect uptrend) → +0.7
  Price > SMA200 only                     → +0.3
  Price < SMA200                          → -0.5
  SMA50 < SMA200 (death cross)            → -0.8

Tech_Score = weighted_avg([RSI, MACD, EMA, SMA])
weights แตกต่างตาม timeframe (ดู section 5)
```

### 4.3 Sentiment Score with Time Decay

```python
# decay function: ข่าวเก่ามีน้ำหนักลดลง
def time_decay(hours_ago, half_life=48):
    return 0.5 ** (hours_ago / half_life)

sent_score = sum(news_sentiment[i] * time_decay(hours_ago[i]) 
                 for i in news) / sum(time_decay(hours_ago[i]) for i in news)
```

### 4.4 Composite Custom Indicator (CCI)

```
CCI = W_T × Tech_Score + W_S × Sent_Score

ค่า W_T, W_S แตกต่างตามกรอบเวลา:

| Timeframe | W_T | W_S | เหตุผล                                       |
|-----------|-----|-----|---------------------------------------------|
| Short     | 0.4 | 0.6 | ข่าวฉับพลันมีผลเร็วกว่าค่าเฉลี่ย              |
| Medium    | 0.5 | 0.5 | สมดุลทั้งสองมิติ                              |
| Long      | 0.7 | 0.3 | โครงสร้างพื้นฐานสำคัญกว่าข่าวสารระยะสั้น |

การแปลงเป็นคำแนะนำ:
  CCI > +0.6  → STRONG BUY
  +0.3 to +0.6 → BUY
  -0.3 to +0.3 → HOLD
  -0.6 to -0.3 → SELL
  CCI < -0.6  → STRONG SELL
```

### 4.5 Indicator Weights ต่อ Timeframe

```
Short-term Tech_Score weights:  RSI(0.4), MACD(0.3), EMA(0.2), SMA(0.1)
Medium-term Tech_Score weights: RSI(0.2), MACD(0.3), EMA(0.3), SMA(0.2)
Long-term Tech_Score weights:   RSI(0.1), MACD(0.2), EMA(0.2), SMA(0.5)
```

---

## 5. Prompt Template สำหรับ AI Agent (Reasoning Layer)

ใช้กับ Claude API หรือ OpenAI API

```
You are a senior equity research analyst specializing in the Stock Exchange of Thailand (SET).
Your task is to provide trading recommendations based on quantitative and qualitative data.

STOCK: {symbol} ({name}) — Sector: {sector}
CURRENT PRICE: {price} THB ({change_pct}%)

TECHNICAL INDICATORS:
- RSI(14): {rsi}
- EMA12 / EMA26: {ema12} / {ema26} (Zone: {cdc_zone})
- MACD / Signal: {macd} / {macd_signal}
- SMA50 / SMA200: {sma50} / {sma200}
- ATR(14): {atr}

COMPOSITE INDICATOR:
- CCI Short-term: {cci_short}
- CCI Medium-term: {cci_medium}
- CCI Long-term: {cci_long}

RECENT NEWS (last 7 days, {news_count} items):
{top_news_with_sentiment}

MARKET CONTEXT:
- SET Index: {set_index} ({set_change_pct}%)
- Volatility regime: {volatility_regime}

INSTRUCTIONS:
Provide a JSON response with recommendations for THREE timeframes.
Each must include: action (STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL),
confidence (0.0-1.0), target_price, stop_loss, and reasoning (2-3 sentences in Thai).

Rules:
1. If ATR > historical 90th percentile → downgrade confidence by 0.2
2. If news_count == 0 in last 7 days → cap confidence at 0.5
3. If sentiment and technical scores conflict by > 0.7 → action must be HOLD
4. Reasoning must reference specific indicator values, not vague claims
5. Output ONLY valid JSON, no preamble

OUTPUT JSON SCHEMA:
{
  "short_term": { "action": "...", "confidence": 0.0, "target_price": 0.0, 
                  "stop_loss": 0.0, "reasoning": "..." },
  "medium_term": { ... },
  "long_term": { ... }
}
```

---

## 6. Scheduler & Automation

### 6.1 Cron Schedule (Linux/Mac)

```bash
# วันละ 2 ครั้ง วันจันทร์-ศุกร์ (ตลาดเปิด)
30 8  * * 1-5  cd /path/to/project && python pipeline.py --mode pre_market
30 17 * * 1-5  cd /path/to/project && python pipeline.py --mode post_close
```

### 6.2 Windows Task Scheduler (สำหรับ Woody บน Windows 11)

สร้าง 2 task:
1. **Pre-market run** — Trigger: Daily 08:30, Days: Mon-Fri
2. **Post-close run** — Trigger: Daily 17:30, Days: Mon-Fri

Action: `python.exe C:\path\to\pipeline.py --mode <mode>`

### 6.3 Cloud Alternative (แนะนำ)

- **GitHub Actions** (ฟรี 2,000 นาที/เดือน) — เขียน workflow YAML
- **Cloudflare Workers + Cron Triggers** (ฟรี 100k requests/day)
- **AWS Lambda + EventBridge** (ฟรีในระดับใช้งานส่วนตัว)

---

## 7. Tech Stack สรุป

```
Backend (Python):
├── yfinance, pandas, numpy        # data + math
├── pythainlp                      # Thai NLP
├── transformers (huggingface)     # WangchanBERTa
├── feedparser, requests, bs4      # news fetch
├── selenium (optional)            # dynamic sites
├── anthropic / openai             # LLM
└── apscheduler / cron             # automation

Frontend (Static HTML):
├── Vanilla JS (ไม่ต้อง build)
├── Tailwind CSS via CDN
├── Chart.js (line/bar charts)
└── Lucide Icons

Storage:
├── signals.json         # ปัจจุบัน (อ่านโดย dashboard)
├── history/*.json       # snapshots ย้อนหลัง
└── watchlist.json       # configuration
```

---

## 8. Roadmap การพัฒนา (แนะนำลำดับ)

| Phase | งาน | เวลาประมาณ |
|---|---|---|
| **1. MVP** | Price fetcher + Tech indicators + ส่งเข้า Claude API + Dashboard แบบอ่าน JSON | 1-2 วัน |
| **2. News** | RSS scraping + sentiment ด้วย WangchanBERTa + เพิ่ม CCI | 2-3 วัน |
| **3. Automation** | Cron/Task Scheduler + history tracking + email/LINE notification | 1 วัน |
| **4. Refinement** | Backtest + ปรับ weights + เพิ่ม guardrails | ต่อเนื่อง |
| **5. Expansion** | เพิ่มหุ้นนอก SET50, multi-agent (CrewAI/LangGraph), portfolio simulation | ระยะยาว |

---

## 9. ค่าใช้จ่ายโดยประมาณ (รายเดือน)

| รายการ | ค่าใช้จ่าย |
|---|---|
| Claude API (Sonnet) — สมมติ 50 หุ้น × 2 รอบ × 30 วัน × ~3,000 tokens/call | ~$15-25 |
| Cloud hosting (Cloudflare/GitHub Actions) | ฟรี |
| ข้อมูลราคา (yfinance) | ฟรี |
| Domain name (optional) | $1-2 |
| **รวม** | **~$15-30/เดือน** |

ถ้าใช้ local LLM (Llama 3 70B) แทน Claude → ฟรี (แต่ต้องมี GPU)

---

## 10. ข้อควรระวังและจริยธรรม

1. **ไม่ใช่คำแนะนำการลงทุน** — ระบบนี้เป็น decision support tool ใช้ส่วนตัว
2. **Backtest ก่อนใช้จริง** — อย่าเชื่อ AI 100% โดยไม่มีหลักฐานสถิติ
3. **เก็บ history** — เพื่อตรวจสอบความแม่นยำ (precision/recall)
4. **Rate limit ข่าวที่ scrape** — เคารพ robots.txt และใช้ delay
5. **ไม่แชร์ output** — เพราะอาจถูกตีความเป็น "การให้คำแนะนำการลงทุน" (ต้องมีใบอนุญาต)
6. **Hallucination check** — ทุก reasoning ที่ AI ให้ต้องอ้างอิงตัวเลขจริง guardrail layer ทำหน้าที่นี้
