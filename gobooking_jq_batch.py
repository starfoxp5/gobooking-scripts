#!/usr/bin/env python3
"""
gobooking_jq_batch.py - 批量預約活力羽球館 J 場 + Q 場

用法：
  python3 gobooking_jq_batch.py --dry-run
  python3 gobooking_jq_batch.py
"""

import argparse
import asyncio
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from playwright.async_api import async_playwright

BACKEND_URL = "https://gobooking.tw/owner/signinmyroom.html"
BACKEND_USER = "FionaAibot"
RESULT_PATH = "/tmp/gobooking_jq_results.json"

CONTACT = {
    "name": "Fiona",
    "phone": "0932-008-667",
    "email": "fiona.aibot@gmail.com",
}
COUPON = "energy0258"

HOLIDAYS = {
    "2026/04/02": "清明連假（週四）",
    "2026/09/29": "中秋節（週二）",
}

MONTH_NAMES = {
    "01": "January",
    "02": "February",
    "03": "March",
    "04": "April",
    "05": "May",
    "06": "June",
    "07": "July",
    "08": "August",
    "09": "September",
    "10": "October",
    "11": "November",
    "12": "December",
}
MONTH_ORDER = {name: int(number) for number, name in MONTH_NAMES.items()}


@dataclass(frozen=True)
class Venue:
    label: str
    room_url: str
    plan_id: str


VENUES = [
    Venue("J場", "https://gobooking.tw/energy/room.html?170052020310077340", "17521"),
    Venue("Q場", "https://gobooking.tw/energy/room.html?170187112712110103", "171871"),
]


@dataclass(frozen=True)
class BookingRequest:
    date: str
    start: str = "19:00"
    end: str = "23:00"
    name: str = CONTACT["name"]
    phone: str = CONTACT["phone"]
    email: str = CONTACT["email"]
    coupon: str = COUPON
    remark: str = "Fiona"

    @property
    def display_name(self) -> str:
        return self.name + "(＾ω＾)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="跑流程但不送出")
    return parser.parse_args()


def _get_gobooking_password() -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-a", "GOBOOKING_PASSWORD", "-s", "fiona-ai", "-w"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def generate_target_dates() -> list[str]:
    start_date = date(2026, 4, 10)
    end_date = date(2026, 9, 30)
    weekdays = {6}  # 週日
    results: list[str] = []
    current = start_date

    while current <= end_date:
        formatted = current.strftime("%Y/%m/%d")
        if current.weekday() in weekdays and formatted not in HOLIDAYS:
            results.append(formatted)
        current += timedelta(days=1)

    return results


def get_skipped_holidays() -> list[str]:
    start_dt = datetime.strptime("2026/04/10", "%Y/%m/%d").date()
    end_dt = datetime.strptime("2026/09/30", "%Y/%m/%d").date()
    weekdays = {6}  # 週日
    skipped = []
    for holiday in sorted(HOLIDAYS):
        holiday_dt = datetime.strptime(holiday, "%Y/%m/%d").date()
        if start_dt <= holiday_dt <= end_dt and holiday_dt.weekday() in weekdays:
            skipped.append(holiday)
    return skipped


BACKEND_ROOM_SETTING_URL = "https://gobooking.tw/owner/v2/room-setting.html"


