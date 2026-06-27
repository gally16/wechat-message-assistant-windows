#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate Release Notes from git commits and current known fixes."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def git_lines(args):
    try:
        out = subprocess.check_output(["git", *args], text=True, encoding="utf-8", errors="replace")
        return [x.strip() for x in out.splitlines() if x.strip()]
    except Exception:
        return []


def main():
    version = os.environ.get("RELEASE_VERSION", "")
    tag = os.environ.get("RELEASE_TAG", f"v{version}" if version else "")
    previous_tag = os.environ.get("PREVIOUS_TAG", "")

    if previous_tag:
        commit_range = f"{previous_tag}..HEAD"
        commits = git_lines(["log", "--pretty=format:%s", commit_range])
    else:
        commits = git_lines(["log", "--pretty=format:%s", "-n", "30"])

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = []
    body.append(f"# WxGuiNotifier {tag}\n")
    body.append(f"发布时间：{now}\n")

    body.append("## 更新内容\n")
    body.append("- 自动构建 Windows 10/11 x64 可运行 EXE。")
    body.append("- 自动创建 GitHub Release，并上传便携版压缩包、EXE 和安装包（如果构建成功）。")
    body.append("- 版本号从 `1.1.1` 开始，按 `patch -> minor -> major` 自动进位。")

    body.append("\n## 修改与修复\n")
    # 固定摘要，方便用户阅读；具体提交记录在下面。
    body.append("- 优化微信消息过滤逻辑：支持免打扰会话过滤。")
    body.append("- 优化公众号文章过滤：兼容 `gh_` 公众号及 `brandsessionholder` 聚合会话。")
    body.append("- 优化通知图标：支持好友/群头像缓存与自定义头像。")
    body.append("- 优化 Win10 通知声音：增加系统声音兜底。")
    body.append("- 优化打包运行路径：兼容 PyInstaller EXE 运行时配置目录。")

    body.append("\n## 本次提交\n")
    if commits:
        for c in commits:
            body.append(f"- {c}")
    else:
        body.append("- 无可用提交记录。")

    body.append("\n## 文件说明\n")
    body.append("- `WxGuiNotifier.exe`：单文件可执行程序。")
    body.append("- `WxGuiNotifier-Portable-*.zip`：便携版压缩包。")
    body.append("- `WxGuiNotifier_Setup_*.exe`：安装包（如果 Inno Setup 构建成功）。")

    Path("RELEASE_NOTES.md").write_text("\n".join(body) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
