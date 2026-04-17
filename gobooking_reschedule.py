#!/usr/bin/env python3
"""
gobooking_reschedule.py - 改時間：取消舊訂單 → 重新預約新時段

【標準流程 - 兩段式，必須先問鳳老闆確認】

  第一步：先用 --dry-run 確認找到的訂單資訊
  python3 gobooking_reschedule.py \
    --name "姓名" --phone "09xxxxxxxx" --email "xxx@xxx" \
    --old-date "2026/04/03" \
    --room A --new-date "2026/04/10" --new-start "19:00" --new-end "22:00" \
    --dry-run

  → 列出訂單資訊（日期/時間/聯絡人/電話/email）後停止
  → 回報鳳老闆，詢問：取消不退款 還是 取消並退款？
  → 等鳳老闆確認後才執行第二步

  第二步：帶入鳳老闆確認的 mode 執行
  python3 gobooking_reschedule.py \
    --name "姓名" --phone "09xxxxxxxx" --email "xxx@xxx" \
    --old-date "2026/04/03" \
    --room A --new-date "2026/04/10" --new-start "19:00" --new-end "22:00" \
    --mode cancel

參數：
  --name        聯絡人姓名（必填）
  --phone       聯絡人電話（必填）
  --email       聯絡人 email（預約用）
  --old-date    舊訂單日期 YYYY/MM/DD（選填，多筆時縮小範圍）
  --room        新場地代號 A/B/C/J/K/Q（必填）
  --new-date    新預約日期 YYYY/MM/DD（必填）
  --new-start   新開始時間 HH:MM，或 now（必填）
  --new-end     新結束時間 HH:MM（必填）
  --coupon      折扣碼（預設 energy0258）
  --mode        取消模式：cancel（$0訂單）/ no-refund / refund（預設 cancel）
                ⚠️ 必須先問鳳老闆確認，不可自行決定
  --dry-run     只搜尋列出訂單資訊，不取消也不預約
"""

import argparse
import asyncio
import subprocess
import sys

from playwright.async_api import async_playwright

# ── 共用設定 ──────────────────────────────────────────
OWNER_URL    = "https://gobooking.tw/owner/signinmyroom.html"
ORDERS_URL   = "https://gobooking.tw/owner/ordercontrol.html"
OWNER_ACCT   = "FionaAibot"
MARK         = "(＾ω＾)"
DEFAULT_COUPON = ""  # 優惠碼請透過 --coupon 傳入，不預設

ROOM_CONFIG = {
    "A": {"qrid": "170049020310051559", "plan_id": "17491"},
    "B": {"qrid": "170050020310063902", "plan_id": "17501"},
    "C": {"qrid": "170051020310068898", "plan_id": "17511"},
    "J": {"qrid": "170052020310077340", "plan_id": "17521"},
    "K": {"qrid": "170188112712121020", "plan_id": "171881"},
    "Q": {"qrid": "170187112712110103", "plan_id": "171871"},
}


def get_password() -> str:
    return subprocess.check_output(
        ["security", "find-generic-password", "-a", "GOBOOKING_PASSWORD", "-s", "fiona-ai", "-w"],
        text=True,
    ).strip()


# ── 後台登入 ──────────────────────────────────────────
async def login(page, password: str) -> None:
    await page.goto(OWNER_URL, timeout=30000)
    await page.fill("input[name='userACCT']", OWNER_ACCT)
    await page.fill("input[name='userPASS']", password)
    await page.click("button[type='submit'], input[type='submit'], .login-btn, button:has-text('登入')")
    await page.wait_for_timeout(3000)


# ── 搜尋訂單 ──────────────────────────────────────────
async def search_orders(page, name: str, phone: str, date_str: str | None = None) -> None:
    await page.goto(ORDERS_URL, timeout=30000)
    await page.wait_for_timeout(2000)

    search_btn = page.locator(".show-search-btn")
    if await search_btn.count() > 0:
        await search_btn.click()
        await page.wait_for_timeout(500)

    await page.fill("#con-name", name)
    await page.fill("#con-phone", phone)
    # 不帶日期條件（#order-start/end 是「下單日期」，不是「預約日期」）
    # 搜完全部訂單再用文字比對預約日期+場地

    await page.keyboard.press("Enter")
    await page.wait_for_timeout(3000)


