# -*- coding: utf-8 -*-
"""Runtime path helpers for source mode and PyInstaller EXE mode."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "WxGuiNotifier"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_dir() -> Path:
    # PyInstaller one-file extracts files to sys._MEIPASS.
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> str:
    return str(bundle_dir().joinpath(*parts))


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        path = Path(base) / APP_NAME
    else:
        path = exe_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def portable_data_dir() -> Path:
    # If config exists next to EXE, prefer portable mode.
    p = exe_dir()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def writable_file(filename: str) -> Path:
    # Portable mode: prefer file next to exe if it already exists.
    p = portable_data_dir() / filename
    if p.exists():
        return p
    return user_data_dir() / filename


def config_file() -> Path:
    return writable_file("gui_config.json")


def keys_file() -> Path:
    return writable_file("all_keys.json")


def ensure_runtime_files() -> None:
    """Create empty runtime files if missing; do not overwrite user config/keys."""
    cfg = config_file()
    if not cfg.exists():
        example = Path(resource_path("gui_config.example.json"))
        if example.exists():
            cfg.write_text(example.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        else:
            cfg.write_text("{}", encoding="utf-8")

    kf = keys_file()
    if not kf.exists():
        kf.write_text("{}", encoding="utf-8")
