#!/usr/bin/env python3
"""
skill_loader.py — Fiona Skill 按需載入系統
精餾自 Claude Code 的 Skills 架構

設計原則（Claude Code）：
  - 描述永遠在 context（~150 chars each, 1% 預算）
  - 完整內容只在觸發時載入
  - Skill = 一組指令 + 必要的 context 檔案

用法：
  python3 skill_loader.py list                    # 列出所有 skill
  python3 skill_loader.py load <skill_name>       # 載入 skill
  python3 skill_loader.py describe                # 輸出所有描述（給 context 注入）
  python3 skill_loader.py match "user message"    # 根據訊息���配 skill
"""

import json
import sys
from pathlib import Path

WORKSPACE = Path('/Users/openmini/.openclaw/workspace-fiona')
SKILLS_DIR = WORKSPACE / 'skills'

# === Skill 定義 ===
# 每個 skill 的描述 < 150 chars，用於 context 注入
# 完整內容在對應的 SKILL.md 裡

SKILLS = {
    'booking': {
        'description': '羽球館預約操作：預約/取消/改期/查詢。硬規：找不到時間→停+回報。',
        'triggers': ['預約', '訂場', 'book', 'cancel', '取消', '改期', 'gobooking'],
        'files': ['SWITCHES.md', 'kb/venue.md'],
        'scripts': ['gobooking_book.py', 'gobooking_cancel.py', 'gobooking_playwright.py'],
    },
    'dispatch': {
        'description': '派工到 Codex/ACP：含預算檢查、風險門控、reviewer 分配、稽核。',
        'triggers': ['派工', 'dispatch', 'codex', 'ACP', '寫程式', '改bug'],
        'files': ['RUNBOOK.md', 'RUNBOOK_REF.md'],
        'scripts': ['dispatch_ticket.py', 'token_guard.py'],
    },
    'venue_control': {
        'description': '場館設備控制：燈/冷氣/門鎖/電壓監控。走 Tuya API + Miezo。',
        'triggers': ['開燈', '關燈', '冷氣', '門鎖', 'AC', 'light', 'lock', '設備'],
        'files': ['SWITCHES.md'],
        'scripts': ['outdoor_lights.py', 'venue_lights.py', 'ac_health_monitor.py', 'tuya_api.py'],
    },
    'email': {
        'description': '信箱管理：掃描Gmail/帳單/重要信件，分類後通知鳳老闆。',
        'triggers': ['信', 'email', 'Gmail', '帳單', 'bill', '信用卡'],
        'files': ['kb/email_digest.md'],
        'scripts': ['email_scanner.py', 'mail_router.py', 'bill_watcher.py'],
    },
    'finance': {
        'description': '投資理財：掃財經新聞、分析。買賣需鳳老闆說「執行」。',
        'triggers': ['股票', '投資', 'stock', 'finance', '理財', '基金'],
        'files': ['kb/finance.md'],
        'scripts': [],
    },
    'deploy': {
        'description': '部署到 GitHub+Zeabur。高風險操作，需 dry-run + 觀察期。',
        'triggers': ['部署', 'deploy', 'zeabur', 'github', 'push', '上線'],
        'files': ['RUNBOOK.md', 'kb/infra.md'],
        'scripts': ['dispatch_ticket.py'],
    },
    'health': {
        'description': '系統健檢：fast(5m)/brain(1h)/memory(4h)/daily。檢查服務狀態。',
        'triggers': ['健檢', 'health', 'status', '狀態', '監控'],
        'files': [],
        'scripts': ['health_batch_runner.py', 'startup_check.py', 'service_monitor.py'],
    },
    'memory': {
        'description': '記憶管理：autoDream蒸餾/qmd查詢/context預算/session hook。',
        'triggers': ['記憶', 'memory', 'dream', '蒸餾', 'context', 'token'],
        'files': ['AGENT_PATTERNS.md'],
        'scripts': ['fiona_dream.py', 'context_manager.py', 'session_hook.py'],
    },
    'parking': {
        'description': '停車空位查詢：台北停車場即時空位 + Telegram 通知。',
        'triggers': ['停車', 'parking', '車位'],
        'files': [],
        'scripts': ['parking_finder.py', 'parking_notify.py'],
    },
    'self_improve': {
        'description': '自我提升：自動捕獲錯誤/學習/功能需求，定期審查晉升。',
        'triggers': ['學習', 'learning', '錯誤', 'error', '改進', 'improve', '提升', 'review'],
        'files': [],
        'scripts': ['self_improve.py'],
    },
}


