#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check runtime files expected by packaged WxGuiNotifier."""
from pathlib import Path
import json
import os

root = Path.cwd()
files = ["gui_config.json", "gui_config.example.json", "all_keys.json", "src/img/WeChat.png", "src/img/WeChat.ico"]
print("cwd =", root)
for f in files:
    p = root / f
    print(("OK  " if p.exists() else "MISS"), f, p)

cfg = root / "gui_config.json"
if cfg.exists():
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        for key in ["db_dir", "msg_db_path", "micro_db_path", "session_db_path"]:
            val = data.get(key)
            print(f"config {key}=", val, "exists=", bool(val and Path(val).exists()))
    except Exception as e:
        print("config parse error:", e)

print("APPDATA=", os.environ.get("APPDATA"))
