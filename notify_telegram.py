"""
Send Telegram notification summarizing latest signals.json.
Called from GitHub Actions workflow after pipeline + commit step.

Required env vars:
  TELEGRAM_BOT_TOKEN  - from @BotFather
  TELEGRAM_CHAT_ID    - your personal chat id (from @userinfobot)
  DASHBOARD_URL       - (optional) link appended at end of message
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests


CONF_MIN = 0.7  # only mention stocks with confidence >= this in short_term


def fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d %b %Y  %H:%M")
    except Exception:
        return iso


def pick(signals: list[dict], action: str, min_conf: float = CONF_MIN) -> list[dict]:
    return [
        s for s in signals
        if s.get("recommendations", {}).get("short_term", {}).get("action") == action
        and s.get("recommendations", {}).get("short_term", {}).get("confidence", 0) >= min_conf
    ]


def fmt_lines(stocks: list[dict], limit: int = 5) -> list[str]:
    lines = []
    for s in stocks[:limit]:
        sym = s["symbol"].replace(".BK", "")
        cci = s.get("cci_short", 0.0)
        conf = s["recommendations"]["short_term"]["confidence"]
        lines.append(f"  • *{sym}*  CCI {cci:+.2f}  conf {conf:.0%}")
    if len(stocks) > limit:
        lines.append(f"  • _… +{len(stocks) - limit} more_")
    return lines


def build_message(data: dict, dashboard_url: str = "") -> str:
    signals = data.get("signals", [])
    run_type = data.get("run_type", "").replace("_", " ").title() or "Pipeline"
    date_str = fmt_date(data.get("generated_at", ""))

    strong_buys  = pick(signals, "STRONG_BUY")
    buys         = pick(signals, "BUY")
    strong_sells = pick(signals, "STRONG_SELL")
    sells        = pick(signals, "SELL")

    lines = [
        f"📊 *TStock — {run_type}*",
        f"_{date_str}_",
        "",
    ]

    if strong_buys:
        lines.append(f"🟢 *STRONG BUY* ({len(strong_buys)})")
        lines += fmt_lines(strong_buys)
        lines.append("")
    if buys:
        lines.append(f"🔵 *BUY* ({len(buys)})")
        lines += fmt_lines(buys)
        lines.append("")
    if strong_sells:
        lines.append(f"🔴 *STRONG SELL* ({len(strong_sells)})")
        lines += fmt_lines(strong_sells)
        lines.append("")
    if sells:
        lines.append(f"🟠 *SELL* ({len(sells)})")
        lines += fmt_lines(sells)
        lines.append("")

    if not (strong_buys or buys or strong_sells or sells):
        lines.append("⚪ ไม่มีสัญญาณชัดเจน (HOLD ทั้งหมด หรือ confidence ต่ำ)")
        lines.append("")

    # Risk flags summary
    all_flags: set[str] = set()
    flagged_syms: dict[str, list[str]] = {}
    for s in signals:
        flags = s.get("risk_flags") or []
        if flags:
            sym = s["symbol"].replace(".BK", "")
            flagged_syms[sym] = flags
            all_flags.update(flags)
    if all_flags:
        lines.append(f"⚠ *Risk flags:* {', '.join(sorted(all_flags))}")
        lines.append("")

    if dashboard_url:
        lines.append(f"🔗 [Open dashboard]({dashboard_url})")

    return "\n".join(lines).strip()


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing — skipping notification")
        return 0  # don't fail the workflow

    signals_file = Path("data/signals.json")
    if not signals_file.exists():
        print(f"{signals_file} not found")
        return 1

    data = json.loads(signals_file.read_text(encoding="utf-8"))
    text = build_message(data, os.environ.get("DASHBOARD_URL", ""))

    # Telegram caps message at 4096 chars
    if len(text) > 4000:
        text = text[:3990] + "\n…(truncated)"

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"Telegram send failed: HTTP {resp.status_code}")
        print(resp.text[:500])
        return 1

    print(f"Telegram notification sent ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