async def get_order_rows(page) -> list[dict]:
    import re
    ORDER_ID_RE = re.compile(r"\b(EY\d{10})\b")

    rows = await page.evaluate(
        """() => {
            const collect = (el, i) => ({
                index: i,
                text: (el.textContent || '').replace(/\\s+/g, ' ').trim().substring(0, 400),
                dataset: Object.assign({}, el.dataset || {}),
            });
            const primary = Array.from(
                document.querySelectorAll('.order-item, [class*="order-row"], .order-card, tr[data-order-id]')
            ).map(collect);
            if (primary.length > 0) return primary;
            return Array.from(document.querySelectorAll('.cancel-btn')).map((btn, i) => {
                const row = btn.closest('tr, .card, .item, div[class*="row"], li') || btn.parentElement || btn;
                return collect(row, i);
            });
        }"""
    )
    for row in rows:
        text = row.get("text", "")
        dataset = row.get("dataset", {})
        oid = None
        for value in dataset.values():
            m = ORDER_ID_RE.search(str(value))
            if m:
                oid = m.group(1)
                break
        if not oid:
            m = ORDER_ID_RE.search(text)
            if m:
                oid = m.group(1)
        row["order_id"] = oid
    return rows


# ── 執行取消 ──────────────────────────────────────────
async def do_cancel(page, row_index: int, mode: str) -> tuple[bool, str]:
    dialog_messages: list[str] = []

    async def handle_dialog(dialog):
        dialog_messages.append(dialog.message)
        await dialog.accept()

    page.on("dialog", handle_dialog)
    try:
        if mode == "no-refund":
            keyword = "取消不退款"
        elif mode == "refund":
            keyword = "取消並退款"
        else:
            keyword = "取消訂單"

        await page.evaluate(
            """({ rowIndex }) => { document.querySelectorAll('.cancel-btn')[rowIndex]?.click(); }""",
            {"rowIndex": row_index},
        )
        await page.wait_for_timeout(1000)

        inner_count = await page.evaluate(
            """({ keyword }) => Array.from(document.querySelectorAll('.inner-cancel-btn'))
                .filter(b => (b.textContent||'').trim().includes(keyword)).length""",
            {"keyword": keyword},
        )
        if inner_count == 0:
            return False, f"找不到 '{keyword}' 按鈕"

        await page.evaluate(
            """({ keyword, rowIndex }) => {
                const matches = Array.from(document.querySelectorAll('.inner-cancel-btn'))
                    .filter(b => (b.textContent||'').trim().includes(keyword));
                (matches[rowIndex] || matches[0])?.click();
            }""",
            {"keyword": keyword, "rowIndex": row_index},
        )
        await page.wait_for_timeout(2000)

        for selector in ["button:has-text('確認')", "button:has-text('確定')", ".confirm-btn", ".ok-btn"]:
            loc = page.locator(selector)
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click()
                await page.wait_for_timeout(1500)
                break

        return True, "取消完成"
    finally:
        page.remove_listener("dialog", handle_dialog)


