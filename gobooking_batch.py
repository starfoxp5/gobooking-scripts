#!/usr/bin/env python3
"""
gobooking_batch.py - 批量預約活力羽球館 A 場

用法：
  python3 gobooking_batch.py --check
  python3 gobooking_batch.py --test
  python3 gobooking_batch.py --cancel-test
  python3 gobooking_batch.py --dry-run
  python3 gobooking_batch.py
"""

import argparse
import asyncio
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from playwright.async_api import async_playwright

from gobooking_cancel import run_cancel

ROOM_URL = "https://gobooking.tw/energy/room.html?170049020310051559"
PLAN_ID = "17491"

# ⚠️ 聯絡人資訊不預設，執行時必須透過 --name / --phone / --email 傳入
CONTACT = {
    "name": "",
    "phone": "",
    "email": "",
}
COUPON = ""  # 優惠碼執行時透過 --coupon 傳入

BACKEND_URL = "https://gobooking.tw/owner/signinmyroom.html"
BACKEND_USER = "FionaAibot"

COMPLETED_BATCH_DATES = [
    "2026/04/23",
    "2026/04/30",
    "2026/05/07",
    "2026/05/14",
    "2026/05/21",
    "2026/05/28",
    "2026/06/04",
    "2026/06/11",
    "2026/06/18",
    "2026/06/25",
]
PENDING_BATCH_DATES: list[str] = []  # 執行時由 --dates 傳入，此處保持空白
CHECK_DATES = [
    "2026/04/02",
    "2026/04/09",
    *COMPLETED_BATCH_DATES,
]
DEFAULT_CANCEL_TEST_DAYS = 21

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
class BookingRequest:
    date: str
    start: str = "19:00"
    end: str = "22:00"
    name: str = ""
    phone: str = ""
    email: str = ""
    coupon: str = ""
    remark: str = "Fiona"

    @property
    def display_name(self) -> str:
        return self.name + "(＾ω＾)"


def _get_gobooking_password() -> str:
    # 嘗試多個 Keychain 帳號名稱（向下相容）
    for account in ["GOBOOKING_PASSWORD", "FionaAibot"]:
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-a", account, "-s", "fiona-ai", "-w"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            continue
    # fallback 硬編碼（緊急用）
    return "52640246"


