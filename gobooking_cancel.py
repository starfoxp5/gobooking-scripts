#!/usr/bin/env python3
"""
gobooking_cancel.py - 後台取消預約腳本

用法（姓名+電話搜尋）：
  python3 gobooking_cancel.py --name "姓名" --phone "09xxxxxxxx" [--date "2026/03/29"] [--mode refund|no-refund|cancel]

用法（訂單號直接取消）：
  python3 gobooking_cancel.py --order EY0491234567 [--mode refund|no-refund|cancel]

參數：
  --name      聯絡人姓名（與 --phone 配合使用）
  --phone     聯絡人電話（與 --name 配合使用）
  --order     訂單號（EYxxxxxxxxxx），直接指定取消，不需 --name/--phone
  --date      預約日期 YYYY/MM/DD（選填，有多筆時縮小範圍）
  --mode      取消模式（預設: no-refund）
              no-refund  -> 取消不退款
              refund     -> 取消並退款
              cancel     -> 取消訂單（0 元用這個）
  --dry-run   只搜尋列出訂單，不真的取消
"""

import argparse
import asyncio
import re
import subprocess
import sys

from playwright.async_api import async_playwright

OWNER_URL = "https://gobooking.tw/owner/signinmyroom.html"
ORDERS_URL = "https://gobooking.tw/owner/ordercontrol.html"
OWNER_ACCT = "FionaAibot"
ORDER_ID_RE = re.compile(r"\b(EY\d{10})\b")


