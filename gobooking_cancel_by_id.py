#!/usr/bin/env python3
"""
用訂單號直接取消
用法：python3 gobooking_cancel_by_id.py --order EY0502603197 --mode cancel
"""
import argparse
import asyncio
import subprocess
import sys

from playwright.async_api import async_playwright

OWNER_URL = "https://gobooking.tw/owner/signinmyroom.html"
ORDERS_URL = "https://gobooking.tw/owner/ordercontrol.html"
OWNER_ACCT = "FionaAibot"


def get_password() -> str:
    return subprocess.check_output(
        ["security", "find-generic-password", "-a", "GOBOOKING_PASSWORD", "-s", "fiona-ai", "-w"],
        text=True,
    ).strip()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--order", required=True, help="訂單號 EYxxxxxxxxxx")
    parser.add_argument("--mode", default="cancel", choices=["cancel", "no-refund", "refund"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    password = get_password()
    order_id = args.order

    if args.mode == "no-refund":
        keyword = "取消不退款"
    elif args.mode == "refund":
        keyword = "取消並退款"
    else:
        keyword = "取消訂單"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})

        # 登入
        print("[後台] 登入...")
        await page.goto(OWNER_URL, timeout=30000)
        await page.fill("input[name='userACCT']", OWNER_ACCT)
        await page.fill("input[name='userPASS']", password)
        await page.click("button[type='submit'], input[type='submit']")
        await page.wait_for_timeout(3000)

        # 前往訂單管理，搜尋訂單號
        await page.goto(ORDERS_URL, timeout=30000)
        await page.wait_for_timeout(2000)

        show_btn = page.locator(".show-search-btn")
        if await show_btn.count() > 0:
            await show_btn.click()
            await page.wait_for_timeout(500)

        # 搜尋訂單號
        order_input = page.locator("#order-id, input[placeholder*='訂單'], input[name*='order']").first
        if await order_input.count() > 0:
            await order_input.fill(order_id)
        else:
            # 嘗試直接填入第一個搜尋欄
            await page.fill("#con-name", order_id)

        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)

        # 確認找到目標訂單
        page_text = await page.evaluate("() => document.body.innerText")
        if order_id not in page_text:
            print(f"❌ 搜尋不到訂單 {order_id}")
            await page.screenshot(path=f"/tmp/cancel_{order_id}_notfound.png")
            await browser.close()
            sys.exit(1)

        print(f"✅ 找到訂單 {order_id}")

        if args.dry_run:
            print("[dry-run] 停止，不取消")
            await browser.close()
            return

        # 點取消按鈕
        cancel_btns = await page.locator(".cancel-btn").all()
        print(f"找到 {len(cancel_btns)} 個取消按鈕")

        dialog_messages = []
        async def handle_dialog(dialog):
            dialog_messages.append(dialog.message)
            await dialog.accept()
        page.on("dialog", handle_dialog)

        # 找包含目標訂單號的那一行的取消按鈕
        clicked = await page.evaluate(f"""
            ({{ orderId, keyword }}) => {{
                // 找包含訂單號的容器
                const allText = document.querySelectorAll('.order-item, tr, .card, li, div');
                for (const el of allText) {{
                    if ((el.textContent || '').includes(orderId)) {{
                        const btn = el.querySelector('.cancel-btn');
                        if (btn) {{ btn.click(); return 'clicked'; }}
                    }}
                }}
                // fallback: 點第一個
                const first = document.querySelector('.cancel-btn');
                if (first) {{ first.click(); return 'fallback'; }}
                return 'not_found';
            }}
        """, {"orderId": order_id, "keyword": keyword})
        print(f"取消按鈕結果: {clicked}")
        await page.wait_for_timeout(1000)

        # 點 inner cancel
        inner_clicked = await page.evaluate(f"""
            ({{ keyword }}) => {{
                const btns = Array.from(document.querySelectorAll('.inner-cancel-btn'))
                    .filter(b => (b.textContent || '').trim().includes(keyword));
                if (btns.length > 0) {{ btns[0].click(); return true; }}
                return false;
            }}
        """, {"keyword": keyword})
        print(f"inner-cancel ({keyword}): {inner_clicked}")
        await page.wait_for_timeout(2000)

        # 確認彈窗
        for sel in ["button:has-text('確認')", "button:has-text('確定')", ".confirm-btn"]:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click()
                await page.wait_for_timeout(1500)
                break

        if dialog_messages:
            print(f"dialog: {dialog_messages}")

        # 驗證
        await page.wait_for_timeout(1000)
        page_text2 = await page.evaluate("() => document.body.innerText")
        await page.screenshot(path=f"/tmp/cancel_{order_id}_result.png")

        if "取消成功" in ' '.join(dialog_messages) or order_id not in page_text2:
            print(f"✅ 訂單 {order_id} 取消成功")
        else:
            print(f"⚠️ 請確認截圖：/tmp/cancel_{order_id}_result.png")

        await browser.close()


asyncio.run(main())
