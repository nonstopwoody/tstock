# GitHub All-in-One Setup Guide

Setup GitHub Actions (auto-run pipeline) + GitHub Pages (host dashboard)
ใช้เวลาประมาณ 30 นาทีครั้งแรก หลังจากนั้น auto ทุกวันเอง

---

## Step 1 — สมัคร GitHub (ถ้ายังไม่มี)

1. เปิด https://github.com/signup
2. ใช้อีเมล `woodyme@gmail.com` (หรืออีเมลอื่น) ตั้ง username เช่น `woodyme`
3. Verify อีเมล
4. ตอบคำถาม onboarding (ตอบอะไรก็ได้ ข้ามได้)

> **Username** ที่ตั้งจะเป็นส่วนหนึ่งของ URL ภายหลัง: `<username>.github.io/<repo>` เลือกชื่อสั้นๆ จำง่าย

---

## Step 2 — สร้าง Repo ใหม่

1. คลิก `+` มุมขวาบน → **New repository**
2. กรอก:
   - **Repository name**: `tstock`
   - **Description**: `Thai Stock AI Advisor`
   - **Public** (ต้อง public เพราะ free GitHub Pages ต้องการ — code เห็นได้ แต่ API key ใน Secrets ไม่เห็น)
   - ✅ ติ๊ก **Add a README file**
3. กด **Create repository**

---

## Step 3 — อัปไฟล์เข้า Repo

ที่หน้า repo คลิก **Add file → Upload files** แล้วลากไฟล์เข้าไป

**ไฟล์ที่ต้องอัป** (ลากจาก `D:\APPS\TStock\` ทีละบาทช์):

**บาทช์ 1 — root files:**
- `dashboard.html`
- `pipeline.py`
- `requirements.txt`
- `.env.example`
- `.gitignore`
- `ARCHITECTURE.md` (optional แต่ดี เก็บเป็นเอกสาร)
- `README.md`

**บาทช์ 2 — workflow folder:**
- ลาก folder `.github` ทั้ง folder (ภายในมี `workflows/run-pipeline.yml`)

**บาทช์ 3 — data folder:**
- ลาก folder `data` ทั้ง folder (ภายในมี `watchlist.json`, `signals.json`)

แต่ละบาทช์ พิมพ์ commit message สั้นๆ เช่น "initial upload" แล้วกด **Commit changes**

> ⚠️ **ห้ามอัป** ไฟล์เหล่านี้ — ลบออกจากรายการก่อนกด Upload:
> - `.env` (มี API key จริง — ห้ามขึ้น public!)
> - `venv/` folder ทั้งโฟลเดอร์
> - `data/cache/` folder
> - `data/snapshots/` folder
> - `*.zip` files
> - `__pycache__/` folder

---

## Step 4 — ตั้ง Secret (ใส่ API Key ปลอดภัย)

1. ที่ repo คลิกแท็บ **Settings** (มุมขวาบน)
2. เมนูซ้าย **Secrets and variables → Actions**
3. กดปุ่มเขียว **New repository secret**
4. กรอก:
   - **Name**: `ANTHROPIC_API_KEY` (พิมพ์ตรงๆ ตัวพิมพ์ใหญ่)
   - **Secret**: paste API key จริง (`sk-ant-api03-...`)
5. กด **Add secret**

> Secret encrypted ด้วย libsodium — ดูซ้ำไม่ได้แม้แต่คุณเอง update ได้อย่างเดียว ปลอดภัย

---

## Step 5 — เปิด GitHub Pages (host dashboard)

1. ยังที่หน้า **Settings** เมนูซ้ายเลือก **Pages**
2. ใต้ **Source** เลือก **Deploy from a branch**
3. **Branch**: `main` / Folder: `/ (root)`
4. กด **Save**
5. รอ ~1 นาที จะเห็นข้อความเขียว `Your site is live at https://<username>.github.io/tstock/`

---

## Step 6 — รัน workflow ครั้งแรก (manual trigger)

1. คลิกแท็บ **Actions** (เมนูบน)
2. ซ้ายมือเลือก **Daily Stock Analysis**
3. กลางขวามีปุ่มสีเทา **Run workflow** → คลิก → ปุ่มเขียว **Run workflow** ในเมนูที่เด้งออกมา
4. รอ ~3-5 นาที — จะเห็น run สีเหลือง (กำลังทำ) → เขียว (สำเร็จ) หรือแดง (พัง)
5. คลิกเข้าไปดู log แต่ละ step ได้

ถ้าสำเร็จ:
- จะเห็น commit ใหม่ใน repo (auto: pipeline run at...)
- ไฟล์ `data/signals.json` อัปเดตแล้ว

---

## Step 7 — เปิดดู dashboard

URL: `https://<username>.github.io/tstock/dashboard.html`

(แทน `<username>` ด้วย username ที่ตั้งใน Step 1)

bookmark บนมือถือ → จะมีหน้าเดียวคุ้นเคย โหลดเร็วเพราะ GitHub Pages CDN ทั่วโลก

---

## หลังจากนี้ มันจะทำงานเองอัตโนมัติ

ทุกวันจันทร์-ศุกร์ เวลา 11:00 ไทย:
1. GitHub Actions ติ๊กอัตโนมัติตาม cron
2. รัน pipeline.py บน server cloud ของ GitHub
3. สร้าง signals.json ใหม่ → push commit
4. GitHub Pages auto-deploy ภายใน 30 วินาทีหลัง commit
5. ใครเปิด URL หลัง 11:05 น. = เห็นข้อมูลใหม่

ไม่ต้องเปิด PC ที่บ้าน ไม่ต้องทำอะไร ดูจากมือถือนอกบ้านได้เลย

---

## วิธีหยุด workflow หลังกลับจากต่างประเทศ (22/5)

ตัวเลือก 1 — ปิดชั่วคราว:
- Settings → Actions → General
- เลื่อนลงสุด **Disable Actions** → กด

ตัวเลือก 2 — หยุดเฉพาะ workflow นี้:
- Actions tab → Daily Stock Analysis
- ขวามือคลิก `...` → Disable workflow

ตัวเลือก 3 — ลบ workflow file:
- ไปที่ `.github/workflows/run-pipeline.yml` ในเวบ
- คลิก 🗑️ delete

---

## Tip การ debug ถ้ารัน workflow ครั้งแรกแดง

คลิกเข้า run ที่แดง → ดู step ที่แดง → อ่าน error message
- `ANTHROPIC_API_KEY missing or placeholder` → กลับไป Step 4 ตรวจชื่อ Secret
- `ModuleNotFoundError` → requirements.txt ไม่ครบ
- `Permission denied` → permissions: contents: write ใน YAML ขาดหาย
- API quota → ตรวจ console.anthropic.com Billing

ส่ง screenshot log มาให้ดูได้ครับ
