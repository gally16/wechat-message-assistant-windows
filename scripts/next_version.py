#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compute next release version for WxGuiNotifier.

Rule requested by gally:
- initial version: 1.1.1
- increment patch by default
- when a segment becomes greater than 10, carry to the previous segment
  Examples:
    no tag     -> 1.1.1
    1.1.1      -> 1.1.2
    1.1.10     -> 1.2.0
    1.10.10    -> 2.0.0

The script writes GitHub Actions outputs:
- version: 1.1.1
- tag: v1.1.1
- previous_tag: latest existing tag or blank
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

INITIAL = (1, 1, 1)
TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()


def parse_tag(tag: str):
    m = TAG_RE.match(tag.strip())
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def bump(v):
    major, minor, patch = v
    patch += 1
    if patch > 10:
        patch = 0
        minor += 1
    if minor > 10:
        minor = 0
        major += 1
    return major, minor, patch


def main():
    try:
        # Make sure tags are available even with shallow checkout.
        subprocess.run(["git", "fetch", "--tags", "--force"], check=False)
        raw_tags = run(["git", "tag", "--list", "v*.*.*"])
    except Exception:
        raw_tags = ""

    candidates = []
    for t in raw_tags.splitlines():
        parsed = parse_tag(t)
        if parsed:
            candidates.append((parsed, t))

    if not candidates:
        version = INITIAL
        previous_tag = ""
    else:
        latest_version, previous_tag = sorted(candidates, key=lambda x: x[0])[-1]
        version = bump(latest_version)

    version_s = ".".join(map(str, version))
    tag = f"v{version_s}"

    # Update version.json if present, otherwise create it.
    version_file = Path("version.json")
    data = {}
    if version_file.exists():
        try:
            data = json.loads(version_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["version"] = version_s
    data["tag"] = tag
    version_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    github_output = os.environ.get("GITHUB_OUTPUT")
    lines = [f"version={version_s}", f"tag={tag}", f"previous_tag={previous_tag}"]
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
