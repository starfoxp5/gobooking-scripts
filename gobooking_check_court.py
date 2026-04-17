#!/usr/bin/env python3
"""
查詢特定場地特定日期時段的訂單
用法：python3 gobooking_check_court.py --court B --date "2026/04/03" --start 2000 --end 2200
"""
import argparse
import asyncio
import subprocess
import sys

from playwright.async_api import async_playwright

ORDERS_URL = "https://gobooking.tw/owner/ordercontrol.html"
OWNER_URL = "https://gobooking.tw/owner/signinmyroom.html"
OWNER_ACCT = "FionaAibot"


def get_password() -> str:
    return subprocess.check_output(
        ["security", "find-generic-password", "-a", "GOBOOKING_PASSWORD", "-s", "fiona-ai", "-w"],
        text=True,
    ).strip()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--court", default="B", help="場地代碼（A/B/C/J/K/Q）")
    parser.add_argument("--date", default="2026/04/03", help="日期 YYYY/MM/DD")
    parser.add_argument("--start", default="2000", help="開始時間 HHMM")
    parser.add_argument("--end", default="2200", help="結束時間 HHMM")
    args = parser.parse_args()

    password = get_password()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})

        # 登入
        await page.goto(OWNER_URL, timeout=30000)
        await page.fill("input[name='userACCT']", OWNER_ACCT)
        await page.fill("input[name='userPASS']", password)
        await page.click("button[type='submit'], input[type='submit']")
        await page.wait_for_timeout(3000)

        # 前往訂單管理，搜尋日期
        await page.goto(ORDERS_URL, timeout=30000)
        await page.wait_for_timeout(2000)

        show_btn = page.locator(".show-search-btn")
        if await show_btn.count() > 0:
            await show_btn.click()
            await page.wait_for_timeout(500)

        # 設定日期範圍
        await page.evaluate(
            """({ dateStr }) => {
                const setValue = (el, value) => {
                    if (!el) return;
                    el.removeAttribute('readonly');
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                };
                setValue(document.querySelector('#order-start'), dateStr);
                setValue(document.querySelector('#order-end'), dateStr);
            }""",
            {"dateStr": args.date},
        )
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)

        # 抓所有訂單文字
        rows = await page.evaluate("""() => {
            const items = document.querySelectorAll('.order-item, [class*="order-row"], .order-card, tr');
            return Array.from(items).map(el => ({
                text: (el.textContent || '').replace(/\\s+/g, ' ').trim().substring(0, 500),
                dataset: Object.assign({}, el.dataset || {})
            })).filter(r => r.text.length > 10);
        }""")

        court_char = args.court.upper()
        start_fmt = args.start[:2] + ":" + args.start[2:]  # 2000 -> 20:00 or just check raw
        
        print(f"\n=== 一館 {court_char} 場 {args.date} {args.start}-{args.end} 訂單查詢 ===\n")
        
        matches = []
        for row in rows:
            t = row["text"]
            # 檢查場地和時間
            court_match = (f"『 Ａ 』" if court_char == "A" else
                          f"『 Ｂ 』" if court_char == "B" else
                          f"『 Ｃ 』" if court_char == "C" else
                          f"『 Ｊ 』" if court_char == "J" else
                          f"『 Ｋ 』" if court_char == "K" else
                          f"『 Ｑ 』")
            
            start_h = args.start[:2]
            start_m = args.start[2:]
            time_str = f"{start_h}{start_m}"
            
            if court_match in t and (args.start in t or time_str in t):
                matches.append(t)
                print(f"✅ {t[:300]}\n")

        if not matches:
            print(f"查無一館 {court_char} 場 {args.start}-{args.end} 的訂單")
            # 印出所有包含該場地的訂單
            print(f"\n--- {court_char} 場今日所有訂單 ---")
            court_match = (f"『 Ａ 』" if court_char == "A" else
                          f"『 Ｂ 』" if court_char == "B" else
                          f"『 Ｃ 』" if court_char == "C" else
                          f"『 Ｊ 』" if court_char == "J" else
                          f"『 Ｋ 』" if court_char == "K" else
                          f"『 Ｑ 』")
            found_any = False
            for row in rows:
                if court_match in row["text"]:
                    print(row["text"][:300])
                    found_any = True
            if not found_any:
                print(f"  今日無任何 {court_char} 場訂單")

        await browser.close()

asyncio.run(main())
