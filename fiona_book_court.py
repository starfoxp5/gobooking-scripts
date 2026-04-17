#!/usr/bin/env python3
"""
fiona_book_court.py — gobooking 預約 CLI Wrapper

設計目的：
- 讓 30B-A3B / cowork agent / grounded_executor 可以透過 shell_exec 呼叫
- 統一介面，所有預約走這一個入口
- 預設 dry-run，必須加 --confirm 才會真送

用法：
  # 1. dry-run（預設）— 列出會送的請求
  python3 fiona_book_court.py \
    --room A --date 2026/04/08 \
    --start 12:00 --end 13:00 \
    --name "鳳老闆" --phone 0932008667 --email fiona.aibot@gmail.com

  # 2. 確認後真送
  python3 fiona_book_court.py \
    --room A --date 2026/04/08 \
    --start 12:00 --end 13:00 \
    --name "鳳老闆" --phone 0932008667 --email fiona.aibot@gmail.com \
    --confirm

  # 3. 列場地方案（不預約，給 agent 探索用）
  python3 fiona_book_court.py --list-plans --room A

回傳格式（grounded_executor 友善的 JSON）：
  {
    "ok": true|false,
    "phase": "dry_run" | "submitted",
    "room": "A",
    "datetime": "2026/04/08 12:00-13:00",
    "price": 800,
    "plan_id": "17491",
    "owner_code": "...",
    "result": {...}  # 真送時才有
  }
"""
import argparse
import json
import sys
from pathlib import Path

# 從同目錄 import gobooking_book
sys.path.insert(0, str(Path(__file__).parent))

try:
    from gobooking_book import (
        ROOMS,
        get_room_plans,
        get_owner_info,
        calculate_price,
        book_single,
    )
except ImportError as e:
    print(json.dumps({
        "ok": False,
        "error": f"無法 import gobooking_book: {e}",
        "fix": "確認 fiona_book_court.py 與 gobooking_book.py 在同一目錄",
    }, ensure_ascii=False))
    sys.exit(1)


def find_single_plan(plans: list) -> dict:
    """從方案列表找出單次預約方案"""
    for p in plans:
        if p.get('cycle') == '0':
            return p
        if '單次' in p.get('app', ''):
            return p
    # fallback: 第一個
    return plans[0] if plans else None


