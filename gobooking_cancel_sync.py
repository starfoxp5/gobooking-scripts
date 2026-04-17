#!/usr/bin/env python3
"""
gobooking_cancel_sync.py
────────────────────────
監聽 GoBooking 取消預定通知信 → 自動刪除 Google Calendar 事件 → Telegram 通知。

設計給 LaunchAgent 定期執行，stdout 帶 timestamp 方便追蹤。
每封信獨立處理，一封失敗不影響其他。
"""

import json
import logging
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

# ── 常數 ─────────────────────────────────────────────────────────────
GOG = "/opt/homebrew/bin/gog"
GMAIL_ACCOUNT = "fiona.aibot@gmail.com"
CALENDAR_ACCOUNT = "energy.shuttlecock979@gmail.com"
TELEGRAM_CHAT_ID = "8472011522"

GMAIL_SEARCH_QUERY = "from:justbooking@miezo.com.tw subject:取消預約通知 is:unread"
AUTH_DEGRADED_PATH = Path(
    "/Users/openmini/.openclaw/workspace-fiona/logs/gobooking_cancel_sync_auth_degraded.json"
)

# 全形 ↔ 半形場地代號對照
FULLWIDTH = "ＡＢＣＪＫＱ"
HALFWIDTH = "ABCJKQ"
TO_HALF = str.maketrans(FULLWIDTH, HALFWIDTH)
TO_FULL = str.maketrans(HALFWIDTH, FULLWIDTH)

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("gobooking_cancel")
AUTH_DEGRADED = False


def _mark_auth_degraded(stage: str, reason: str, error_text: str) -> None:
    """Persist auth-degraded state so watchdog can report real root cause."""
    global AUTH_DEGRADED
    AUTH_DEGRADED = True
    try:
        AUTH_DEGRADED_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now().isoformat(),
            "stage": stage,
            "reason": reason,
            "error": error_text[:1000],
        }
        AUTH_DEGRADED_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _clear_auth_degraded() -> None:
    global AUTH_DEGRADED
    AUTH_DEGRADED = False
    try:
        if AUTH_DEGRADED_PATH.exists():
            AUTH_DEGRADED_PATH.unlink()
    except Exception:
        pass


def _auth_error_reason(text: str) -> Optional[str]:
    lowered = text.lower()
    if "invalid_grant" in lowered:
        return "invalid_grant"
    if "no auth for" in lowered:
        return "no_auth"
    return None


# ── 資料結構 ─────────────────────────────────────────────────────────
@dataclass
class CancelInfo:
    """從取消通知信解析出的預約資訊。"""

    order: str  # e.g. "EY0492603489"
    date: str  # e.g. "2026-03-28"
    start_time: str  # e.g. "06:00"
    end_time: str  # e.g. "07:30"
    venue: str  # 全形場地代號，e.g. "Ａ"

    @property
    def venue_half(self) -> str:
        return self.venue.translate(TO_HALF)

    @property
    def venue_full(self) -> str:
        return self.venue.translate(TO_FULL)

    @property
    def time_range_display(self) -> str:
        return f"{self.date} {self.start_time}-{self.end_time}"

    @property
    def venue_display(self) -> str:
        return f"{self.venue_full}場"


# ── HTML 解析 ────────────────────────────────────────────────────────
class TextExtractor(HTMLParser):
    """從 HTML 中擷取有意義的文字片段。"""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text and not text.startswith(".") and "{" not in text:
            self.parts.append(text)


def _extract_text_parts(html: str) -> list[str]:
    parser = TextExtractor()
    parser.feed(html)
    return parser.parts


# ── 解析取消通知 ─────────────────────────────────────────────────────
_RE_ORDER = re.compile(r"EY\w+")
_RE_DATE = re.compile(r"(\d{4})[/-](\d{2})[/-](\d{2})")
_RE_TIME_RANGE = re.compile(r"(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})")
_RE_VENUE = re.compile(r"[ＡＢＣＪＫＱABCJKQ]", re.IGNORECASE)


def parse_cancel_email(html_body: str) -> Optional[CancelInfo]:
    """
    解析 GoBooking 取消通知的 HTML body，回傳 CancelInfo 或 None。

    預期文字片段中包含：
      - 訂單編號行：'# EY0492603489'
      - 場地行：'活力一館『 Ａ 』場'
      - 時段行：'2026-03-28 06:00 - 07:30'
    """
    parts = _extract_text_parts(html_body)

    order: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    venue: Optional[str] = None

    for text in parts:
        # 訂單編號
        if order is None:
            m = _RE_ORDER.search(text)
            if m:
                order = m.group(0)

        # 日期 + 時段（通常在同一行，e.g. "2026-03-28 06:00 - 07:30"）
        if date is None:
            m = _RE_DATE.search(text)
            if m:
                date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        if start_time is None:
            m = _RE_TIME_RANGE.search(text)
            if m:
                start_time = m.group(1).zfill(5)  # "6:00" → "06:00"
                end_time = m.group(2).zfill(5)

        # 場地代號（全形優先出現在 summary 如「活力一館『 Ａ 』場」）
        if venue is None and "場" in text:
            m = _RE_VENUE.search(text)
            if m:
                venue = m.group(0).upper().translate(TO_FULL)  # 統一存全形

    if not all([order, date, start_time, end_time, venue]):
        log.warning(
            "解析不完整: order=%s date=%s time=%s-%s venue=%s",
            order,
            date,
            start_time,
            end_time,
            venue,
        )
        return None

    return CancelInfo(
        order=order,  # type: ignore[arg-type]
        date=date,  # type: ignore[arg-type]
        start_time=start_time,  # type: ignore[arg-type]
        end_time=end_time,  # type: ignore[arg-type]
        venue=venue,  # type: ignore[arg-type]
    )