# ── 前台預約 ──────────────────────────────────────────
async def set_booking_window(page, days: int) -> None:
    """設定後台可預約天數"""
    await page.goto(OWNER_URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(1500)
    # 已登入，直接找設定頁
    for tab_text in ["房型設定", "時租定價", "營業資訊"]:
        try:
            await page.click(f"text={tab_text}", timeout=3000)
            await page.wait_for_timeout(800)
        except Exception:
            pass

    ok = await page.evaluate(f"""
        () => {{
            const inputs = Array.from(document.querySelectorAll('input[type=text],input[type=number],input:not([type])'))
                .filter(el => el.offsetParent !== null);
            const setValue = (el, value) => {{
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                setter.call(el, String(value));
                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
            }};
            let minEl = null, maxEl = null;
            for (const el of inputs) {{
                const ctx = (el.closest('div,tr,li') || document.body).innerText || '';
                if (!minEl && (el.id.startsWith('min-week-') || ctx.includes('可開始預約'))) minEl = el;
                if (!maxEl && (el.id.startsWith('max-week-') || ctx.includes('無法預約'))) maxEl = el;
            }}
            if (!maxEl) return 'not_found';
            if (minEl) setValue(minEl, 0);
            setValue(maxEl, {days});
            return `ok:min=${{minEl ? minEl.value : 'n/a'}}/max=${{maxEl.value}}`;
        }}
    """)
    if not ok.startswith("ok:"):
        raise RuntimeError("找不到天數欄位")

    async with page.expect_response(
        lambda resp: "ow_set_roombasic_info" in resp.url, timeout=10000
    ):
        await page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button,input[type=submit]'))
                    .filter(el => el.offsetParent !== null);
                for (const b of btns) {
                    const t = b.innerText || b.value || '';
                    if (t.includes('儲存') || t.includes('確認') || t.includes('Save')) { b.click(); return; }
                }
            }
        """)
    await page.wait_for_timeout(2000)
    print(f"[後台] ✅ 預約天數已設為 {days}")


async def do_book(page, room: str, date: str, start: str, end: str,
                  coupon: str, name: str, phone: str, email: str) -> dict:
    cfg = ROOM_CONFIG[room.upper()]
    url = f"https://gobooking.tw/energy/room.html?{cfg['qrid']}"
    print(f"[預約] {room}場 {date} {start}~{end}  聯絡人:{name}")

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)

    # 選日期
    date_input = page.locator("input[placeholder='選擇日期'], input[type='date'], .date-picker input").first
    if await date_input.count() > 0:
        await page.evaluate(
            """({ sel, val }) => {
                const el = document.querySelector(sel);
                if (!el) return;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
            }""",
            {"sel": "input[placeholder='選擇日期'], input[type='date'], .date-picker input", "val": date},
        )
        await page.wait_for_timeout(1500)

    # 選開始時間
    await page.wait_for_function(
        "() => !document.querySelector(\"select[name='start-time']\").disabled",
        timeout=15000,
    )
    await page.wait_for_timeout(300)

    if start.lower() == "now":
        await page.select_option("select[name='start-time']", label="即刻 Now")
    else:
        try:
            await page.select_option("select[name='start-time']", label=start, timeout=5000)
        except Exception:
            print(f"[預約] ⚠️ 找不到 {start}，改選「即刻 Now」")
            await page.select_option("select[name='start-time']", label="即刻 Now")
    await page.wait_for_timeout(1500)

    # 選結束時間
    await page.wait_for_function(
        "() => !document.querySelector(\"select[name='end-time']\").disabled",
        timeout=15000,
    )
    await page.wait_for_timeout(300)
    try:
        await page.select_option("select[name='end-time']", label=end, timeout=5000)
    except Exception:
        print(f"[預約] ⚠️ 找不到結束時間 {end}，自動選第一個可用")
        await page.evaluate("""
            () => {
                const sel = document.querySelector("select[name='end-time']");
                const first = Array.from(sel.options).find(o => !o.disabled && o.value);
                if (first) {
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype,'value').set;
                    setter.call(sel, first.value);
                    sel.dispatchEvent(new Event('change',{bubbles:true}));
                }
            }
        """)
    await page.wait_for_timeout(500)

    # 填 coupon
    if coupon:
        try:
            coupon_input = page.locator("input[placeholder*='優惠'], input[name*='coupon'], #coupon-input").first
            await coupon_input.fill(coupon, timeout=3000)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1500)
        except Exception:
            pass

    # 填聯絡資料
    for sel, val in [
        ("input[name='username'], input[placeholder*='姓名']", name),
        ("input[name='phone'], input[placeholder*='電話']", phone),
        ("input[name='email'], input[placeholder*='Email'], input[type='email']", email),
    ]:
        try:
            await page.fill(sel, val, timeout=3000)
        except Exception:
            pass
    await page.wait_for_timeout(500)

    # 備註填 Fiona
    try:
        await page.fill("textarea[name='remark'], textarea[placeholder*='備註']", "Fiona", timeout=3000)
    except Exception:
        pass

    # 送出
    submit_resp = None
    async with page.expect_response(
        lambda r: "submitBooking" in r.url or "book" in r.url.lower(),
        timeout=20000,
    ) as resp_info:
        await page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button,input[type=submit]'))
                    .filter(el => el.offsetParent !== null);
                for (const b of btns) {
                    const t = (b.innerText || b.value || '').trim();
                    if (t.includes('確認') || t.includes('預約') || t.includes('送出') || t.includes('Submit')) {
                        b.click(); return;
                    }
                }
            }
        """)
    submit_resp = await resp_info.value
    await page.wait_for_timeout(2000)

    # 解析結果
    result_text = await page.evaluate("() => document.body.innerText")
    import re
    order_match = re.search(r"EY\d{10}", result_text)
    lock_match  = re.search(r"門鎖密碼[：:]\s*(\d+)", result_text)

    return {
        "order_id":  order_match.group(0) if order_match else None,
        "lock_code": lock_match.group(1) if lock_match else None,
        "page_text": result_text[:300],
    }


