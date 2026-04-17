import asyncio
from playwright.async_api import async_playwright
import sys

ROOMS = {
    "J場": {"url": "https://gobooking.tw/energy/room.html?170052020310077340", "plan": "17521"},
    "Q場": {"url": "https://gobooking.tw/energy/room.html?170187112712110103", "plan": "171871"}
}

DATES = [
    "2026/03/31", "2026/04/07", "2026/04/09", "2026/04/14", "2026/04/16",
    "2026/04/21", "2026/04/23", "2026/04/28", "2026/05/05", "2026/05/07",
    "2026/05/12", "2026/05/14", "2026/05/19", "2026/05/21", "2026/05/26",
    "2026/05/28", "2026/06/02", "2026/06/04", "2026/06/09", "2026/06/11",
    "2026/06/16", "2026/06/23", "2026/06/25", "2026/06/30", "2026/07/02",
    "2026/07/07", "2026/07/09", "2026/07/14", "2026/07/16", "2026/07/21",
    "2026/07/23", "2026/07/28", "2026/07/30", "2026/08/04", "2026/08/06",
    "2026/08/11", "2026/08/13", "2026/08/18", "2026/08/20", "2026/08/25",
    "2026/08/27", "2026/09/01", "2026/09/03", "2026/09/08", "2026/09/10",
    "2026/09/15", "2026/09/17", "2026/09/22", "2026/09/29"
]

def has_conflict(booked, start, end):
    for item in booked:
        if not (item["end"] <= start or item["start"] >= end):
            return True
    return False

MONTH_NAMES = {"03": "March", "04": "April", "05": "May", "06": "June", "07": "July", "08": "August", "09": "September"}
MONTH_ORDER = {n: int(num) for num, n in MONTH_NAMES.items()}

async def goto_month(page, year, month, loaded):
    key = f"{year}/{month}"
    if key in loaded: return
    
    cv = await page.evaluate("() => document.querySelector('.air-datepicker.-active-') !== null")
    if not cv:
        try: await page.locator("#date-picker").click(timeout=5000)
        except: await page.locator("input[name='startdate']").first.click(timeout=5000)
        await page.wait_for_timeout(1000)
    
    target_name = MONTH_NAMES[month]
    target_ym = int(year) * 12 + int(month)
    
    for _ in range(15):
        nav = await page.evaluate("() => document.querySelector('.air-datepicker-nav--title')?.innerText || ''")
        if target_name in nav and year in nav: break
        
        cym = 0
        for mn, no in MONTH_ORDER.items():
            if mn in nav:
                yt = [t for t in nav.split() if t.isdigit() and len(t) == 4]
                if yt: cym = int(yt[0])*12 + no
                break
        
        dir_ = "prev" if cym and cym > target_ym else "next"
        clicked = await page.evaluate(f"() => {{ let b=document.querySelector('[data-action=\"{dir_}\"]'); if(b){{b.click();return true;}} return false; }}")
        if not clicked: break
        await page.wait_for_timeout(600)
    loaded.add(key)

async def check():
    results = {"J場": [], "Q場": []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        
        for name, info in ROOMS.items():
            print(f"Checking {name}...")
            await page.goto(info["url"], wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            await page.select_option("select[name='booking-plan']", value=info["plan"])
            await page.wait_for_timeout(500)
            
            loaded = set()
            for date_str in DATES:
                y, m, d = date_str.split('/')
                await goto_month(page, y, m, loaded)
                
                booked = await page.evaluate("""({d}) => {
                    const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
                    if (!pinia) return [];
                    const store = pinia.state.value.calendar;
                    if (!store || !store.bookedList) return [];
                    return JSON.parse(JSON.stringify(store.bookedList)).filter(i => i.date === d);
                }""", {"d": date_str})
                
                conflict = has_conflict(booked, "20:00", "23:00")
                results[name].append({"date": date_str, "available": not conflict})
                print(f"  {date_str}: {'✅ 可預約' if not conflict else '❌ 已滿'}")
                
        await browser.close()
    return results

if __name__ == "__main__":
    asyncio.run(check())