# ── 外部工具呼叫 ─────────────────────────────────────────────────────
def _run_gog(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """執行 gog CLI，回傳 CompletedProcess。"""
    cmd = [GOG, *args]
    log.debug("exec: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def get_telegram_token() -> str:
    """從 macOS Keychain 讀取 Telegram Bot Token。"""
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            "ENERGYCOCK_BOT_TOKEN",
            "-s",
            "fiona-ai",
            "-w",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("無法從 Keychain 取得 ENERGYCOCK_BOT_TOKEN")
    return token


def send_telegram(token: str, msg: str) -> None:
    """透過 Telegram Bot API 發送訊息（使用 urllib，避免 curl 編碼問題）。"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
        }
    ).encode()
    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                log.warning("Telegram API 回傳 %d", resp.status)
    except Exception as e:
        log.error("Telegram 發送失敗: %s", e)


# ── Gmail 操作 ───────────────────────────────────────────────────────
def fetch_unread_cancel_emails() -> list[dict]:
    """搜尋未讀的 GoBooking 取消通知信。"""
    result = _run_gog(
        "gmail",
        "messages",
        "search",
        GMAIL_SEARCH_QUERY,
        "--account",
        GMAIL_ACCOUNT,
        "--include-body",
        "-j",
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        reason = _auth_error_reason(err)
        if reason:
            _mark_auth_degraded("gmail_search", reason, err)
        log.error(
            "Gmail 搜尋失敗 (rc=%d): %s", result.returncode, err
        )
        return []

    stdout = result.stdout.strip()
    if not stdout:
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        log.error("Gmail 搜尋結果 JSON 解析失敗: %s", e)
        return []

    # gog 回傳格式：直接 list 或 {"messages": [...]}
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("messages", [])
    return []


def mark_as_read(msg_id: str) -> None:
    """標記 Gmail 訊息為已讀。"""
    result = _run_gog(
        "--client",
        "fiona",
        "gmail",
        "messages",
        "modify",
        msg_id,
        "--remove",
        "UNREAD",
        "-a",
        GMAIL_ACCOUNT,
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        reason = _auth_error_reason(err)
        if reason:
            _mark_auth_degraded("gmail_mark_read", reason, err)
        log.warning("標記已讀失敗 (msg_id=%s): %s", msg_id, err)
    else:
        log.info("已標記已讀: %s", msg_id)


# ── Calendar 操作 ────────────────────────────────────────────────────
def find_calendar_event(info: CancelInfo) -> Optional[tuple[str, str]]:
    """
    在 Google Calendar 中找到對應的事件。

    回傳 (event_id, summary) 或 None。
    """
    from_dt = f"{info.date}T00:00:00+08:00"
    to_dt = f"{info.date}T23:59:59+08:00"

    result = _run_gog(
        "--client",
        "fiona",
        "calendar",
        "list",
        "-a",
        CALENDAR_ACCOUNT,
        "--from",
        from_dt,
        "--to",
        to_dt,
        "--max",
        "200",
        "-j",
    )

    if result.returncode != 0:
        err = result.stderr.strip()
        reason = _auth_error_reason(err)
        if reason:
            _mark_auth_degraded("calendar_list", reason, err)
        log.error(
            "Calendar 查詢失敗 (rc=%d): %s", result.returncode, err
        )
        return None

    stdout = result.stdout.strip()
    if not stdout:
        log.info("Calendar 查詢無結果 (%s)", info.date)
        return None

    try:
        cal_data = json.loads(stdout)
    except json.JSONDecodeError as e:
        log.error("Calendar JSON 解析失敗: %s", e)
        return None

    # gog calendar list 回傳格式：{"events": [...]}（也防禦其他格式）
    if isinstance(cal_data, list):
        events = cal_data
    elif isinstance(cal_data, dict):
        events = cal_data.get("events", cal_data.get("items", []))
    else:
        events = []

    # 組合開始時間字串用於比對，e.g. "06:00"
    # Calendar event start.dateTime 格式：2026-03-28T06:00:00+08:00
    target_hour_min = info.start_time  # e.g. "06:00"

    for event in events:
        summary = event.get("summary", "")
        start = event.get("start", {})
        start_dt = start.get("dateTime", "") if isinstance(start, dict) else str(start)

        # 比對時間：start_dt 包含 "T06:00:" 或 "T6:00:"
        time_match = f"T{target_hour_min}:" in start_dt

        # 比對場地：全形或半形都接受
        venue_match = info.venue_full in summary or info.venue_half in summary

        if venue_match and time_match:
            event_id = event.get("id", "")
            if event_id:
                log.info("找到匹配事件: id=%s summary=%s", event_id, summary)
                return event_id, summary

    return None


def delete_calendar_event(event_id: str) -> tuple[bool, str]:
    """
    刪除 Calendar 事件。

    回傳 (success, error_message)。
    """
    result = _run_gog(
        "--client",
        "fiona",
        "calendar",
        "delete",
        "primary",
        event_id,
        "-y",
        "-a",
        CALENDAR_ACCOUNT,
    )
    if result.returncode == 0:
        return True, ""
    error = result.stderr.strip() or f"exit code {result.returncode}"
    return False, error


# ── 單封信處理 ───────────────────────────────────────────────────────
def process_message(msg: dict, tg_token: str) -> None:
    """處理一封取消通知信（解析 → 刪日曆 → 通知 → 標已讀）。"""
    msg_id = msg.get("id", "")
    if not msg_id:
        log.warning("訊息缺少 id，跳過")
        return

    html_body = msg.get("body", "")
    log.info("處理訊息: %s", msg_id)

    try:
        info = parse_cancel_email(html_body)
    except Exception as e:
        log.error("解析例外 (msg_id=%s): %s", msg_id, e)
        send_telegram(
            tg_token,
            f"⚠️ <b>取消通知解析失敗</b>\n訊息 ID: {msg_id}\n錯誤: {e}\n請手動處理",
        )
        mark_as_read(msg_id)
        return

    if info is None:
        send_telegram(
            tg_token,
            f"⚠️ <b>取消通知解析失敗</b>\n訊息 ID: {msg_id}\n無法提取完整訂單資訊，請手動處理",
        )
        mark_as_read(msg_id)
        return

    log.info(
        "解析結果: 訂單=%s 場地=%s 時段=%s",
        info.order,
        info.venue_display,
        info.time_range_display,
    )

    # 查詢日曆
    match = find_calendar_event(info)

    if match is None:
        # 日曆無對應事件 — 正常情況，靜默處理
        log.info("日曆無對應事件，略過")
    else:
        event_id, summary = match
        ok, error = delete_calendar_event(event_id)

        if ok:
            # 成功靜默，只記 log
            log.info("日曆事件已刪除: %s", event_id)
        else:
            send_telegram(
                tg_token,
                f"⚠️ <b>取消通知收到，但日曆刪除失敗</b>\n"
                f"訂單：{info.order}\n"
                f"場地：{info.venue_display}\n"
                f"時段：{info.time_range_display}\n"
                f"錯誤：{error}",
            )
            log.error("日曆事件刪除失敗: %s — %s", event_id, error)

    # 最後才標記已讀（不管成功失敗都要標，避免重複處理）
    mark_as_read(msg_id)


# ── 主流程 ───────────────────────────────────────────────────────────
def main() -> None:
    log.info("=== gobooking_cancel_sync 開始 ===")
    _clear_auth_degraded()

    # 先取 token，失敗就直接結束
    try:
        tg_token = get_telegram_token()
    except RuntimeError as e:
        log.error("%s", e)
        return

    # 搜尋未讀取消通知
    messages = fetch_unread_cancel_emails()
    if not messages:
        log.info("無未讀取消通知")
        log.info("=== gobooking_cancel_sync 結束 ===")
        return

    log.info("找到 %d 封取消通知", len(messages))

    # 逐封處理，互不影響
    for msg in messages:
        try:
            process_message(msg, tg_token)
        except Exception as e:
            msg_id = msg.get("id", "?")
            log.error("處理訊息 %s 時未預期錯誤: %s", msg_id, e, exc_info=True)
            try:
                send_telegram(
                    tg_token, f"⚠️ <b>取消通知處理異常</b>\n訊息 ID: {msg_id}\n錯誤: {e}"
                )
                mark_as_read(msg_id)
            except Exception:
                log.error("錯誤恢復也失敗了 (msg_id=%s)", msg_id)

    if AUTH_DEGRADED:
        reason = "google_oauth_auth_error"
        try:
            if AUTH_DEGRADED_PATH.exists():
                raw = json.loads(AUTH_DEGRADED_PATH.read_text(encoding="utf-8"))
                if str(raw.get("reason", "")).strip():
                    reason = f"google_oauth_{raw['reason']}"
        except Exception:
            pass
        log.warning(
            "STATUS=degraded_auth reason=%s action=reauth_google_client_fiona",
            reason,
        )

    log.info("=== gobooking_cancel_sync 結束 ===")


if __name__ == "__main__":
    main()
