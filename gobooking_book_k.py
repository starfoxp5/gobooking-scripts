#!/usr/bin/env python3
"""臨時腳本：幫王偉晉預約二館 K 場 2026/04/03 20:00-22:00"""
import sys
sys.path.insert(0, '/Users/openmini/.openclaw/workspace-fiona/scripts')

from gobooking_book import book_single, get_room_plans, get_owner_info, ROOMS

QRID = ROOMS["K"]
DATE = "2026/04/03"
START = "20:00"
END = "22:00"
NAME = "王偉晉(＾ω＾)"
PHONE = "0987581557"
EMAIL = "jimmy3000221@gmail.com"
COUPON = "energy0258"
REMARK = "Fiona"

# 取得方案列表
plans = get_room_plans(QRID)
print("K 場方案:")
for p in plans:
    print(f"  {p['app']} (appid={p['appid']}, cycle={p['cycle']})")

# 取得 owner code
owner = get_owner_info(QRID)
owner_code = owner.get('client_code', '')
print(f"Owner code: {owner_code}")

# 找單次方案
plan = next((p for p in plans if p['cycle'] == '0' or '單次' in p.get('app','')), plans[0] if plans else None)
if not plan:
    print("❌ 找不到單次方案")
    sys.exit(1)

plan_id = plan['appid']
print(f"使用方案: {plan['app']} (id={plan_id})")

# 計算價格（先查 $0 優惠碼）
import requests, json
from datetime import datetime, timedelta

BASE_URL = "https://gobooking.tw/energy"

# 用優惠碼 energy0258 折抵 $0
session = requests.Session()
session.headers.update({
    "Referer": f"https://gobooking.tw/energy/room.html?{QRID}",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://gobooking.tw",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
})

start_dt = datetime.strptime(f"{DATE} {START}", "%Y/%m/%d %H:%M")
end_dt = datetime.strptime(f"{DATE} {END}", "%Y/%m/%d %H:%M")

date_time_arr = []
cur = start_dt
while cur < end_dt:
    date_time_arr.append(cur.strftime("%Y-%m-%d %H:%M"))
    cur += timedelta(minutes=30)

booking_datetime_arr = [{"start_time": start_dt.strftime("%Y-%m-%d %H:%M"),
                          "end_time": end_dt.strftime("%Y-%m-%d %H:%M")}]

payload = {
    "content": json.dumps([{
        "room_id": QRID,
        "plan_id": plan_id,
        "start_time": START,
        "end_time": END,
        "date_time_arr": date_time_arr,
        "booking_datetime_arr": booking_datetime_arr,
        "exception_date_arr": [],
        "cross_day": "0",
        "price": 0,
        "equips": [],
        "fees": []
    }]),
    "payer_info": json.dumps({"name": NAME, "phone": PHONE, "email": EMAIL}),
    "contact_info": json.dumps({"name": NAME, "phone": PHONE, "email": EMAIL}),
    "notification": json.dumps([]),
    "coupon": json.dumps({"code": COUPON, "times": None}),
    "invoice": json.dumps({"type": "", "code": "", "title": "", "address": ""}),
    "prime": "",
    "remark": REMARK,
}

print(f"\n[送出] 二館 K 場 {DATE} {START}-{END} 王偉晉 (優惠碼: {COUPON})")
r = session.post(f"{BASE_URL}/bookingnow", data=payload, allow_redirects=False)
print(f"HTTP {r.status_code}")
print(f"Location: {r.headers.get('Location', '(none)')}")
print(f"Body: {r.text[:500]}")

if r.status_code in (200, 302, 303) or 'EY' in r.text or 'success' in r.text.lower():
    print("\n✅ 預約送出成功")
else:
    print("\n❌ 預約可能失敗，請確認")
