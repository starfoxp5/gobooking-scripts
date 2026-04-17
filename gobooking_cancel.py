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
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OWNER_URL = "https://gobooking.tw/owner/signinmyroom.html"
ORDERS_URL = "https://gobooking.tw/owner/ordercontrol.html"
OWNER_ACCT = "FionaAibot"
ORDER_ID_RE = re.compile(r"\b(EY\d{10})\b")
# === 硬鎖：訂單號嚴格格式 EY + 10 位數字 ===
ORDER_ID_STRICT_RE = re.compile(r"^EY\d{10}$")
# === Audit log：每筆 cancel 操作都留證據（事故 2026/04/07 後加固）===
AUDIT_LOG = Path("/tmp/gobooking_cancel_audit.jsonl")


def audit(event: str, **fields) -> None:
    """寫 audit log（jsonl 格式），永不阻塞主流程

    每個 event 都記錄：時間、事件名、所有相關欄位
    用途：事故調查、operation review、debug
    """
    record = {
        "ts": datetime.datetime.now().isoformat(),
        "event": event,
        **fields,
    }
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 寫 audit 失敗絕不能阻擋取消流程


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
    """用訂單號直接搜尋並取消（四道硬鎖版本）

    硬規則（事故 2026/04/07 唐于婷 EY0522604256 誤取消後加固）：
    ────────────────────────────────────────────
    Lock 1 [格式]   order_id 必須符合 ^EY\\d{10}$，否則拒絕
    Lock 2 [唯一]   後台最近列表中 order_id 必須唯一匹配（0 或 >1 都 ABORT）
    Lock 3 [點擊前] 取消前 re-read 該 row 的 dataset，order_id 必須再次相符
    Lock 4 [diff]   取消前後 snapshot 全部訂單號，diff 必須 = {target}（不多不少）
    ────────────────────────────────────────────
    每個事件都寫 /tmp/gobooking_cancel_audit.jsonl，留 audit trail。

    任一鎖失敗 → 立刻 ABORT，絕不取消任何訂單。
    """
    password = get_password()
    result: dict = {
        "success": False,
        "order_id": order_id,
        "mode": mode,
        "message": "",
        "screenshot": None,
        "verified": False,
        "locks_passed": [],
    }

    # === Lock 1: 格式嚴格 regex 驗證（不需開瀏覽器即可拒絕）===
    if not ORDER_ID_STRICT_RE.match(order_id):
        audit(
            "lock1_format_fail",
            order_id=order_id,
            reason="not match ^EY\\d{10}$",
        )
        result["message"] = (
            f"❌ ABORT (Lock1 格式): order_id='{order_id}' 不符 ^EY\\d{{10}}$，拒絕操作"
        )
        result["abort_lock"] = "lock1_format"
        return result
    result["locks_passed"].append("lock1_format")

    audit(
        "cancel_start",
        order_id=order_id,
        mode=mode,
        dry_run=dry_run,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})

        try:
            print("[後台] 登入...")
            await login(page, password)

            print(f"[搜尋] 訂單號={order_id}（四道硬鎖精準匹配）")
            await page.goto(ORDERS_URL, timeout=30000)
            await page.wait_for_timeout(2000)

            # 不送任何搜尋條件，直接列最近訂單再用 order_id 比對
            rows = await get_order_rows(page)
            cancel_count = await page.locator(".cancel-btn").count()

            if cancel_count == 0:
                audit("no_cancellable_orders", order_id=order_id)
                result["message"] = f"後台沒有任何可取消訂單"
                result["screenshot"] = f"/tmp/gobooking_cancel_{order_id}.png"
                await page.screenshot(path=result["screenshot"])
                return result

            # === 取消前 snapshot：全部可取消訂單號 ===
            pre_cancel_ids = [
                row.get("order_id")
                for row in rows[:cancel_count]
                if row.get("order_id")
            ]
            audit(
                "pre_cancel_snapshot",
                order_id=order_id,
                cancel_count=cancel_count,
                visible_orders=pre_cancel_ids,
            )

            # === Lock 2: 唯一匹配（0 個或 >1 個都 ABORT）===
            matching = [
                (i, row)
                for i, row in enumerate(rows[:cancel_count])
                if row.get("order_id") == order_id
            ]

            if len(matching) == 0:
                audit(
                    "lock2_not_found",
                    order_id=order_id,
                    visible_orders=pre_cancel_ids,
                )
                result["message"] = (
                    f"❌ ABORT (Lock2 唯一匹配): 後台最近 {cancel_count} 筆可取消訂單中"
                    f"找不到 {order_id}。可能該訂單已不在最近列表（需翻頁）、訂單號錯誤、"
                    f"或該訂單早已取消。絕不取消其他訂單。"
                )
                result["abort_lock"] = "lock2_not_found"
                result["screenshot"] = f"/tmp/gobooking_cancel_{order_id}_notfound.png"
                await page.screenshot(path=result["screenshot"])
                print(f"\n[debug] 後台最近 {cancel_count} 筆可取消訂單:")
                for i, row in enumerate(rows[:cancel_count]):
                    label = row.get("order_id") or "UNKNOWN"
                    print(f"  [{i}] {label} {row['text'][:120]}")
                return result

            if len(matching) > 1:
                # paranoia：理論上不可能（一個 order_id 只能有一筆）
                audit(
                    "lock2_duplicate",
                    order_id=order_id,
                    duplicate_count=len(matching),
                    duplicate_indices=[i for i, _ in matching],
                )
                result["message"] = (
                    f"❌ ABORT (Lock2 重複): 後台同時出現 {len(matching)} 筆 {order_id}（異常）"
                )
                result["abort_lock"] = "lock2_duplicate"
                result["screenshot"] = f"/tmp/gobooking_cancel_{order_id}_dup.png"
                await page.screenshot(path=result["screenshot"])
                return result

            target_index, target_row = matching[0]
            result["locks_passed"].append("lock2_unique")
            audit(
                "lock2_pass",
                order_id=order_id,
                target_index=target_index,
            )

            print(f"\n[Lock2 ✅] 唯一匹配 {order_id} → row[{target_index}]")
            print(f"  {target_row['text'][:150]}")

            if dry_run:
                audit("dry_run_complete", order_id=order_id, target_index=target_index)
                result["success"] = True
                result["message"] = f"DRY_RUN: 找到 {order_id}"
                return result

            # === Lock 3: 點擊取消按鈕前最後一次 re-read，assert 一致 ===
            fresh_rows = await get_order_rows(page)
            fresh_count = await page.locator(".cancel-btn").count()

            if target_index >= fresh_count:
                audit(
                    "lock3_index_oob",
                    order_id=order_id,
                    target_index=target_index,
                    fresh_count=fresh_count,
                )
                result["message"] = (
                    f"❌ ABORT (Lock3 越界): re-read 後可取消數量變了"
                    f"（{cancel_count}→{fresh_count}），target_index={target_index} 越界"
                )
                result["abort_lock"] = "lock3_index_oob"
                return result

            fresh_target = fresh_rows[target_index]
            fresh_order_id = fresh_target.get("order_id")
            if fresh_order_id != order_id:
                audit(
                    "lock3_mismatch",
                    order_id=order_id,
                    target_index=target_index,
                    fresh_order_id=fresh_order_id,
                )
                result["message"] = (
                    f"❌ ABORT (Lock3 不一致): row[{target_index}] 在 re-read 後變成"
                    f"'{fresh_order_id}'，預期 '{order_id}'。可能列表 reorder，拒絕點擊。"
                )
                result["abort_lock"] = "lock3_mismatch"
                result["screenshot"] = f"/tmp/gobooking_cancel_{order_id}_lock3.png"
                await page.screenshot(path=result["screenshot"])
                return result

            result["locks_passed"].append("lock3_recheck")
            audit("lock3_pass", order_id=order_id, target_index=target_index)
            print(f"[Lock3 ✅] re-read 確認 row[{target_index}] 仍是 {order_id}")

            # === 點擊取消按鈕 ===
            result["screenshot"] = f"/tmp/gobooking_cancel_{order_id}.png"
            print(f"\n[執行] 模式={mode}，取消訂單 {order_id}（row[{target_index}]）...")
            audit(
                "click_cancel",
                order_id=order_id,
                target_index=target_index,
                mode=mode,
            )

            ok, message, dialog_messages = await do_cancel(page, target_index, mode)
            if not ok:
                audit(
                    "do_cancel_failed",
                    order_id=order_id,
                    message=message,
                )
                result["message"] = f"取消點擊失敗: {message}"
                await page.screenshot(path=result["screenshot"])
                return result

            if dialog_messages:
                print(f"  dialog 已處理: {dialog_messages}")
                audit(
                    "dialog_handled",
                    order_id=order_id,
                    dialogs=dialog_messages,
                )

            # === Lock 4: 取消後 snapshot diff（最強硬鎖）===
            # 重新撈列表，跟 pre_cancel_ids 比 diff
            # 規則：必須只有 target 從可取消列表消失，多消失或消失的不是 target = critical alert
            post_cancel_ids = []
            diff_removed: set = set()
            diff_added: set = set()

            for attempt in range(3):
                await page.goto(ORDERS_URL, timeout=30000)
                await page.wait_for_timeout(3000 if attempt > 0 else 2500)
                verify_rows = await get_order_rows(page)
                verify_cancel_count = await page.locator(".cancel-btn").count()
                post_cancel_ids = [
                    row.get("order_id")
                    for row in verify_rows[:verify_cancel_count]
                    if row.get("order_id")
                ]

                diff_removed = set(pre_cancel_ids) - set(post_cancel_ids)
                diff_added = set(post_cancel_ids) - set(pre_cancel_ids)

                if order_id in diff_removed:
                    break  # target 消失了，可以驗證 diff
                print(f"  [Lock4 retry {attempt+1}/3] target 還在可取消列表，等 3 秒...")

            await page.screenshot(path=result["screenshot"])

            audit(
                "post_cancel_snapshot",
                order_id=order_id,
                pre_count=len(pre_cancel_ids),
                post_count=len(post_cancel_ids),
                diff_removed=sorted(diff_removed),
                diff_added=sorted(diff_added),
            )

            # === Lock 4 critical 判斷 ===
            if order_id not in diff_removed:
                # ⚠️ dialog 確認了，但列表 race condition 沒消失（疑似成功但未驗證）
                # 但仍要檢查有沒有別的訂單意外消失
                if len(diff_removed) > 0:
                    audit(
                        "lock4_critical_target_not_removed_others_removed",
                        order_id=order_id,
                        unexpected_removed=sorted(diff_removed),
                    )
                    result["message"] = (
                        f"❌❌❌ CRITICAL (Lock4): target {order_id} 未消失，"
                        f"但其他訂單意外消失了 {sorted(diff_removed)}！立刻人工檢查！"
                    )
                    result["abort_lock"] = "lock4_critical_collateral"
                    return result
                # diff_removed 為空 → race condition，dialog 說成功但列表還沒 refresh
                audit(
                    "lock4_race_warning",
                    order_id=order_id,
                    note="dialog said success but list shows target still present after 3 retries",
                )
                result["message"] = (
                    f"⚠️ 取消 dialog 已確認（取消成功），但 3 次重試後 {order_id} "
                    f"仍在可取消列表，且沒有其他訂單意外消失。"
                    f"很可能是後台 refresh race，但請用 check_court 確認。"
                )
                result["success"] = True
                result["verified"] = False
                return result

            # target 確實消失了
            if len(diff_removed) > 1:
                # ❗ critical：消失了不只 target
                others = sorted(diff_removed - {order_id})
                audit(
                    "lock4_critical_collateral_removed",
                    order_id=order_id,
                    target_removed=True,
                    other_removed=others,
                )
                result["message"] = (
                    f"❌❌❌ CRITICAL (Lock4): target {order_id} 消失了，"
                    f"但同時還有其他訂單也消失了 {others}！立刻人工檢查！"
                    f"可能是後台 race，也可能是嚴重 bug。"
                )
                result["abort_lock"] = "lock4_critical_collateral"
                result["pre_snapshot"] = pre_cancel_ids
                result["post_snapshot"] = post_cancel_ids
                return result

            if len(diff_added) > 0:
                # 通常是新訂單在 cancel 期間建立的，audit 但不 abort
                audit(
                    "lock4_new_orders_appeared",
                    order_id=order_id,
                    new_orders=sorted(diff_added),
                    note="這通常是新預約進來，不影響取消結果",
                )

            # ✅ 完美路徑：diff_removed == {target}，diff_added 不影響
            result["locks_passed"].append("lock4_diff")
            audit(
                "cancel_verified",
                order_id=order_id,
                diff_removed=[order_id],
                diff_added=sorted(diff_added),
            )
            result["success"] = True
            result["verified"] = True
            result["message"] = (
                f"訂單 {order_id} 取消完成並驗證消失（4 道鎖全通過 ✅）"
            )
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
