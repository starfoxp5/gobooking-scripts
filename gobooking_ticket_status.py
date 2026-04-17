#!/usr/bin/env python3
"""
gobooking_ticket_status.py
查詢套票使用狀態（訂單管理 → 套票分頁）

用法：
  python3 gobooking_ticket_status.py                        # 列所有套票訂單
  python3 gobooking_ticket_status.py --code TEY01972506011  # 用套票編號搜尋
"""
import asyncio
import argparse
from playwright.async_api import async_playwright

OWNER_URL = "https://gobooking.tw/owner/signinmyroom.html"
ACCOUNT   = "FionaAibot"


def _get_password() -> str:
    import subprocess
    for account in ["GOBOOKING_PASSWORD", "FionaAibot"]:
        try:
            return subprocess.check_output(
                ["security", "find-generic-password", "-a", account, "-s", "fiona-ai", "-w"],
                text=True,
            ).strip()
        except Exception:
            continue
    return "52640246"  # fallback


PASSWORD = _get_password()


async def check_ticket_status(code: str = ""):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        # 1. 登入
        await page.goto(OWNER_URL, timeout=30000)
        await page.wait_for_timeout(1500)
        pwd = _get_password()
        await page.fill("input[name='userACCT']", ACCOUNT)
        await page.fill("input[name='userPASS']", pwd)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(4000)

        # 2. 點「訂單管理」選單（保持 session，不直接 goto）
        await page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a,button,li,nav *'));
            const t = links.find(el => el.innerText && el.innerText.trim() === '訂單管理');
            if (t) t.click();
        }""")
        await page.wait_for_timeout(3000)

        # 3. 點「套票」分頁
        clicked = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const t = btns.find(b => b.innerText && b.innerText.trim() === '套票');
            if (t) { t.click(); return true; }
            return false;
        }""")
        if not clicked:
            print("❌ 找不到套票分頁 tab")
            await browser.close()
            return
        await page.wait_for_timeout(2000)

        # 4. 若有 --code 才展開搜尋篩選
        if code:
            await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(el => el.innerText && el.innerText.trim().includes('搜尋與篩選'));
                if (b) b.click();
            }""")
            await page.wait_for_timeout(1500)

            # 填套票編號（#order-id）
            await page.locator("#order-id").fill(code)
            await page.wait_for_timeout(300)

            # 點搜尋
            await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(b => b.innerText && b.innerText.trim() === '搜尋');
                if (b) b.click();
            }""")
            await page.wait_for_timeout(3000)

        # 5. 截圖
        await page.screenshot(path="/tmp/gobooking_ticket_status.png", full_page=True)

        # 6. 擷取結果
        content = await page.evaluate("document.body.innerText")
        lines = [l.strip() for l in content.split('\n') if l.strip()]

        print(f"\n🎫 套票訂單查詢" + (f"（{code}）" if code else "（全部）"))
        print("=" * 55)

        # 找訂單資料區段
        in_result = False
        result_lines = []
        for line in lines:
            if '第' in line and '筆' in line and '共' in line:
                in_result = True
            if in_result:
                result_lines.append(line)

        if result_lines:
            for line in result_lines:
                print(line)
        else:
            # fallback: 找包含 TEY 或次數資訊的行
            found = False
            for line in lines:
                if any(kw in line for kw in ['TEY', '球券', '剩', '次，共', '已啟用', '已失效', '有效至', '2026']):
                    print(line)
                    found = True
            if not found:
                print("查無資料")
                print("\n[debug] 頁面內容摘要:")
                for line in lines[:50]:
                    print(line)

        print("=" * 55)
        print("📸 截圖: /tmp/gobooking_ticket_status.png")

        await browser.close()


def main():
    parser = argparse.ArgumentParser(description="查詢 gobooking 套票使用狀態")
    parser.add_argument("--code", default="", help="套票編號（如 TEY01972506011），不填則列所有訂單")
    args = parser.parse_args()
    asyncio.run(check_ticket_status(args.code))


if __name__ == "__main__":
    main()
