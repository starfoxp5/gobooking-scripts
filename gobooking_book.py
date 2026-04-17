#!/usr/bin/env python3
"""
gobooking 預約腳本
用途：幫教練預約/批次預約場地
"""

import requests
import json
import sys
from datetime import datetime, timedelta

# === 設定 ===
BASE_URL = "https://gobooking.tw/energy"

# 場地 QRID 對照
ROOMS = {
    "A": "170049020310051559",
    "B": "170050020310063902",
    "C": "170051020310068898",
    "J": "170052020310077340",
    "Q": "170187112712110103",
    "K": "170188112712121020",
}

def get_session(room_qrid: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Referer": f"https://gobooking.tw/energy/room.html?{room_qrid}",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://gobooking.tw",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })
    return session

def get_room_plans(room_qrid: str) -> list:
    """取得場地的預約方案列表"""
    session = get_session(room_qrid)
    r = session.get(f"{BASE_URL}/get_room_opening", params={"QRID": room_qrid})
    data = r.json()
    return data.get("openinginfo", [])

def get_owner_info(room_qrid: str) -> dict:
    session = get_session(room_qrid)
    r = session.get(f"{BASE_URL}/ownerinfo", params={"QRID": room_qrid})
    return r.json()

def calculate_price(plan: dict, start_time: str, end_time: str, date: str) -> int:
    """計算預約費用"""
    dt = datetime.strptime(date, "%Y/%m/%d")
    is_weekend = dt.weekday() >= 5  # 5=Sat, 6=Sun
    
    pricing = plan["weekend"] if is_weekend else plan["weekday"]
    
    # 計算時段
    start_h = int(start_time.split(":")[0])
    end_h = int(end_time.split(":")[0])
    duration_hours = end_h - start_h
    
    total = 0
    for hour in range(start_h, end_h):
        for tier in pricing:
            tier_start = int(tier["start"].split(":")[0])
            tier_end = int(tier["end"].split(":")[0]) if tier["end"] != "24:00" else 24
            if tier_start <= hour < tier_end:
                total += int(tier["price"])
                break
    
    return total

def book_single(
    room_qrid: str,
    plan_id: str,
    owner_code: str,
    date: str,           # "2026/04/02"
    start_time: str,     # "19:00"
    end_time: str,       # "22:00"
    name: str,
    phone: str,
    email: str,
    price: int,
    remark: str = "",
    dry_run: bool = False
) -> dict:
    """單次預約"""
    
    # 組 datetime_arr（每30分鐘一格）
    start_dt = datetime.strptime(f"{date} {start_time}", "%Y/%m/%d %H:%M")
    end_dt = datetime.strptime(f"{date} {end_time}", "%Y/%m/%d %H:%M")
    
    date_time_arr = []
    cur = start_dt
    while cur < end_dt:
        date_time_arr.append(cur.strftime("%Y-%m-%d %H:%M"))
        cur += timedelta(minutes=30)
    
    booking_datetime_arr = [{"start_time": start_dt.strftime("%Y-%m-%d %H:%M"), 
                              "end_time": end_dt.strftime("%Y-%m-%d %H:%M")}]
    
    payload = {
        "content": json.dumps([{
            "room_id": room_qrid,
            "plan_id": plan_id,
            "start_time": start_time,
            "end_time": end_time,
            "date_time_arr": date_time_arr,
            "booking_datetime_arr": booking_datetime_arr,
            "exception_date_arr": [],
            "cross_day": "0",
            "price": price,
            "equips": [],
            "fees": []
        }]),
        "payer_info": json.dumps({"name": name, "phone": phone, "email": email}),
        "contact_info": json.dumps({"name": name, "phone": phone, "email": email}),
        "notification": json.dumps([]),
        "coupon": json.dumps({"code": "", "times": None}),
        "invoice": json.dumps({"type": "", "code": "", "title": "", "address": ""}),
        "prime": "",
        "remark": remark,
    }
    
    if dry_run:
        print(f"  [DRY RUN] 會送出預約: {date} {start_time}-{end_time}, 金額={price}")
        return {"status": "dry_run"}
    
    session = get_session(room_qrid)
    r = session.post(f"{BASE_URL}/bookingnow", data=payload, allow_redirects=False)
    
    return {
        "status_code": r.status_code,
        "location": r.headers.get("Location", ""),
        "body": r.text[:300]
    }

def book_cycle(
    room_qrid: str,
    plan_id: str,
    owner_code: str,
    start_date: str,     # 第一週 "2026/04/02"
    dates: list,         # 所有預約日期 ["2026/04/02", ...]
    start_time: str,
    end_time: str,
    name: str,
    phone: str,
    email: str,
    price_per: int,
    exception_dates: list = [],
    dry_run: bool = False
) -> dict:
    """週期預約（季租）"""
    
    all_booking_arr = []
    for d in dates:
        start_dt = datetime.strptime(f"{d} {start_time}", "%Y/%m/%d %H:%M")
        end_dt = datetime.strptime(f"{d} {end_time}", "%Y/%m/%d %H:%M")
        all_booking_arr.append({
            "start_time": start_dt.strftime("%Y-%m-%d %H:%M"),
            "end_time": end_dt.strftime("%Y-%m-%d %H:%M")
        })
    
    # date_time_arr for first date
    first_dt = datetime.strptime(f"{start_date} {start_time}", "%Y/%m/%d %H:%M")
    first_end = datetime.strptime(f"{start_date} {end_time}", "%Y/%m/%d %H:%M")
    date_time_arr = []
    cur = first_dt
    while cur < first_end:
        date_time_arr.append(cur.strftime("%Y-%m-%d %H:%M"))
        cur += timedelta(minutes=30)
    
    total_price = price_per * len(dates)
    
    payload = {
        "content": json.dumps([{
            "room_id": room_qrid,
            "plan_id": plan_id,
            "start_time": start_time,
            "end_time": end_time,
            "date_time_arr": date_time_arr,
            "booking_datetime_arr": all_booking_arr,
            "exception_date_arr": exception_dates,
            "cross_day": "0",
            "price": total_price,
            "equips": [],
            "fees": []
        }]),
        "payer_info": json.dumps({"name": name, "phone": phone, "email": email}),
        "contact_info": json.dumps({"name": name, "phone": phone, "email": email}),
        "notification": json.dumps([]),
        "coupon": json.dumps({"code": "", "times": None}),
        "invoice": json.dumps({"type": "", "code": "", "title": "", "address": ""}),
        "prime": "",
        "remark": "",
    }
    
    if dry_run:
        print(f"  [DRY RUN] 週期預約: {len(dates)} 次, 每次={price_per}, 總計={total_price}")
        print(f"  日期: {dates}")
        return {"status": "dry_run"}
    
    session = get_session(room_qrid)
    r = session.post(f"{BASE_URL}/bookingnow", data=payload, allow_redirects=False)
    
    return {
        "status_code": r.status_code,
        "location": r.headers.get("Location", ""),
        "body": r.text[:300]
    }


if __name__ == "__main__":
    # 測試取得方案
    QRID = ROOMS["A"]
    plans = get_room_plans(QRID)
    print("A 場方案:")
    for p in plans:
        print(f"  {p['app']} (appid={p['appid']}, cycle={p['cycle']})")
    
    owner = get_owner_info(QRID)
    print(f"\nOwner code: {owner.get('client_code','')}")