async def set_booking_window(days: int) -> str:
    password = _get_gobooking_password()
    print(f"[後台] 設定預約天數 -> {days} 天")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        # 步驟 1：登入
        await page.goto(BACKEND_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        await page.fill("input[name='userACCT']", BACKEND_USER)
        await page.fill("input[name='userPASS']", password)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        # 步驟 2：直接導航到房型設定頁（不靠點選，避免落地頁差異）
        await page.goto(BACKEND_ROOM_SETTING_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # 步驟 3：依序點所有相關 tab，確保天數欄位載入
        for tab_text in ["房型設定", "時租定價", "營業資訊"]:
            try:
                await page.click(f"text={tab_text}", timeout=3000)
                await page.wait_for_timeout(800)
            except Exception:
                pass

        status = await page.evaluate(
            """({ days }) => {
                const inputs = Array.from(
                    document.querySelectorAll('input[type=text],input[type=number],input:not([type])')
                ).filter((el) => el.offsetParent !== null);
                const setValue = (el, value) => {
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, String(value));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                };

                let minEl = null;
                let maxEl = null;
                for (const el of inputs) {
                    const ctx = (el.closest('div,tr,li') || document.body).innerText || '';
                    if (!minEl && (el.id.startsWith('min-week-') || ctx.includes('可開始預約'))) minEl = el;
                    if (!maxEl && (el.id.startsWith('max-week-') || ctx.includes('無法預約'))) maxEl = el;
                }
                if (!maxEl) return 'not_found';
                if (minEl) setValue(minEl, 0);
                setValue(maxEl, days);
                return `ok:min=${minEl ? minEl.value : 'n/a'}/max=${maxEl.value}`;
            }""",
            {"days": days},
        )
        print(f"[後台] 欄位設定: {status}")
        if status == "not_found":
            await page.screenshot(path="/tmp/gobooking_backend_debug.png", full_page=True)
            await browser.close()
            raise RuntimeError("找不到天數欄位，請檢查 /tmp/gobooking_backend_debug.png")

        async with page.expect_response(lambda resp: "ow_set_roombasic_info" in resp.url, timeout=10000) as save_resp_info:
            saved = await page.evaluate(
                """() => {
                    const buttons = Array.from(document.querySelectorAll('button,input[type=submit]'))
                        .filter((el) => el.offsetParent !== null);
                    for (const button of buttons) {
                        const text = button.innerText || button.value || '';
                        if (text.includes('儲存') || text.includes('確認') || text.includes('Save')) {
                            button.click();
                            return `clicked:${text.trim()}`;
                        }
                    }
                    return 'no_save_btn';
                }"""
            )
        save_resp = await save_resp_info.value
        print(f"[後台] 儲存: {saved}")
        print(f"[後台] 儲存 API: {save_resp.status} {save_resp.url}")

        await page.wait_for_timeout(2000)
        # 重新載入房型設定頁驗證
        await page.goto(BACKEND_ROOM_SETTING_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        for tab_text in ["房型設定", "時租定價", "營業資訊"]:
            try:
                await page.click(f"text={tab_text}", timeout=3000)
                await page.wait_for_timeout(800)
            except Exception:
                pass

        verified = await page.evaluate("() => document.querySelector('#max-week-0')?.value ?? 'unknown'")
        print(f"[後台] 驗證天數: {verified}")
        await browser.close()
        return verified


async def close_modal(page) -> None:
    modal = await page.query_selector(".fixed.top-0.left-0.z-10")
    if modal:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)


async def goto_month(page, year: str, month: str, loaded: set[str]) -> None:
    key = f"{year}/{month}"
    if key in loaded:
        return

    calendar_visible = await page.evaluate(
        "() => document.querySelector('.air-datepicker.-active-') !== null"
    )
    if not calendar_visible:
        try:
            await page.locator("#date-picker").click(timeout=5000)
        except Exception:
            await page.locator(
                "input[name='startdate'], input[placeholder*='日期'], input[placeholder*='date']"
            ).first.click(timeout=5000)
        await page.wait_for_timeout(1500)

    target_name = MONTH_NAMES[month]
    target_ym = int(year) * 12 + int(month)

    for _ in range(18):
        await close_modal(page)
        nav = await page.evaluate(
            "() => document.querySelector('.air-datepicker-nav--title')?.innerText || ''"
        )
        if target_name in nav and year in nav:
            break

        current_ym = 0
        for month_name, number in MONTH_ORDER.items():
            if month_name in nav:
                year_tokens = [token for token in nav.split() if token.isdigit() and len(token) == 4]
                if year_tokens:
                    current_ym = int(year_tokens[0]) * 12 + number
                break
        direction = "prev" if current_ym and current_ym > target_ym else "next"
        clicked = await page.evaluate(
            """({ direction }) => {
                const button = document.querySelector(`[data-action="${direction}"]`);
                if (!button) return false;
                button.click();
                return true;
            }""",
            {"direction": direction},
        )
        if not clicked:
            break
        await page.wait_for_timeout(600)
    loaded.add(key)


async def set_date(page, date_str: str) -> None:
    day = date_str.split("/")[2].lstrip("0")
    clicked = await page.evaluate(
        """({ day }) => {
            for (const cell of document.querySelectorAll('.air-datepicker-cell.-day-')) {
                if (
                    cell.offsetParent !== null &&
                    (cell.textContent || '').trim() === day &&
                    !cell.classList.contains('-disabled-') &&
                    !cell.classList.contains('-other-month-')
                ) {
                    cell.click();
                    return true;
                }
            }
            return false;
        }""",
        {"day": day},
    )
    if not clicked:
        raise RuntimeError(f"日期 {date_str} 在日曆上 disabled 或找不到")
    await page.wait_for_timeout(1500)
    await close_modal(page)


async def get_booked(page, date_str: str) -> list[dict]:
    return await page.evaluate(
        """({ dateStr }) => {
            const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
            return JSON.parse(JSON.stringify(pinia.state.value.calendar.bookedList))
                .filter((item) => item.date === dateStr);
        }""",
        {"dateStr": date_str},
    )


def has_conflict(booked: list[dict], start: str, end: str) -> bool:
    for item in booked:
        if not (item["end"] <= start or item["start"] >= end):
            return True
    return False


async def check_date_availability(page, venue: Venue, request: BookingRequest) -> tuple[bool, list[dict]]:
    year, month, _ = request.date.split("/")
    await page.goto(venue.room_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2500)
    await page.select_option("select[name='booking-plan']", value=venue.plan_id)
    await page.wait_for_timeout(500)
    await goto_month(page, year, month, set())
    await set_date(page, request.date)
    booked = await get_booked(page, request.date)
    return (not has_conflict(booked, request.start, request.end), booked)


async def book_one(page, venue: Venue, request: BookingRequest, dry_run: bool = False) -> dict:
    print(f"\n▶ [{venue.label}] 預約 {request.date} {request.start}~{request.end}")

    try:
        try:
            await page.wait_for_function(
                """() => {
                    const select = document.querySelector("select[name='start-time']");
                    return select && select.options.length > 1 && !select.disabled;
                }""",
                timeout=10000,
            )
        except Exception:
            options = await page.evaluate(
                """() => {
                    const select = document.querySelector("select[name='start-time']");
                    return select ? Array.from(select.options).map((option) => option.text).join(',') : 'NOT FOUND';
                }"""
            )
            return {"date": request.date, "success": False, "msg": f"start-time 未載入: {options}"}

        start_set = await page.evaluate(
            """({ start }) => {
                const select = document.querySelector("select[name='start-time']");
                for (let i = 0; i < select.options.length; i++) {
                    if (select.options[i].text.trim() === start) {
                        select.selectedIndex = i;
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""",
            {"start": request.start},
        )
        if not start_set:
            options = await page.evaluate(
                """() => {
                    const select = document.querySelector("select[name='start-time']");
                    return Array.from(select.options).map((option) => option.text).join(',');
                }"""
            )
            return {"date": request.date, "success": False, "msg": f"{request.start} 無法選取，可用: {options}"}
        await page.wait_for_timeout(1500)

        try:
            await page.wait_for_function(
                """() => {
                    const select = document.querySelector("select[name='end-time']");
                    return select && select.options.length > 1 && !select.disabled;
                }""",
                timeout=10000,
            )
        except Exception:
            pass

        end_set = await page.evaluate(
            """({ end }) => {
                const select = document.querySelector("select[name='end-time']");
                for (let i = 0; i < select.options.length; i++) {
                    if (select.options[i].text.trim() === end) {
                        select.selectedIndex = i;
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""",
            {"end": request.end},
        )
        if not end_set:
            options = await page.evaluate(
                """() => {
                    const select = document.querySelector("select[name='end-time']");
                    return Array.from(select.options).map((option) => option.text).join(',');
                }"""
            )
            return {"date": request.date, "success": False, "msg": f"{request.end} 無法選取，可用: {options}"}
        await page.wait_for_timeout(1000)

        book_now = await page.evaluate(
            """() => {
                const visible = (el) => !!(el && el.offsetParent !== null);
                const buttons = Array.from(document.querySelectorAll('button'));
                const target = buttons.find((el) => {
                    const text = (el.innerText || '').trim();
                    return visible(el) && text.includes('立即預約');
                });
                if (!target) {
                    return {
                        clicked: false,
                        visibleButtons: buttons.filter((el) => visible(el)).map((el) => (el.innerText || '').trim()),
                    };
                }
                target.click();
                return { clicked: true, text: (target.innerText || '').trim() };
            }"""
        )
        if not book_now.get("clicked"):
            return {
                "date": request.date,
                "success": False,
                "msg": f"找不到可見的立即預約按鈕: {book_now.get('visibleButtons', [])}",
            }

        try:
            await page.wait_for_selector("#payment-name", timeout=10000)
        except Exception:
            await page.screenshot(path=f"/tmp/gobooking_{venue.label}_{request.date.replace('/', '')}_after_click.png")
            return {"date": request.date, "success": False, "msg": "點立即預約後填資料表單未出現"}
        await page.wait_for_timeout(500)

        await page.locator("#payment-name").fill(request.display_name)
        await page.wait_for_timeout(200)
        await page.locator("#payment-phone").fill(request.phone)
        await page.wait_for_timeout(200)
        await page.locator("#payment-email").fill(request.email)
        await page.wait_for_timeout(200)
        await page.evaluate("document.querySelector('label[for=\"same-as-contact\"]')?.click()")
        await page.wait_for_timeout(500)

        await page.evaluate(
            """({ booking }) => {
                const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
                const store = pinia._s.get('booking');
                if (!store) return;
                store.$patch({
                    paymentName: booking.display_name,
                    paymentPhone: booking.phone,
                    paymentEmail: booking.email,
                    contactName: booking.display_name,
                    contactPhone: booking.phone,
                    contactEmail: booking.email,
                    invType: 'member',
                });
            }""",
            {"booking": {**asdict(request), "display_name": request.display_name}},
        )
        await page.wait_for_timeout(300)

        await page.locator("#coupon").fill(request.coupon)
        await page.wait_for_timeout(300)
        await page.evaluate(
            """() => {
                const input = document.querySelector('#coupon');
                let current = input?.parentElement;
                for (let i = 0; i < 5; i++) {
                    const button = current?.querySelector('button');
                    if (button) {
                        button.click();
                        return true;
                    }
                    current = current?.parentElement;
                }
                return false;
            }"""
        )
        await page.wait_for_timeout(2500)
        await page.evaluate(
            """() => {
                for (const button of document.querySelectorAll('button')) {
                    if ((button.innerText || '').trim() === 'Apply') {
                        button.click();
                        return true;
                    }
                }
                return false;
            }"""
        )
        await page.wait_for_timeout(2000)

        before_shot = f"/tmp/gobooking_{venue.label}_{request.date.replace('/', '')}_before.png"
        await page.screenshot(path=before_shot)
        print(f"  截圖: {before_shot}")

        submit_info = await page.evaluate(
            """() => {
                const visible = (el) => !!(el && el.offsetParent !== null);
                const textOf = (el) => (el.innerText || el.value || '').trim();
                const buttons = Array.from(document.querySelectorAll('button,input[type=submit],input[type=button]'));
                const target = buttons.find((el) => {
                    if (!visible(el) || el.disabled) return false;
                    const text = textOf(el);
                    return text.includes('確認付款')
                        || text === 'Confirm'
                        || text.includes('確認預約')
                        || text.includes('送出預約')
                        || text === '送出';
                });
                if (!target) {
                    return {
                        found: false,
                        visibleButtons: buttons.filter((el) => visible(el)).map((el) => ({
                            text: textOf(el),
                            type: el.getAttribute('type') || '',
                            className: el.className || '',
                        })),
                    };
                }
                return {
                    found: true,
                    text: textOf(target),
                    type: target.getAttribute('type') || '',
                    className: target.className || '',
                };
            }"""
        )
        if not submit_info.get("found"):
            return {
                "date": request.date,
                "success": False,
                "msg": f"找不到送出按鈕，可見按鈕: {submit_info.get('visibleButtons', [])}",
            }

        print(f"  ✓ 送出按鈕: {submit_info['text']} ({submit_info['type']})")
        if dry_run:
            return {"date": request.date, "success": True, "msg": "DRY_RUN"}

        submit_result = await page.evaluate(
            """({ booking }) => {
                return (async () => {
                    try {
                        const app = document.querySelector('#app').__vue_app__;
                        const pinia = app.config.globalProperties.$pinia;
                        const store = pinia._s.get('booking');
                        store.$patch({
                            paymentName: booking.display_name,
                            paymentPhone: booking.phone,
                            paymentEmail: booking.email,
                            contactName: booking.display_name,
                            contactPhone: booking.phone,
                            contactEmail: booking.email,
                            invType: 'member',
                            bookingRemark: booking.remark,
                        });
                        await store.submitBooking();
                        return 'ok';
                    } catch (error) {
                        return 'error: ' + error.message;
                    }
                })();
            }""",
            {"booking": {**asdict(request), "display_name": request.display_name}},
        )
        print(f"  submitBooking: {submit_result}")
        await page.wait_for_timeout(5000)

        result_shot = f"/tmp/gobooking_{venue.label}_{request.date.replace('/', '')}_result.png"
        await page.screenshot(path=result_shot)
        return {"date": request.date, "success": submit_result == "ok", "msg": submit_result}
    except Exception as error:
        error_shot = f"/tmp/gobooking_{venue.label}_{request.date.replace('/', '')}_error.png"
        try:
            await page.screenshot(path=error_shot)
        except Exception:
            pass
        print(f"  ✗ 錯誤: {error}")
        return {"date": request.date, "success": False, "msg": str(error)}


async def run_for_venue(venue: Venue, requests: list[BookingRequest], skipped_holidays: list[str], dry_run: bool = False) -> dict:
    """接受 list[BookingRequest]；Q場每天有3筆，J場每天1筆。"""
    summary = {
        "booked": [],
        "already_booked": [],
        "skipped_holidays": list(skipped_holidays),
        "failed": [],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        for index, request in enumerate(requests):
            available, booked = await check_date_availability(page, venue, request)
            if not available:
                print(f"[{venue.label}] 跳過 {request.date} {request.start}~{request.end}: 已有衝突 {booked}")
                summary["already_booked"].append(f"{request.date} {request.start}~{request.end}")
            else:
                result = await book_one(page, venue, request, dry_run=dry_run)
                if result["success"]:
                    summary["booked"].append(f"{request.date} {request.start}~{request.end}")
                else:
                    summary["failed"].append(f"{request.date} {request.start}~{request.end}: {result['msg']}")
            if index < len(requests) - 1:
                await asyncio.sleep(3)

        await browser.close()

    return summary


def build_requests(dates: list[str]) -> dict[str, list[BookingRequest]]:
    """為每個 venue 建立 BookingRequest 清單。
    J場: 每天 1 筆 19:00–23:00
    Q場: 每天 3 筆 19:00–20:00 / 20:00–21:00 / 21:00–22:00
    """
    j_requests = [
        BookingRequest(date=d, start="19:00", end="23:00")
        for d in dates
    ]
    q_requests = [
        BookingRequest(date=d, start=s, end=e)
        for d in dates
        for s, e in [("19:00", "20:00"), ("20:00", "21:00"), ("21:00", "22:00")]
    ]
    return {
        "J場": j_requests,
        "Q場": q_requests,
    }


async def run_batch(dry_run: bool = False) -> dict:
    dates = generate_target_dates()
    skipped_holidays = get_skipped_holidays()
    venue_requests = build_requests(dates)
    results = {}
    window_changed = False

    try:
        verify = await set_booking_window(100)
        if verify != "100":
            raise RuntimeError(f"後台驗證失敗，max-week-0={verify}")
        window_changed = True
        print("[後台] ✅ 預約窗口已設為 100 天")

        for venue in VENUES:
            print(f"\n=== {venue.label} {'(DRY RUN)' if dry_run else ''} ===")
            requests = venue_requests[venue.label]
            results[venue.label] = await run_for_venue(venue, requests, skipped_holidays, dry_run=dry_run)
    finally:
        if window_changed:
            try:
                verify = await set_booking_window(30)
                if verify != "30":
                    raise RuntimeError(f"後台驗證失敗，max-week-0={verify}")
                print("[後台] ✅ 預約窗口已還原為 30 天")
            except Exception as error:
                print(f"[後台] ⚠️ 無法還原天數（{error}），請手動改回 30 天！")

    with open(RESULT_PATH, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)

    print(f"\n結果存至 {RESULT_PATH}")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


async def main() -> None:
    args = parse_args()
    await run_batch(dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
