"""测试多用户扫描功能"""
from .gui_config import scan_all_wechat_dirs

candidates = scan_all_wechat_dirs()
print(f'扫描到 {len(candidates)} 个用户:')
for c in candidates:
    print(f'  - {c["wxid"]}: {c["path"]}')
    print(f'    显示：{c["display"]}')
    print(f'    修改时间：{c["mtime"]}')
