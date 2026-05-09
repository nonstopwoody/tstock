# Thai Stock AI Advisor

ระบบวิเคราะห์หุ้นไทยอัตโนมัติด้วย AI พร้อม Dashboard แสดงผล
ทำงานวันละ 2 ครั้ง (เช้า 08:30 ก่อนตลาดเปิด • เย็น 17:30 หลังปิดตลาด)

## โครงสร้างไฟล์

```
thai-stock-ai/
├── ARCHITECTURE.md       ← เอกสารสถาปัตยกรรมแบบเต็ม (อ่านก่อน!)
├── dashboard.html        ← Dashboard หน้าจอหลัก เปิดดูได้ทันที
├── pipeline.py           ← Backend Python สำหรับ fetch + analyze
├── README.md             ← ไฟล์นี้
└── data/
    ├── watchlist.json    ← รายชื่อหุ้นที่จะวิเคราะห์
    ├── signals.json      ← ผลวิเคราะห์ล่าสุด (Dashboard อ่านจากที่นี่)
    └── snapshots/        ← เก็บ history แต่ละรอบ
```

## วิธีทดสอบ Dashboard ทันที (ใช้ mock data)

```bash
# วิธี 1: เปิดไฟล์โดยตรง (อาจติด CORS เพราะโหลด JSON)
# วิธี 2 (แนะนำ): รัน HTTP server ในโฟลเดอร์
cd thai-stock-ai
python3 -m http.server 8080

# เปิดเบราว์เซอร์ไปที่
# http://localhost:8080/dashboard.html
```

## วิธีต่อยอดให้ใช้งานจริง (Roadmap)

### Phase 1 — MVP (1-2 วัน)

```bash
# 1. ติดตั้ง dependencies
pip install yfinance pandas numpy feedparser beautifulsoup4 requests anthropic

# 2. ตั้ง API key
export ANTHROPIC_API_KEY="sk-ant-..."

# 3. ทดสอบ pipeline
python pipeline.py --mode pre_market

# 4. เปิด dashboard.html ผ่าน HTTP server
```

ใน `pipeline.py` ส่วนที่ต้องเปิดใช้งาน:
- ใน `call_llm_agent()` ลบคอมเมนต์ส่วน `import anthropic` และ API call

### Phase 2 — Sentiment Analysis ภาษาไทย (2-3 วัน)

```bash
pip install pythainlp transformers torch
```

ใน `pipeline.py` แก้ฟังก์ชัน `analyze_sentiment()` ให้ใช้ WangchanBERTa จริง

### Phase 3 — Automation

#### Linux/Mac (cron)
```bash
crontab -e
# เพิ่ม 2 บรรทัดนี้
30 8  * * 1-5  cd /path/to/thai-stock-ai && python pipeline.py --mode pre_market
30 17 * * 1-5  cd /path/to/thai-stock-ai && python pipeline.py --mode post_close
```

#### Windows (Task Scheduler)
1. เปิด Task Scheduler → Create Basic Task
2. Trigger: Daily 08:30, Recur every weekday
3. Action: Start Program
   - Program: `python.exe`
   - Arguments: `C:\path\to\pipeline.py --mode pre_market`
4. ทำซ้ำสำหรับ 17:30 / `--mode post_close`

#### Cloud (แนะนำสำหรับ production)
- **GitHub Actions** + push signals.json กลับเข้า repo → host ผ่าน GitHub Pages
- **Cloudflare Workers** + KV storage + Cron Triggers
- **AWS Lambda** + EventBridge schedule

## การปรับแต่งระบบ

### เพิ่ม/ลดหุ้นใน watchlist
แก้ `data/watchlist.json` — ระวังต้องใส่ suffix `.BK` สำหรับ Yahoo Finance

### ปรับน้ำหนัก CCI
ใน `pipeline.py` ที่ตัวแปร `CCI_WEIGHTS` และ `TECH_WEIGHTS`

### เปลี่ยน LLM
- Claude: ใช้ `anthropic` SDK (ในโค้ดอยู่แล้ว)
- OpenAI: เปลี่ยน import เป็น `openai` และ adapt format
- Local LLM: ใช้ `ollama` + Llama 3 70B (ฟรี แต่ต้องมี GPU)

## หมายเหตุสำคัญ

⚠️ **Disclaimer**: ระบบนี้เป็นเครื่องมือช่วยตัดสินใจส่วนตัวเท่านั้น ไม่ใช่คำแนะนำการลงทุนเชิงพาณิชย์ ผู้ใช้รับผิดชอบในการตัดสินใจของตนเอง

✅ **Best practices**:
- Backtest ก่อนใช้จริง (เก็บ history.json ไว้ตรวจ precision/recall)
- เคารพ robots.txt เมื่อ scrape ข่าว
- ใส่ delay 1-2 วินาทีระหว่าง requests
- อย่าใช้ระบบเดียวเป็น single source of truth — combined กับ judgment ของตัวเอง

## License
สำหรับใช้ส่วนตัวเท่านั้น
