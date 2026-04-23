#!/usr/bin/env python3
"""
陳俊傑批次預約腳本
用法：python3 batch_chen_junjie.py [--dry-run]

任務：
  A場 9:30-10:00  × 12 日
  A場 12:00-13:30 × 2 日（5/17、8/2 補預約）

執行前自動：
  1. 開預約天數 → 110 天
  2. 最短時租 → 30 分鐘（for 9:30）
  3. 跑完 9:30 批次
  4. 還原最短時租 → 60 分鐘
  5. 跑 12:00 補預約
  6. 還原預約天數 → 30 天

鐵律（寫死，不得修改）：
  - 找不到指定開始時間 → 立刻中止，回報，絕不改時間
  - 找不到指定結束時間 → 立刻中止，回報，絕不改時間
  - 時間衝突（已被預約）→ 立刻中止，回報，絕不換時段或換場地
  - 日期 disabled → 立刻中止，回報，絕不跳到其他日期
"""

import asyncio
import argparse
import sys
from gobooking_playwright import book, set_booking_window, set_min_booking_minutes

NAME   = "陳俊傑"
PHONE  = "0922952350"
EMAIL  = "jchen857@yahoo.com.tw"
COUPON = "energy0258"
ROOM   = "A"

# 9:30-10:00 × 12 日
DATES_930 = [
    "2026/05/17", "2026/05/24", "2026/05/31",
    "2026/06/07", "2026/06/14", "2026/06/21", "2026/06/28",
    "2026/07/05", "2026/07/12", "2026/07/19", "2026/07/26",
    "2026/08/02",
]

# 12:00-13:30 補預約（5/17、8/2）
DATES_1200 = [
    "2026/05/17",
    "2026/08/02",
]


async def run_batch(dry_run: bool):
    results = []

    # ── Step 1: 開預約天數 ─────────────────────────────
    if not dry_run:
        print("\n[後台] 開放預約天數 → 110 天")
        await set_booking_window(110)

    # ── Step 2: 最短時租 → 30 分鐘 ──────────────────────
    if not dry_run:
        print("[後台] 最短時租 → 30 分鐘")
        await set_min_booking_minutes(30, rooms=[ROOM])

    # ── Step 3: 9:30-10:00 批次 ──────────────────────────
    print(f"\n{'[DRY-RUN] ' if dry_run else ''}=== A場 9:30-10:00 批次（共 {len(DATES_930)} 筆）===")
    for date in DATES_930:
        print(f"\n  ▶ {date} 9:30-10:00")
        try:
            result = await book(
                room=ROOM, date=date,
                start="9:30", end="10:00",
                coupon=COUPON,
                name=NAME, phone=PHONE, email=EMAIL,
                dry_run=dry_run,
            )
            status = "✅ DRY-RUN" if dry_run else ("✅ " + result.get("order_id","") if result.get("success") else "❌ " + result.get("error",""))
        except RuntimeError as e:
            status = f"❌ 中止：{e}"
        except Exception as e:
            status = f"❌ 例外：{e}"
        print(f"  {status}")
        results.append({"date": date, "time": "9:30-10:00", "status": status})

    # ── Step 4: 還原最短時租 → 60 分鐘 ──────────────────
    if not dry_run:
        print("\n[後台] 最短時租還原 → 60 分鐘")
        await set_min_booking_minutes(60, rooms=[ROOM])

    # ── Step 5: 12:00-13:30 補預約 ───────────────────────
    print(f"\n{'[DRY-RUN] ' if dry_run else ''}=== A場 12:00-13:30 補預約（共 {len(DATES_1200)} 筆）===")
    for date in DATES_1200:
        print(f"\n  ▶ {date} 12:00-13:30")
        try:
            result = await book(
                room=ROOM, date=date,
                start="12:00", end="13:30",
                coupon=COUPON,
                name=NAME, phone=PHONE, email=EMAIL,
                dry_run=dry_run,
            )
            status = "✅ DRY-RUN" if dry_run else ("✅ " + result.get("order_id","") if result.get("success") else "❌ " + result.get("error",""))
        except RuntimeError as e:
            status = f"❌ 中止：{e}"
        except Exception as e:
            status = f"❌ 例外：{e}"
        print(f"  {status}")
        results.append({"date": date, "time": "12:00-13:30", "status": status})

    # ── Step 6: 還原預約天數 → 30 天 ─────────────────────
    if not dry_run:
        print("\n[後台] 預約天數還原 → 30 天")
        await set_booking_window(30)

    # ── 報告 ──────────────────────────────────────────────
    print("\n" + "="*50)
    print("預約結果報告")
    print("="*50)
    ok  = [r for r in results if "✅" in r["status"]]
    err = [r for r in results if "❌" in r["status"]]
    for r in results:
        print(f"  {r['date']} {r['time']}  {r['status']}")
    print(f"\n成功：{len(ok)} 筆  失敗：{len(err)} 筆")

    if err:
        print("\n⚠️  失敗的日期需手動確認：")
        for r in err:
            print(f"  {r['date']} {r['time']}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="模擬，不實際送出")
    args = parser.parse_args()
    asyncio.run(run_batch(args.dry_run))


if __name__ == "__main__":
    main()