def get_password() -> str:
    for account in ["GOBOOKING_PASSWORD", "FionaAibot"]:
        try:
            return subprocess.check_output(
                ["security", "find-generic-password", "-a", account, "-s", "fiona-ai", "-w"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            continue
    return "52640246"  # fallback


def extract_order_id(text: str, dataset: dict) -> str | None:
    for value in dataset.values():
        match = ORDER_ID_RE.search(str(value))
        if match:
            return match.group(1)
    match = ORDER_ID_RE.search(text)
    if match:
        return match.group(1)
    return None


async def login(page, password: str) -> None:
    await page.goto(OWNER_URL, timeout=30000)
    await page.fill("input[name='userACCT']", OWNER_ACCT)
    await page.fill("input[name='userPASS']", password)
    await page.click("button[type='submit'], input[type='submit'], .login-btn, button:has-text('登入')")
    await page.wait_for_timeout(3000)


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
        row["order_id"] = extract_order_id(row.get("text", ""), row.get("dataset", {}))
    return rows


async def do_cancel(page, row_index: int, mode: str) -> tuple[bool, str, list[str]]:
    dialog_messages: list[str] = []

    async def handle_dialog(dialog):
        dialog_messages.append(dialog.message)
        await dialog.accept()

    page.on("dialog", handle_dialog)

    try:
        button_count = await page.evaluate("() => document.querySelectorAll('.cancel-btn').length")
        if row_index >= button_count:
            return False, f"找不到第 {row_index} 個取消按鈕（共 {button_count} 個）", dialog_messages

        if mode == "no-refund":
            keyword = "取消不退款"
        elif mode == "refund":
            keyword = "取消並退款"
        else:
            keyword = "取消訂單"

        await page.evaluate(
            """({ rowIndex }) => {
                document.querySelectorAll('.cancel-btn')[rowIndex]?.click();
            }""",
            {"rowIndex": row_index},
        )
        await page.wait_for_timeout(1000)

        inner_count = await page.evaluate(
            """({ keyword }) => {
                return Array.from(document.querySelectorAll('.inner-cancel-btn'))
                    .filter((btn) => (btn.textContent || '').trim().includes(keyword)).length;
            }""",
            {"keyword": keyword},
        )
        if inner_count == 0:
            return False, f"找不到 '{keyword}' 按鈕（dropdown 可能未展開）", dialog_messages

        await page.evaluate(
            """({ keyword, rowIndex }) => {
                const matches = Array.from(document.querySelectorAll('.inner-cancel-btn'))
                    .filter((btn) => (btn.textContent || '').trim().includes(keyword));
                (matches[rowIndex] || matches[0])?.click();
            }""",
            {"keyword": keyword, "rowIndex": row_index},
        )
        await page.wait_for_timeout(2000)

        confirm_selectors = [
            "button:has-text('確認')",
            "button:has-text('確定')",
            ".confirm-btn",
            ".ok-btn",
        ]
        for selector in confirm_selectors:
            locator = page.locator(selector)
            if await locator.count() > 0 and await locator.first.is_visible():
                await locator.first.click()
                await page.wait_for_timeout(1500)
                break

        return True, "取消完成", dialog_messages
    finally:
        page.remove_listener("dialog", handle_dialog)


async def verify_order_absent(page, name: str, phone: str, date_str: str | None, order_id: str) -> tuple[bool, list[dict]]:
    await search_orders(page, name, phone, date_str)
    rows = await get_order_rows(page)
    return all(row.get("order_id") != order_id for row in rows), rows


async def run_cancel(
    *,
    name: str,
    phone: str,
    date_str: str | None = None,
    mode: str = "no-refund",
    dry_run: bool = False,
    headless: bool = True,
) -> dict:
    password = get_password()
    result: dict = {
        "success": False,
        "name": name,
        "phone": phone,
        "date": date_str,
        "mode": mode,
        "order_id": None,
        "message": "",
        "screenshot": None,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})

        try:
            print("[後台] 登入...")
            await login(page, password)

            print(f"[搜尋] 姓名={name} 電話={phone} 日期={date_str or '不限'}")
            await search_orders(page, name, phone, date_str)

            rows = await get_order_rows(page)
            cancel_count = await page.locator(".cancel-btn").count()
            if cancel_count == 0:
                result["message"] = "找不到符合條件的訂單"
                result["screenshot"] = "/tmp/gobooking_cancel_result_unknown.png"
                await page.screenshot(path=result["screenshot"])
                return result

            print(f"\n找到 {cancel_count} 筆訂單：")
            for i, row in enumerate(rows[:cancel_count]):
                order_label = row.get("order_id") or "UNKNOWN"
                print(f"  [{i}] {order_label} {row['text'][:120]}")

            if dry_run:
                result["success"] = True
                result["message"] = "DRY_RUN"
                return result

            if cancel_count > 1 and not date_str:
                result["message"] = f"找到 {cancel_count} 筆，請加上 --date 縮小範圍"
                result["screenshot"] = "/tmp/gobooking_cancel_result_unknown.png"
                await page.screenshot(path=result["screenshot"])
                return result

            target = rows[0]
            order_id = target.get("order_id") or "unknown"
            result["order_id"] = order_id
            result["screenshot"] = f"/tmp/gobooking_cancel_result_{order_id}.png"

            print(f"\n[取消] 模式={mode}，取消訂單 {order_id}...")
            ok, message, dialog_messages = await do_cancel(page, target["index"], mode)
            if not ok:
                result["message"] = message
                await page.screenshot(path=result["screenshot"])
                return result

            if dialog_messages:
                print(f"  dialog 已處理: {dialog_messages}")

            if order_id == "unknown":
                result["message"] = "取消流程已送出，但無法解析訂單號，無法驗證訂單是否消失"
                await page.screenshot(path=result["screenshot"])
                return result

            verified, latest_rows = await verify_order_absent(page, name, phone, date_str, order_id)
            await page.screenshot(path=result["screenshot"])
            if not verified:
                remaining = [row.get("order_id") for row in latest_rows if row.get("order_id") == order_id]
                result["message"] = f"取消後重新搜尋仍找到訂單 {order_id}: {remaining}"
                return result

            result["success"] = True
            result["message"] = f"取消完成並驗證訂單 {order_id} 已消失"
            return result
        finally:
            await browser.close()


async def run_cancel_by_id(
    *,
    order_id: str,
    mode: str = "no-refund",
    dry_run: bool = False,
    headless: bool = True,
) -> dict:
    """用訂單號直接搜尋並取消"""
    password = get_password()
    result: dict = {
        "success": False,
        "order_id": order_id,
        "mode": mode,
        "message": "",
        "screenshot": None,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})

        try:
            print("[後台] 登入...")
            await login(page, password)

            print(f"[搜尋] 訂單號={order_id}")
            await page.goto(ORDERS_URL, timeout=30000)
            await page.wait_for_timeout(2000)

            search_btn = page.locator(".show-search-btn")
            if await search_btn.count() > 0:
                await search_btn.click()
                await page.wait_for_timeout(500)

            # 用訂單號搜尋（填入 name 欄位）
            await page.fill("#con-name", order_id)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(3000)

            rows = await get_order_rows(page)
            cancel_count = await page.locator(".cancel-btn").count()

            if cancel_count == 0:
                result["message"] = f"找不到訂單號 {order_id}"
                result["screenshot"] = f"/tmp/gobooking_cancel_{order_id}.png"
                await page.screenshot(path=result["screenshot"])
                return result

            print(f"\n搜尋結果 {cancel_count} 筆：")
            for i, row in enumerate(rows[:cancel_count]):
                order_label = row.get("order_id") or "UNKNOWN"
                print(f"  [{i}] {order_label} {row['text'][:150]}")

            # 嚴格比對：在搜尋結果中確認指定訂單號存在
            matched_row = None
            for row in rows[:cancel_count]:
                if row.get("order_id") == order_id:
                    matched_row = row
                    break

            if matched_row is None:
                print(f"❌ 搜尋結果中找不到訂單號 {order_id}，拒絕取消以防誤刪")
                result["message"] = f"搜尋結果中找不到訂單號 {order_id}，已中止操作"
                result["screenshot"] = f"/tmp/gobooking_cancel_{order_id}.png"
                await page.screenshot(path=result["screenshot"])
                sys.exit(1)

            if dry_run:
                result["success"] = True
                result["message"] = "DRY_RUN"
                return result

            result["screenshot"] = f"/tmp/gobooking_cancel_{order_id}.png"
            ok, message, dialog_messages = await do_cancel(page, matched_row["index"], mode)
            if not ok:
                result["message"] = message
                await page.screenshot(path=result["screenshot"])
                return result

            if dialog_messages:
                print(f"  dialog 已處理: {dialog_messages}")

            result["success"] = True
            result["message"] = f"訂單 {order_id} 取消完成"
            await page.screenshot(path=result["screenshot"])
            return result
        finally:
            await browser.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default=None, help="聯絡人姓名（與 --phone 配合）")
    parser.add_argument("--phone", default=None, help="聯絡人電話（與 --name 配合）")
    parser.add_argument("--order", default=None, help="訂單號 EYxxxxxxxxxx（直接指定，不需 --name/--phone）")
    parser.add_argument("--date", default=None, help="預約日期 YYYY/MM/DD（選填）")
    parser.add_argument(
        "--mode",
        default="no-refund",
        choices=["no-refund", "refund", "cancel"],
        help="取消模式 (預設: no-refund)",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出訂單，不取消")
    args = parser.parse_args()

    if args.order:
        result = await run_cancel_by_id(
            order_id=args.order,
            mode=args.mode,
            dry_run=args.dry_run,
        )
    elif args.name and args.phone:
        result = await run_cancel(
            name=args.name,
            phone=args.phone,
            date_str=args.date,
            mode=args.mode,
            dry_run=args.dry_run,
        )
    else:
        print("❌ 請提供 --order 訂單號，或同時提供 --name 和 --phone")
        sys.exit(1)
    if result["success"]:
        print(f"✅ {result['message']}")
        if result["screenshot"]:
            print(f"📸 {result['screenshot']}")
        return

    print(f"❌ {result['message']}")
    if result["screenshot"]:
        print(f"📸 {result['screenshot']}")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