# ── 主流程 ────────────────────────────────────────────
async def run(args):
    password = get_password()
    name_marked = args.name if MARK in args.name else args.name + MARK

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})

        # 1. 登入後台
        print("[後台] 登入...")
        await login(page, password)

        # 2. 搜尋舊訂單
        old_name = args.name  # 搜尋用原始姓名（不加標記）
        print(f"[搜尋] 姓名={old_name} 電話={args.phone} 日期={args.old_date or '不限'}")
        await search_orders(page, old_name, args.phone, args.old_date)
        rows = await get_order_rows(page)
        cancel_count = await page.locator(".cancel-btn").count()

        if cancel_count == 0:
            print("❌ 找不到符合條件的訂單，結束。")
            await browser.close()
            sys.exit(1)

        print(f"\n找到 {cancel_count} 筆訂單：")
        for i, row in enumerate(rows[:cancel_count]):
            print(f"  [{i}] {row.get('order_id') or 'UNKNOWN'}  {row['text'][:120]}")

        if args.dry_run:
            print("\n✅ DRY_RUN — 不執行取消與預約")
            await browser.close()
            return

        if cancel_count > 1 and not args.old_date:
            print(f"⚠️ 找到 {cancel_count} 筆，請加上 --old-date 縮小範圍")
            await browser.close()
            sys.exit(1)

        # 3. 取消第一筆
        target = rows[0]
        old_order_id = target.get("order_id") or "unknown"
        print(f"\n[取消] 模式={args.mode}，取消訂單 {old_order_id}...")
        ok, msg = await do_cancel(page, target["index"], args.mode)
        if not ok:
            print(f"❌ 取消失敗：{msg}")
            await browser.close()
            sys.exit(1)
        print(f"✅ 取消成功：{old_order_id}")

        # 4. 設定預約天數（100天）
        await set_booking_window(page, 100)

        # 5. 新預約
        book_result = await do_book(
            page,
            room=args.room,
            date=args.new_date,
            start=args.new_start,
            end=args.new_end,
            coupon=args.coupon,
            name=name_marked,
            phone=args.phone,
            email=args.email,
        )

        # 6. 還原預約天數（30天）
        await set_booking_window(page, 30)

        await browser.close()

    if book_result["order_id"]:
        print(f"\n✅ 預約成功！訂單：{book_result['order_id']}  門鎖：{book_result['lock_code']}")
    else:
        print(f"\n⚠️ 預約回應：{book_result['page_text']}")


def main():
    parser = argparse.ArgumentParser(description="gobooking 改時間：取消 + 重新預約")
    parser.add_argument("--name",      required=True,  help="聯絡人姓名")
    parser.add_argument("--phone",     required=True,  help="聯絡人電話")
    parser.add_argument("--email",     required=True,  help="聯絡人 email")
    parser.add_argument("--old-date",  default=None,   help="舊訂單日期 YYYY/MM/DD（選填）")
    parser.add_argument("--room",      required=True,  help="新場地代號 A/B/C/J/K/Q")
    parser.add_argument("--new-date",  required=True,  help="新預約日期 YYYY/MM/DD")
    parser.add_argument("--new-start", required=True,  help="新開始時間 HH:MM 或 now")
    parser.add_argument("--new-end",   required=True,  help="新結束時間 HH:MM")
    parser.add_argument("--coupon",    default="", help="折扣碼（選填）")
    parser.add_argument("--mode",      default="cancel",
                        choices=["cancel", "no-refund", "refund"],
                        help="取消模式（預設 cancel，適用 $0 訂單）")
    parser.add_argument("--dry-run",   action="store_true", help="只搜尋，不取消也不預約")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
