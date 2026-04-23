# Gobooking Agent — 操作規則

## ⛔ 鐵律（不得違反，無例外）

### 時間
- 找不到指定開始時間 → **立刻 raise，中止，回報鳳老闆，不改時間**
- 找不到指定結束時間 → **立刻 raise，中止，回報鳳老闆，不改時間**
- 時間衝突（已被預約）→ **立刻中止，回報，不換時段、不換場地**
- 任何「fallback 選第一個可用時段」邏輯 → **絕對禁止**

### 預約執行
- dry-run 通過 + 鳳老闆說「執行」→ 才能正式跑
- 正式執行時看到任何警告 → **立刻停止，回報，等指示**
- 不得自行決定換時間、換場地、換日期

---

## 直接執行原則

收到指令 → 直接執行 → 回報結果，不問：
- 「後台最短時租調好了嗎？」← 自己調
- 「要我開始執行嗎？」← 說執行就執行
- 「確認後台設定好了嗎？」← 自己確認

---

## 30 分鐘預約標準流程（例：9:30-10:00）

```
1. set_booking_window(110)           # 開放足夠天數
2. set_min_booking_minutes(30, ['A'])  # 最短時租改 30 分
3. 跑批次（加 --no-set-days）
4. set_min_booking_minutes(60, ['A'])  # 還原 60 分
5. set_booking_window(30)            # 還原天數
```

批次用 `batch_chen_junjie.py`，不要一筆一筆手動跑。

---

## 重要腳本

| 腳本 | 用途 |
|------|------|
| `batch_chen_junjie.py` | 陳俊傑 9:30 × 12日 + 12:00 × 2日 批次 |
| `gobooking_playwright.py` | 單筆預約（含後台時租/天數自動調整） |
| `gobooking_cancel.py` | 取消訂單 |
| `gobooking_batch.py` | 通用批次框架 |

---

## 今日事故紀錄（2026-04-23）

**錯誤**：gobooking_playwright.py 找不到時間時會 fallback 選第一個可用時段，
結果 9:30 選成 00:00，預約了 00:00-10:00 的 10 小時時段。

**已修正**：找不到時間改為 `raise RuntimeError`，直接中止。

**教訓**：任何 fallback 邏輯都是危險的，時間不對就停。

---

## 即刻現場預約

```
python3 gobooking_playwright.py --room A --date 2026/05/17 \
  --start now --name "姓名" --phone "09xx" --email "x@x" --no-set-days
```

`--start now` 選前台「即刻 Now」，不需要時間。
