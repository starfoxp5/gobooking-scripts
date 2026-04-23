#!/usr/bin/env python3
"""
gobooking_playwright.py - 活力羽球館 前台自動預約（通用版）

支援模式：
  1. 單次預約
     python3 gobooking_playwright.py --room A --date 2026/05/17 --start 10:00 --end 12:00 \\
       --name "姓名" --phone "09xx" --email "xxx@xxx" --dry-run

  2. 套票預約
     python3 gobooking_playwright.py --room A --date 2026/05/17 --start 13:30 --end 14:30 \\
       --name "姓名" --phone "09xx" --email "xxx@xxx" --ticket TEYxxx --dry-run

  3. 套票 + 優惠碼
     python3 gobooking_playwright.py --room A --date 2026/05/17 --start 14:30 --end 15:00 \\
       --name "姓名" --phone "09xx" --email "xxx@xxx" --ticket TEYxxx --coupon energy0258 --dry-run

  4. 純優惠碼預約
     python3 gobooking_playwright.py --room A --date 2026/05/17 --start 10:00 --end 12:00 \\
       --name "姓名" --phone "09xx" --email "xxx@xxx" --coupon energy0258 --dry-run

⚠️ 規則：
  - 執行前必須先加 --dry-run 確認計畫，回報鳳老闆後才能去掉 --dry-run 正式執行
  - --name / --phone / --email 為必填，不得留空
  - 執行完輸出明細：訂單號、日期時間、門鎖密碼
"""

import asyncio
import argparse
import sys
import subprocess

from playwright.async_api import async_playwright

BACKEND_URL  = "https://gobooking.tw/owner/signinmyroom.html"
BACKEND_USER = "FionaAibot"

def _get_gobooking_password() -> str:
    r = subprocess.run(
        ["security","find-generic-password","-a","GOBOOKING_PASSWORD","-s","fiona-ai","-w"],
        capture_output=True, text=True
    )
    pw = r.stdout.strip()
    if not pw:
        # fallback: 直接從已知密碼取（Keychain 未設定時使用）
        pw = "52640246"
    return pw

BACKEND_ROOM_SETTING_URL = "https://gobooking.tw/owner/v2/room-setting.html"