def cmd_list():
    """列出所有 skill"""
    print(f'Available Skills ({len(SKILLS)}):')
    for name, skill in SKILLS.items():
        triggers = ', '.join(skill['triggers'][:3])
        print(f'  [{name}] {skill["description"][:60]}...')
        print(f'    triggers: {triggers}')
        print(f'    files: {len(skill["files"])} | scripts: {len(skill["scripts"])}')


def cmd_describe():
    """輸出所有描述（適合注入 context，每 skill < 150 chars）"""
    total_chars = 0
    print('## Available Skills')
    for name, skill in SKILLS.items():
        line = f'- **{name}**: {skill["description"]}'
        print(line)
        total_chars += len(line)
    print(f'\n<!-- Total: {total_chars} chars, ~{int(total_chars * 1.5)} tokens -->')


def cmd_load(skill_name: str):
    """載入 skill 的完整內容"""
    if skill_name not in SKILLS:
        print(f'Unknown skill: {skill_name}')
        print(f'Available: {", ".join(SKILLS.keys())}')
        return

    skill = SKILLS[skill_name]
    print(f'=== Loading Skill: {skill_name} ===')
    print(f'Description: {skill["description"]}')

    # 載入關聯檔案
    if skill['files']:
        print(f'\nContext files:')
        for f in skill['files']:
            path = WORKSPACE / f
            if path.exists():
                size = len(path.read_text(encoding='utf-8'))
                print(f'  ✅ {f} ({size} chars)')
            else:
                print(f'  ❌ {f} (not found)')

    # 列出可用腳本
    if skill['scripts']:
        print(f'\nAvailable scripts:')
        for s in skill['scripts']:
            path = WORKSPACE / 'scripts' / s
            if path.exists():
                print(f'  ✅ {s}')
            else:
                print(f'  ❌ {s} (not found)')

    # 載入 skill 專用指令（如果有 SKILL.md）
    skill_md = SKILLS_DIR / skill_name / 'SKILL.md'
    if skill_md.exists():
        content = skill_md.read_text(encoding='utf-8')
        print(f'\nSkill instructions:\n{content}')
    else:
        print(f'\nNo SKILL.md found at {skill_md}')


def cmd_match(message: str):
    """��據用戶訊���匹配最佳 skill"""
    msg_lower = message.lower()
    matches = []

    for name, skill in SKILLS.items():
        score = 0
        for trigger in skill['triggers']:
            if trigger.lower() in msg_lower:
                score += 1

        if score > 0:
            matches.append((name, score, skill['description']))

    if not matches:
        print('No skill matched.')
        return

    matches.sort(key=lambda x: x[1], reverse=True)
    print('Matched Skills:')
    for name, score, desc in matches:
        print(f'  [{name}] score={score} — {desc[:60]}')

    best = matches[0]
    print(f'\nBest match: {best[0]}')
    return best[0]


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: skill_loader.py <list|load|describe|match> [args]')
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'list':
        cmd_list()
    elif cmd == 'describe':
        cmd_describe()
    elif cmd == 'load' and len(sys.argv) > 2:
        cmd_load(sys.argv[2])
    elif cmd == 'match' and len(sys.argv) > 2:
        cmd_match(' '.join(sys.argv[2:]))
    else:
        print(f'Unknown: {cmd}')
        sys.exit(1)
