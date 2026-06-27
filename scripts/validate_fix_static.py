#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Static validator for gally16/wechat-message-assistant-windows fixes.
It checks whether the expected symbols/logic are present before packaging.
It does not access WeChat data, extract keys, or run privacy-sensitive code.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
py_files = [p for p in ROOT.rglob('*.py') if '.git' not in p.parts and 'build' not in p.parts]
texts = {str(p.relative_to(ROOT)): p.read_text(encoding='utf-8', errors='ignore') for p in py_files}
all_text = '\n'.join(texts.values())

checks = []

def check(name, ok, hint):
    checks.append((name, bool(ok), hint))

# 1. Mute detection
check(
    '存在 SessionTable 免打扰字段自省函数',
    '_detect_session_mute_column' in all_text,
    '需要新增 _detect_session_mute_column()，通过 PRAGMA table_info(SessionTable) 动态识别 mute 字段。'
)
check(
    '存在 PRAGMA table_info 自省',
    'PRAGMA table_info' in all_text and 'SessionTable' in all_text,
    '需要使用 PRAGMA table_info(SessionTable) 兼容多版本字段名。'
)
check(
    '覆盖常见免打扰字段名',
    any(x in all_text for x in ['mute_notification', 'is_mute', 'is_muted', 'notification_on']),
    '建议覆盖 mute_notification/is_mute/mute/notification_on/is_muted。'
)
check(
    '存在 _is_session_muted 判断函数',
    '_is_session_muted' in all_text,
    '需要把不同字段值统一转换成布尔免打扰状态。'
)
check(
    '存在 contact_mute_map 兜底',
    'contact_mute_map' in all_text,
    '需要在 load_contacts() 加载联系人层面的免打扰状态作为兜底。'
)

# 2. Official account filtering
check(
    '存在 _should_skip 过滤入口',
    '_should_skip' in all_text,
    '需要将免打扰和公众号过滤集中到 _should_skip(username, msg_type, is_muted)。'
)
check(
    '存在公众号 gh_ 识别',
    re.search(r'gh_', all_text) is not None,
    '公众号 username 通常以 gh_ 开头，应有识别逻辑。'
)
check(
    '存在 msg_type 49/1 过滤',
    ('49' in all_text and 'msg_type' in all_text and 'filter_official_article' in all_text),
    '公众号文章/富媒体通常需要过滤 msg_type=49，文本推广可考虑 msg_type=1。'
)
check(
    '过滤后仍更新 prev_session_state',
    'prev_session_state' in all_text and re.search(r'prev_session_state.*=', all_text, re.S),
    '需要确保被过滤消息也更新 prev_session_state，避免取消免打扰后历史补推。'
)

# 3. Avatar cache
avatar_file = ROOT / 'utils' / 'avatar_cache.py'
check(
    '存在 utils/avatar_cache.py',
    avatar_file.exists(),
    '需要新增头像缓存模块 utils/avatar_cache.py。'
)
if avatar_file.exists():
    avatar_text = avatar_file.read_text(encoding='utf-8', errors='ignore')
else:
    avatar_text = ''
check(
    '头像模块使用 Pillow 处理 96x96 PNG',
    ('PIL' in avatar_text or 'Image' in avatar_text) and ('96' in avatar_text),
    '头像模块应使用 Pillow 居中裁剪/缩放为 96x96 PNG。'
)
check(
    '头像模块包含缓存/过期/失败冷却',
    any(x in avatar_text.lower() for x in ['cache', 'ttl', 'expire', 'cooldown']) or any(x in avatar_text for x in ['缓存', '过期', '冷却']),
    '建议实现磁盘缓存、7天有效期、失败冷却。'
)
check(
    'send_notification 支持 icon_path',
    re.search(r'def\s+send_notification\s*\([^)]*icon_path', all_text, re.S) is not None,
    'send_notification() 应增加 icon_path=None 参数并传给 winotify Notification(icon=...)。'
)
check(
    'send_notification 支持 username 参数',
    re.search(r'def\s+send_notification\s*\([^)]*username', all_text, re.S) is not None,
    'send_notification() 建议支持 username，用于按联系人/群聊取头像。'
)

# 4. UI config switches
check(
    '存在 filter_mute 配置',
    'filter_mute' in all_text,
    '运行配置面板和 gui_config.json 应持久化 filter_mute。'
)
check(
    '存在 filter_official_article 配置',
    'filter_official_article' in all_text,
    '运行配置面板和 gui_config.json 应持久化 filter_official_article。'
)
check(
    'QFluentWidgets 图标枚举有回退保护',
    ('try:' in all_text and 'except' in all_text and ('FIF.' in all_text or 'FluentIcon' in all_text)),
    '对可能缺失的 MUTE/MESSAGE 枚举应有 try/except 回退。'
)

failed = [c for c in checks if not c[1]]
for name, ok, hint in checks:
    mark = '✅' if ok else '❌'
    print(f'{mark} {name}')
    if not ok:
        print(f'   - {hint}')

print('\nSummary: %d passed, %d failed' % (len(checks)-len(failed), len(failed)))
if failed:
    sys.exit(1)