async def set_booking_window(days: int):
    """登入後台，將 A 場「預約結束 N 日後無法預約」改為指定天數。"""
    password = _get_gobooking_password()
    print(f"[後台] 設定預約天數 → {days} 天")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width":1280,"height":900})
        await page.goto(BACKEND_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        await page.fill("input[name='userACCT']", BACKEND_USER)
        await page.fill("input[name='userPASS']", password)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        # 直接導航到房型設定頁（避免落地頁差異）
        await page.goto(BACKEND_ROOM_SETTING_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        for tab_text in ["房型設定", "時租定價", "營業資訊"]:
            try:
                await page.click(f"text={tab_text}", timeout=3000)
                await page.wait_for_timeout(800)
            except:
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

                let minEl = null;
                let maxEl = null;
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
            await browser.close()
            raise RuntimeError("找不到天數欄位")

        async with page.expect_response(
            lambda resp: "ow_set_roombasic_info" in resp.url,
            timeout=10000,
        ) as save_resp_info:
            await page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button,input[type=submit]'))
                        .filter(el => el.offsetParent !== null);
                    for (const b of btns) {
                        const t = b.innerText || b.value || '';
                        if (t.includes('儲存') || t.includes('確認') || t.includes('Save')) {
                            b.click(); return;
                        }
                    }
                }
            """)
        save_resp = await save_resp_info.value
        if save_resp.status != 200:
            await browser.close()
            raise RuntimeError(f"儲存失敗，HTTP {save_resp.status}")
        await page.wait_for_timeout(2000)
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        await browser.close()
    print(f"[後台] ✅ 天數已設為 {days}")

async def set_min_booking_minutes(minutes: int, rooms: list | None = None):
    """
    登入後台，把指定場館（預設全部）的所有方案「最短時租」改為指定分鐘數。
    minutes=30 → value="1"（0.5小時）
    minutes=60 → value="2"（1小時）
    """
    # select value mapping
    val = "1" if minutes == 30 else "2"
    password = _get_gobooking_password()

    # QRID per room（與 ROOM_CONFIG 對應）
    ROOM_QRIDS = {
        "A": "170049020310051559",
        "B": "170050020310063902",
        "C": "170051020310068898",
        "J": "170052020310077340",
        "K": "170188112712121020",
        "Q": "170187112712110103",
    }
    target_rooms = rooms if rooms else list(ROOM_QRIDS.keys())

    print(f"[後台] 設定最短時租 → {minutes} 分鐘，場館：{target_rooms}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        # 登入一次
        await page.goto(BACKEND_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        await page.fill("input[name='userACCT']", BACKEND_USER)
        await page.fill("input[name='userPASS']", password)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        for room in target_rooms:
            qrid = ROOM_QRIDS.get(room.upper())
            if not qrid:
                print(f"[後台] ⚠️ 找不到 {room} 場 QRID，跳過")
                continue

            url = f"https://gobooking.tw/owner/inroomsetting.html?{qrid}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # 點「營業資訊」tab
            try:
                await page.click("text=營業資訊", timeout=5000)
                await page.wait_for_timeout(1500)
            except Exception as e:
                print(f"[後台] ⚠️ {room} 場找不到「營業資訊」tab：{e}")
                continue

            # 找所有「最短時租」select 並改值
            changed = await page.evaluate(f"""
                () => {{
                    const selects = Array.from(document.querySelectorAll("select"))
                        .filter(el => el.offsetParent !== null);
                    let count = 0;
                    for (const sel of selects) {{
                        const label = (sel.closest("div,tr,li,td,label") || document.body).innerText || "";
                        if (label.includes("最短時租")) {{
                            const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
                            setter.call(sel, "{val}");
                            sel.dispatchEvent(new Event("input", {{bubbles: true}}));
                            sel.dispatchEvent(new Event("change", {{bubbles: true}}));
                            count++;
                        }}
                    }}
                    return count;
                }}
            """)
            if changed == 0:
                print(f"[後台] ⚠️ {room} 場找不到最短時租 select")
                continue

            # 點儲存
            try:
                async with page.expect_response(
                    lambda resp: "inroomsetting" in resp.url or "roomsetting" in resp.url or "ow_set" in resp.url,
                    timeout=8000,
                ) as resp_info:
                    await page.evaluate("""
                        () => {
                            const btns = Array.from(document.querySelectorAll("button,input[type=submit]"))
                                .filter(el => el.offsetParent !== null);
                            for (const b of btns) {
                                const t = (b.innerText || b.value || "").trim();
                                if (t.includes("儲存") || t.includes("Save") || t.includes("確認")) {
                                    b.click(); return t;
                                }
                            }
                        }
                    """)
                await resp_info.value
            except Exception:
                # 若攔截不到 XHR，直接點儲存不等
                await page.evaluate("""
                    () => {
                        const btns = Array.from(document.querySelectorAll("button,input[type=submit]"))
                            .filter(el => el.offsetParent !== null);
                        for (const b of btns) {
                            const t = (b.innerText || b.value || "").trim();
                            if (t.includes("儲存") || t.includes("Save") || t.includes("確認")) {
                                b.click(); return;
                            }
                        }
                    }
                """)
                await page.wait_for_timeout(2000)

            print(f"[後台] ✅ {room} 場最短時租已設為 {minutes} 分鐘（改了 {changed} 個方案）")

        await browser.close()
    print(f"[後台] ✅ 全部完成：最短時租 = {minutes} 分鐘")



# ── 預設值 ──────────────────────────────────────────────
DEFAULTS = {
    "room":    "A",                       # A 或 B
    "date":    "",                         # YYYY/MM/DD（必填）
    "start":   "19:00",
    "end":     "22:00",
    "coupon":  "",
    "name":    "",                         # 必填，不可為空
    "phone":   "",                         # 必填，不可為空
    "email":   "",                         # 必填，不可為空
}

ROOM_CONFIG = {
    "A": {"qrid": "170049020310051559", "plan_id": "17491"},
    "B": {"qrid": "170050020310063902", "plan_id": "17501"},
    "C": {"qrid": "170051020310068898", "plan_id": "17511"},
    "J": {"qrid": "170052020310077340", "plan_id": "17521"},
    "K": {"qrid": "170188112712121020", "plan_id": "171881"},
    "Q": {"qrid": "170187112712110103", "plan_id": "171871"},
}
# ────────────────────────────────────────────────────────


async def book(room: str, date: str, start: str, end: str,
               coupon: str, name: str, phone: str, email: str,
               dry_run: bool = False, ticket: str = ""):

    # 名字後面自動加上標記（若尚未有）
    MARK = "(＾ω＾)"
    if MARK not in name:
        name = name + MARK

    cfg = ROOM_CONFIG[room.upper()]
    qrid = cfg["qrid"]
    plan_id = cfg["plan_id"]
    url = f"https://gobooking.tw/energy/room.html?{qrid}"

    print(f"[gobooking] 預約 {room}場 {date} {start}~{end}  聯絡人:{name}")
    if dry_run:
        print("[dry-run] 不送出，僅模擬流程")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        booking_response = {}
        async def on_response(resp):
            if "bookingnow" in resp.url:
                try:
                    body = await resp.text()
                except Exception:
                    body = ""
                booking_response["status"] = resp.status
                booking_response["body"] = body[:200]
        page.on("response", on_response)

        # 1. 開頁面
        await page.goto(url)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)

        # 2. 選方案
        await page.select_option("select[name='booking-plan']", value=plan_id)
        await page.wait_for_timeout(1000)

        # 3. 選日期（日曆）
        await page.locator("#date-picker").click()
        await page.wait_for_timeout(1500)

        # 翻到目標月份（最多翻 6 次）
        target_month = date.split("/")[1].lstrip("0")  # "04" → "4"
        month_names = {"1":"January","2":"February","3":"March","4":"April",
                       "5":"May","6":"June","7":"July","8":"August",
                       "9":"September","10":"October","11":"November","12":"December"}
        target_name = month_names[target_month]
        for _ in range(6):
            nav = await page.evaluate(
                "document.querySelector('.air-datepicker-nav--title')?.innerText || ''"
            )
            if target_name in nav:
                break
            await page.locator('[data-action="next"]').first.click()
            await page.wait_for_timeout(500)

        target_day = date.split("/")[2].lstrip("0")  # "23"
        clicked = await page.evaluate(f"""
            () => {{
                for (const c of document.querySelectorAll('.air-datepicker-cell.-day-'))
                    if ((c.textContent||'').trim()==='{target_day}'
                        && !c.classList.contains('-disabled-')
                        && !c.classList.contains('-other-month-')) {{
                        c.click(); return true;
                    }}
                return false;
            }}
        """)
        if not clicked:
            print(f"[ERROR] 日期 {date} 在日曆上是 disabled（超出預約天數）")
            await browser.close()
            return None

        await page.wait_for_timeout(2000)

        # 4. 選時段
        await page.wait_for_function(
            "() => !document.querySelector(\"select[name='start-time']\").disabled",
            timeout=15000
        )
        await page.wait_for_timeout(300)
        # start="now" → 即刻 Now；否則用指定時間 HH:MM
        if start.lower() == "now":
            await page.evaluate("""
                () => {
                    const sel = document.querySelector("select[name='start-time']");
                    const first = Array.from(sel.options).find(o => !o.disabled && o.value);
                    if (first) {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype,'value').set;
                        setter.call(sel, first.value);
                        sel.dispatchEvent(new Event('change', {bubbles:true}));
                    }
                }
            """)
        else:
            try:
                await page.select_option("select[name='start-time']", label=start, timeout=5000)
            except Exception:
                raise RuntimeError(f"找不到開始時間 {start}，中止預約。請確認時間格式正確且該時段存在。")
        await page.wait_for_timeout(1500)
        await page.wait_for_function(
            "() => !document.querySelector(\"select[name='end-time']\").disabled",
            timeout=15000
        )
        await page.wait_for_timeout(300)
        # 若 end="nearest" 或指定時間找不到，自動選第一個可用選項
        if end.lower() == "nearest":
            await page.evaluate("""
                () => {
                    const sel = document.querySelector("select[name='end-time']");
                    const first = Array.from(sel.options).find(o => !o.disabled && o.value);
                    if (first) {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype,'value').set;
                        setter.call(sel, first.value);
                        sel.dispatchEvent(new Event('change', {bubbles:true}));
                    }
                }
            """)
        else:
            try:
                await page.select_option("select[name='end-time']", label=end, timeout=5000)
            except Exception:
                raise RuntimeError(f"找不到結束時間 {end}，中止預約。請確認時間格式正確且該時段存在。")
                if False:
                    pass
                    }
                """)
        await page.wait_for_timeout(800)

        # 5. 點「立即預約」
        await page.get_by_role("button", name="立即預約 Booking").first.click()
        await page.wait_for_selector("#payment-name", timeout=10000)

        # 6. 填付款人資訊
        await page.locator("#payment-name").fill(name)
        await page.wait_for_timeout(200)
        await page.locator("#payment-phone").fill(phone)
        await page.wait_for_timeout(200)
        await page.locator("#payment-email").fill(email)
        await page.wait_for_timeout(200)
        await page.evaluate(
            "document.querySelector('label[for=\"same-as-contact\"]')?.click()"
        )
        await page.wait_for_timeout(500)

        # 7. 套用套票（可與優惠碼同時使用）
        if ticket:
            # 套票模式：填 #pass 套票欄位 → Search → Apply
            print(f"[gobooking] 使用套票: {ticket}")
            await page.locator("#pass").fill(ticket)
            await page.wait_for_timeout(300)
            # 點 #pass 旁的 Search 按鈕
            await page.evaluate("""
                () => {
                    const inp = document.querySelector('#pass');
                    let c = inp?.parentElement;
                    for (let i = 0; i < 5; i++) {
                        const btn = c?.querySelector('button');
                        if (btn) { btn.click(); return; }
                        c = c?.parentElement;
                    }
                }
            """)
            await page.wait_for_timeout(2500)
            # 點 Apply
            await page.evaluate("""
                () => {
                    for (const btn of document.querySelectorAll('button'))
                        if (btn.innerText.trim() === 'Apply') { btn.click(); return; }
                }
            """)
            await page.wait_for_timeout(2000)
            # Apply 後補填付款人（若系統未自動帶入）
            payer_name = await page.locator("#payment-name").input_value()
            if not payer_name.strip():
                await page.locator("#payment-name").fill(name)
                await page.locator("#payment-phone").fill(phone)
                await page.locator("#payment-email").fill(email)
                await page.evaluate(
                    "document.querySelector('label[for=\"same-as-contact\"]')?.click()"
                )
            await page.wait_for_timeout(500)

        # 8. 套用優惠碼（套票後接著用，或單獨使用）
        if coupon and not ticket:
            # 純優惠碼模式（無套票）
            print(f"[gobooking] 使用優惠碼: {coupon}")
            await page.locator("#coupon").fill(coupon)
            await page.wait_for_timeout(300)
            await page.evaluate("""
                () => {
                    const inp = document.querySelector('#coupon');
                    let c = inp?.parentElement;
                    for (let i = 0; i < 5; i++) {
                        const btn = c?.querySelector('button');
                        if (btn) { btn.click(); return; }
                        c = c?.parentElement;
                    }
                }
            """)
            await page.wait_for_timeout(2500)
            await page.evaluate("""
                () => {
                    for (const btn of document.querySelectorAll('button'))
                        if (btn.innerText.trim() === 'Apply') { btn.click(); return; }
                }
            """)
            await page.wait_for_timeout(2000)
        elif coupon and ticket:
            # 套票＋優惠碼同時使用：套票 Apply 後再補折扣碼
            print(f"[gobooking] 套票後補用優惠碼: {coupon}")
            await page.locator("#coupon").fill(coupon)
            await page.wait_for_timeout(300)
            await page.evaluate("""
                () => {
                    const inp = document.querySelector('#coupon');
                    let c = inp?.parentElement;
                    for (let i = 0; i < 5; i++) {
                        const btn = c?.querySelector('button');
                        if (btn) { btn.click(); return; }
                        c = c?.parentElement;
                    }
                }
            """)
            await page.wait_for_timeout(2500)
            await page.evaluate("""
                () => {
                    for (const btn of document.querySelectorAll('button'))
                        if (btn.innerText.trim() === 'Apply') { btn.click(); return; }
                }
            """)
            await page.wait_for_timeout(2000)

        # 確認總計 0
        total = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('tr, [class*="total"], [class*="price"]');
                for (const r of rows) {
                    const t = r.innerText || '';
                    if (t.includes('Total') || t.includes('總價')) return t.trim();
                }
                return '';
            }
        """)
        print(f"[gobooking] 總計確認: {total}")

        if dry_run:
            print("[dry-run] 停在此，不送出預約")
            await page.screenshot(path="/tmp/gobooking_dry_run.png", full_page=True)
            await browser.close()
            return {"dry_run": True, "total": total}

        # 8. 直接呼叫 Pinia booking.submitBooking()（繞過 Vue form validation）
        result = await page.evaluate(f"""
            async () => {{
                try {{
                    const app = document.querySelector('#app').__vue_app__;
                    const pinia = app.config.globalProperties.$pinia;
                    const booking = pinia._s.get('booking');
                    booking.$patch({{
                        paymentName: '{name}',
                        paymentPhone: '{phone}',
                        paymentEmail: '{email}',
                        contactName: '{name}',
                        contactPhone: '{phone}',
                        contactEmail: '{email}',
                        invType: 'member',
                        bookingRemark: '（Fiona^o^）',
                    }});
                    await booking.submitBooking();
                    return 'ok';
                }} catch(e) {{
                    return 'error: ' + e.message;
                }}
            }}
        """)
        print(f"[gobooking] submitBooking: {result}")
        await page.wait_for_timeout(5000)

        final_url = page.url
        page_text = await page.evaluate("document.body.innerText.substring(0, 400)")

        if "reservation" in final_url or "成功" in page_text:
            # 取訂單資訊
            order_id = final_url.split("?")[-1] if "?" in final_url else ""
            password = ""
            import re
            m = re.search(r"門鎖密碼[^：]*：(\d+)", page_text)
            if m:
                password = m.group(1)
            print(f"[gobooking] ✅ 預約成功！訂單：{order_id}  門鎖密碼：{password}")
            await browser.close()
            return {"success": True, "order_id": order_id, "password": password, "url": final_url}
        else:
            print(f"[gobooking] ❌ 預約可能失敗，URL: {final_url}")
            print(page_text[:300])
            await page.screenshot(path="/tmp/gobooking_fail.png", full_page=True)
            await browser.close()
            return {"success": False, "url": final_url, "text": page_text[:300]}


def main():
    parser = argparse.ArgumentParser(description="gobooking 自動預約")
    parser.add_argument("--room",   default=DEFAULTS["room"],   help="A 或 B")
    parser.add_argument("--date",   default=DEFAULTS["date"],   help="YYYY/MM/DD")
    parser.add_argument("--start",  default=DEFAULTS["start"],  help="開始時間 HH:MM，或填 now 選「即刻 Now」")
    parser.add_argument("--end",    default=DEFAULTS["end"],    help="結束時間 HH:MM")
    parser.add_argument("--coupon", default="", help="優惠碼（不傳則不使用）")
    parser.add_argument("--name",   required=True,  help="聯絡人姓名（必填）")
    parser.add_argument("--phone",  required=True,  help="聯絡人電話（必填）")
    parser.add_argument("--email",  required=True,  help="聯絡人 email（必填）")
    parser.add_argument("--ticket", default="",     help="套票代碼（TEY...），帶此參數走套票流程")
    parser.add_argument("--dry-run", action="store_true",       help="只模擬，不送出")
    parser.add_argument("--no-set-days", action="store_true",   help="跳過內部天數設定（批次模式用）")
    parser.add_argument("--min-minutes", type=int, default=0,    help="強制設定最短時租分鐘數（30/60），0=自動偵測）")
    args = parser.parse_args()

    # 自動偵測：booking 時長 < 60 分鐘時需調整最短時租
    need_short_min = False
    if args.start.lower() != "now" and args.end:
        from datetime import datetime as _dt
        try:
            _s = _dt.strptime(args.start, "%H:%M")
            _e = _dt.strptime(args.end, "%H:%M")
            duration_min = int((_e - _s).total_seconds() / 60)
            if 0 < duration_min < 60:
                need_short_min = True
        except Exception:
            pass
    if hasattr(args, "min_minutes") and args.min_minutes:
        need_short_min = True

    async def run():
        # 預約前：後台開放 100 天（除非外部已設定）
        if not args.dry_run and not args.no_set_days:
            try:
                await set_booking_window(100)
            except Exception as e:
                print(f"[後台] ⚠️ 無法設定天數（{e}），仍繼續預約")

        # 預約前：若需 30 分鐘最短時租，先調整
        if not args.dry_run and not args.no_set_days and need_short_min:
            try:
                await set_min_booking_minutes(30)
            except Exception as e:
                print(f"[後台] ⚠️ 無法調整最短時租（{e}），仍繼續預約")

        result = await book(
            room=args.room, date=args.date, start=args.start, end=args.end,
            coupon=args.coupon, name=args.name, phone=args.phone, email=args.email,
            dry_run=args.dry_run, ticket=args.ticket,
        )

        # 預約後：還原最短時租 → 60 分鐘
        if not args.dry_run and not args.no_set_days and need_short_min:
            try:
                await set_min_booking_minutes(60)
            except Exception as e:
                print(f"[後台] ⚠️ 無法還原最短時租（{e}），請手動改回 60 分鐘！")

        # 預約後：後台改回 30 天（除非外部控制）
        if not args.dry_run and not args.no_set_days:
            try:
                await set_booking_window(30)
            except Exception as e:
                print(f"[後台] ⚠️ 無法還原天數（{e}），請手動改回 30 天！")

        return result

    result = asyncio.run(run())
    if result and not result.get("success") and not result.get("dry_run"):
        sys.exit(1)


if __name__ == "__main__":
    main()