async def set_booking_window(days: int) -> str:
    password = _get_gobooking_password()
    print(f"[後台] 設定預約天數 -> {days} 天")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        await page.goto(BACKEND_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        await page.fill("input[name='userACCT']", BACKEND_USER)
        await page.fill("input[name='userPASS']", password)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        try:
            await page.click("text=房型設定", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)

        for tab_text in ["營業資訊", "時租定價", "time"]:
            try:
                await page.click(f"text={tab_text}", timeout=3000)
                await page.wait_for_timeout(800)
                break
            except Exception:
                continue

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
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        for tab_text in ["營業資訊", "時租定價", "time"]:
            try:
                await page.click(f"text={tab_text}", timeout=3000)
                await page.wait_for_timeout(800)
                break
            except Exception:
                continue

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


async def get_booked(page, date_str: str) -> list:
    return await page.evaluate(
        """({ dateStr }) => {
            const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
            return JSON.parse(JSON.stringify(pinia.state.value.calendar.bookedList))
                .filter((item) => item.date === dateStr);
        }""",
        {"dateStr": date_str},
    )


def has_conflict(booked: list, start: str, end: str) -> bool:
    for item in booked:
        if not (item["end"] <= start or item["start"] >= end):
            return True
    return False


async def check_availability(dates: list[str], start: str = "19:00", end: str = "22:00") -> dict:
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        await page.goto(ROOM_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        await page.select_option("select[name='booking-plan']", value=PLAN_ID)
        await page.wait_for_timeout(500)

        loaded: set[str] = set()
        for date_str in dates:
            year, month, _ = date_str.split("/")
            await goto_month(page, year, month, loaded)
            await set_date(page, date_str)
            booked = await get_booked(page, date_str)
            conflict = has_conflict(booked, start, end)
            results[date_str] = {"available": not conflict, "booked": booked}
            icon = "🔴" if conflict else "✅"
            detail = booked if conflict else "空"
            print(f"{icon} {date_str}: {detail}")

        await browser.close()
    return results


async def book_one(page, request: BookingRequest, dry_run: bool = False) -> dict:
    print(f"\n▶ 預約 {request.date} {request.start}~{request.end}")
    year, month, _ = request.date.split("/")

    try:
        await page.goto(ROOM_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2500)
        await page.select_option("select[name='booking-plan']", value=PLAN_ID)
        await page.wait_for_timeout(500)

        await goto_month(page, year, month, set())
        await set_date(page, request.date)

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
            await page.screenshot(path=f"/tmp/gobooking_{request.date.replace('/', '')}_after_click.png")
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
        # 先嘗試找 Apply button（在 fill 前顯示）
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
        await page.wait_for_timeout(1000)
        # 按 Enter 觸發 coupon apply（fill 後旁邊是 Cancel，需要 Enter 送出）
        await page.locator("#coupon").press("Enter")
        await page.wait_for_timeout(3000)
        # 再嘗試點 Apply button
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

        before_shot = f"/tmp/gobooking_{request.date.replace('/', '')}_before.png"
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

        result_shot = f"/tmp/gobooking_{request.date.replace('/', '')}_result.png"
        await page.screenshot(path=result_shot)
        print(f"  ✓ 完成: {page.url}")
        return {"date": request.date, "success": True, "msg": page.url}
    except Exception as error:
        error_shot = f"/tmp/gobooking_{request.date.replace('/', '')}_error.png"
        try:
            await page.screenshot(path=error_shot)
        except Exception:
            pass
        print(f"  ✗ 錯誤: {error}")
        return {"date": request.date, "success": False, "msg": str(error)}


async def run_booking(dates: list[str], dry_run: bool = False, stop_after_first_success: bool = False,
                       name: str = "", phone: str = "", email: str = "", coupon: str = "") -> list[dict]:
    if not dates:
        print("[批量] 目前沒有待預約日期。已完成日期不會再重跑。")
        return []
    if not name or not phone or not email:
        raise ValueError("聯絡人資訊必填：--name / --phone / --email")

    requests = [BookingRequest(date=item, name=name, phone=phone, email=email, coupon=coupon) for item in dates]
    results: list[dict] = []
    window_changed = False

    try:
        try:
            verify = await set_booking_window(100)
            if verify != "100":
                raise RuntimeError(f"後台驗證失敗，max-week-0={verify}")
            window_changed = True
            print("[後台] ✅ 預約窗口已設為 100 天")
        except Exception as error:
            print(f"[後台] ⚠️ 無法設定天數（{error}），仍繼續預約")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()

            for index, request in enumerate(requests):
                result = await book_one(page, request, dry_run=dry_run)
                results.append(result)
                if stop_after_first_success and result["success"]:
                    break
                if index < len(requests) - 1:
                    await asyncio.sleep(3)

            await browser.close()
    finally:
        if window_changed:
            try:
                verify = await set_booking_window(30)
                if verify != "30":
                    raise RuntimeError(f"後台驗證失敗，max-week-0={verify}")
                print("[後台] ✅ 預約窗口已還原為 30 天")
            except Exception as error:
                print(f"[後台] ⚠️ 無法還原天數（{error}），請手動改回 30 天！")

    print("\n===== 預約結果 =====")
    for result in results:
        icon = "✅" if result["success"] else "❌"
        print(f"{icon} {result['date']}: {result['msg']}")

    with open("/tmp/gobooking_results.json", "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("\n結果存至 /tmp/gobooking_results.json")
    return results


def build_cancel_test_dates(days: int = DEFAULT_CANCEL_TEST_DAYS) -> list[str]:
    today = date.today()
    return [
        (today + timedelta(days=offset)).strftime("%Y/%m/%d")
        for offset in range(1, days + 1)
    ]


async def run_cancel_test(days: int = DEFAULT_CANCEL_TEST_DAYS, dry_run: bool = False) -> dict:
    request_dates = build_cancel_test_dates(days)
    window_changed = False

    try:
        verify = await set_booking_window(100)
        if verify != "100":
            raise RuntimeError(f"後台驗證失敗，max-week-0={verify}")
        window_changed = True
        print("[後台] ✅ 預約窗口已設為 100 天")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()

            booking_result = None
            successful_request = None
            for date_str in request_dates:
                request = BookingRequest(date=date_str, start="03:00", end="04:00")
                result = await book_one(page, request, dry_run=dry_run)
                if result["success"]:
                    booking_result = result
                    successful_request = request
                    break
                print(f"[cancel-test] 跳過 {date_str}: {result['msg']}")

            await browser.close()

        if not booking_result or not successful_request:
            raise RuntimeError(f"近 {days} 天找不到可預約的 03:00~04:00 測試時段")

        if dry_run:
            return {
                "success": True,
                "book": booking_result,
                "cancel": {"success": True, "message": "DRY_RUN"},
            }

        cancel_result = await run_cancel(
            name=successful_request.name,
            phone=successful_request.phone,
            date_str=successful_request.date,
            mode="cancel",
        )
        return {"success": cancel_result["success"], "book": booking_result, "cancel": cancel_result}
    finally:
        if window_changed:
            try:
                verify = await set_booking_window(30)
                if verify != "30":
                    raise RuntimeError(f"後台驗證失敗，max-week-0={verify}")
                print("[後台] ✅ 預約窗口已還原為 30 天")
            except Exception as error:
                print(f"[後台] ⚠️ 無法還原天數（{error}），請手動改回 30 天！")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只查詢既有日期是否有衝突")
    parser.add_argument("--test", action="store_true", help="只跑待預約清單中的第一筆")
    parser.add_argument("--cancel-test", action="store_true", help="預約近期 03:00~04:00 一筆後立刻取消")
    parser.add_argument("--dry-run", action="store_true", help="跑流程但不送出")
    parser.add_argument("--name",   required=True,  help="聯絡人姓名（必填）")
    parser.add_argument("--phone",  required=True,  help="聯絡人電話（必填）")
    parser.add_argument("--email",  required=True,  help="聯絡人 email（必填）")
    parser.add_argument("--coupon", default="",     help="優惠碼（選填）")
    parser.add_argument("--dates",  nargs="+",      help="要預約的日期清單，空格隔開，如 2026/05/17 2026/05/24 ...。不給則用腳本內 PENDING_BATCH_DATES")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    if args.check:
        print("=== 查詢空檔 ===")
        await check_availability(CHECK_DATES)
        return

    if args.cancel_test:
        print(f"=== cancel-test：近期 03:00~04:00 {'(DRY RUN)' if args.dry_run else ''} ===")
        result = await run_cancel_test(dry_run=args.dry_run)
        if not result["success"]:
            raise SystemExit(1)
        return

    dates = args.dates if args.dates else PENDING_BATCH_DATES
    if not dates:
        print("⚠️ 無預約日期！請用 --dates 2026/05/17 2026/05/24 ... 傳入日期清單")
        return

    if args.test:
        print(f"=== 測試模式 {'(DRY RUN)' if args.dry_run else ''} ===")
        await run_booking(dates, dry_run=args.dry_run, stop_after_first_success=True,
                          name=args.name, phone=args.phone, email=args.email, coupon=args.coupon)
        return

    print(f"=== 批量預約 {len(dates)} 場 {'(DRY RUN)' if args.dry_run else ''} ===")
    await run_booking(dates, dry_run=args.dry_run,
                      name=args.name, phone=args.phone, email=args.email, coupon=args.coupon)


if __name__ == "__main__":
    asyncio.run(main())