def main():
    ap = argparse.ArgumentParser(
        description='gobooking 預約 wrapper - 給 cowork/30B-A3B 用'
    )
    ap.add_argument('--room', required=False, choices=list(ROOMS.keys()),
                    help='場地代號 A/B/C/J/K/Q')
    ap.add_argument('--date', help='日期 YYYY/MM/DD')
    ap.add_argument('--start', help='開始時間 HH:MM')
    ap.add_argument('--end', help='結束時間 HH:MM')
    ap.add_argument('--name', help='聯絡人姓名')
    ap.add_argument('--phone', help='聯絡電話')
    ap.add_argument('--email', help='Email')
    ap.add_argument('--remark', default='Fiona', help='備註（預設 Fiona）')
    ap.add_argument('--coupon', default='energy0258',
                    help='優惠碼（預設 energy0258 — 鳳老闆專用 100%% 折抵碼，'
                         '見 kb/gobooking.md 規則 6。傳空字串 --coupon "" 取消）')
    ap.add_argument('--ticket', default='', help='套票代碼（TEY...）')
    ap.add_argument('--confirm', action='store_true',
                    help='⚠️ 真送預約。不加 --confirm 預設只 dry-run')
    ap.add_argument('--list-plans', action='store_true',
                    help='只列出場地方案，不預約（給 agent 探索用）')
    args = ap.parse_args()

    # === Mode 1: 列方案 ===
    if args.list_plans:
        if not args.room:
            print(json.dumps({"ok": False, "error": "--list-plans 需要 --room"}))
            sys.exit(1)
        try:
            qrid = ROOMS[args.room]
            plans = get_room_plans(qrid)
            owner = get_owner_info(qrid)
            print(json.dumps({
                "ok": True,
                "room": args.room,
                "qrid": qrid,
                "owner_code": owner.get('client_code', ''),
                "plans": [
                    {
                        "name": p.get('app'),
                        "appid": p.get('appid'),
                        "cycle": p.get('cycle'),
                    }
                    for p in plans
                ],
            }, ensure_ascii=False, indent=2))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            sys.exit(1)
        return

    # === Mode 2: 預約 ===
    required = ['room', 'date', 'start', 'end', 'name', 'phone', 'email']
    missing = [r for r in required if not getattr(args, r)]
    if missing:
        print(json.dumps({
            "ok": False,
            "error": f"缺少必填參數: {missing}",
            "usage": "python3 fiona_book_court.py --room A --date 2026/04/08 --start 12:00 --end 13:00 --name '鳳老闆' --phone 0932008667 --email fiona.aibot@gmail.com [--confirm]",
        }, ensure_ascii=False))
        sys.exit(1)

    try:
        qrid = ROOMS[args.room]

        # 取得方案
        plans = get_room_plans(qrid)
        plan = find_single_plan(plans)
        if not plan:
            print(json.dumps({"ok": False, "error": "找不到單次方案"}, ensure_ascii=False))
            sys.exit(1)

        # 取得 owner code
        owner = get_owner_info(qrid)
        owner_code = owner.get('client_code', '')

        # 計算價格
        try:
            price = calculate_price(plan, args.start, args.end, args.date)
        except Exception:
            price = 0  # fallback

        # 預約資訊摘要
        summary = {
            "room": args.room,
            "qrid": qrid,
            "date": args.date,
            "time": f"{args.start}-{args.end}",
            "plan_id": plan.get('appid'),
            "plan_name": plan.get('app'),
            "owner_code": owner_code,
            "price": price,
            "coupon": args.coupon or None,
            "ticket": args.ticket or None,
            "expected_total": 0 if args.coupon == 'energy0258' else price,
            "name": args.name,
            "phone": args.phone,
            "email": args.email,
            "remark": args.remark,
        }

        # === Dry-run 預設 ===
        if not args.confirm:
            print(json.dumps({
                "ok": True,
                "phase": "dry_run",
                "warning": "這是 dry-run，沒有真送。確認無誤後加 --confirm 真送",
                "would_book": summary,
            }, ensure_ascii=False, indent=2))
            return

        # === 真送 ===
        # 注意：gobooking_book.book_single() 的 API endpoint /energy/bookingnow
        # 已經 404，所以改走 gobooking_playwright.py（Playwright 模擬瀏覽器）
        import subprocess
        playwright_script = Path(__file__).parent / "gobooking_playwright.py"
        if not playwright_script.exists():
            print(json.dumps({
                "ok": False,
                "phase": "error",
                "error": f"找不到 gobooking_playwright.py 在 {playwright_script}",
            }, ensure_ascii=False))
            sys.exit(1)

        cmd = [
            "python3", str(playwright_script),
            "--room", args.room,
            "--date", args.date,
            "--start", args.start,
            "--end", args.end,
            "--name", args.name,
            "--phone", args.phone,
            "--email", args.email,
        ]
        if args.coupon:
            cmd += ["--coupon", args.coupon]
        if args.ticket:
            cmd += ["--ticket", args.ticket]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            stdout = r.stdout
            stderr = r.stderr
            combined = (stdout + stderr).lower()

            # 訂單編號 regex：EY 開頭 10-12 位數字
            import re
            order_match = re.search(r'EY\d{10,12}', stdout + stderr)
            order_code = order_match.group(0) if order_match else None

            # ok 判定：return code 0 + 有訂單號或成功標記
            ok = r.returncode == 0 and (
                order_code is not None
                or 'success' in combined
                or '預約成功' in stdout
                or 'order_code' in combined
            )

            print(json.dumps({
                "ok": ok,
                "phase": "submitted",
                "order_code": order_code,
                "booking": summary,
                "stdout_tail": stdout[-1500:],
                "stderr_tail": stderr[-500:] if stderr else "",
                "returncode": r.returncode,
            }, ensure_ascii=False, indent=2))
        except subprocess.TimeoutExpired:
            print(json.dumps({
                "ok": False,
                "phase": "timeout",
                "error": "Playwright 預約超時 180s",
                "booking": summary,
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

    except Exception as e:
        import traceback
        print(json.dumps({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()[:500],
        }, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
