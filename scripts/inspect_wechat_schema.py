#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect decrypted WeChat DB schema for mute/official-account related fields.

Usage examples:
  python scripts/inspect_wechat_schema.py --db decrypted_session.db --tables SessionTable
  python scripts/inspect_wechat_schema.py --db decrypted_contact.db --user brandsessionholder

This script prints column names and a sanitized row for a target username. It does
not print database keys and does not decrypt anything by itself.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

MASK_KEYS = {"enc_key", "key", "salt", "password", "token"}


def safe(v):
    if v is None:
        return None
    s = str(v)
    if len(s) > 120:
        return s[:80] + "...<truncated>"
    return s


def columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name").fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='decrypted sqlite db path')
    ap.add_argument('--tables', nargs='*', default=None)
    ap.add_argument('--user', default=None, help='username to sample, e.g. brandsessionholder or xxx@chatroom')
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f'DB not found: {db}')

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    all_tables = args.tables or tables(conn)

    for t in all_tables:
        try:
            cols = columns(conn, t)
        except Exception:
            continue
        print(f'\n## {t}')
        print('columns:', ', '.join(cols))
        interesting = [c for c in cols if any(k in c.lower() for k in ['mute','notify','notification','chatroom','brand','official','username','user','wxid','flag','verify'])]
        if interesting:
            print('interesting:', ', '.join(interesting))

        if args.user and any(c.lower() in ('username','user_name','strusername','userName'.lower(),'wxid','sessionname','strusrname') for c in cols):
            user_cols = [c for c in cols if c.lower() in ('username','user_name','strusername','wxid','sessionname','strusrname')]
            for uc in user_cols:
                try:
                    rows = conn.execute(f'SELECT * FROM "{t}" WHERE "{uc}"=? LIMIT 3', (args.user,)).fetchall()
                except Exception:
                    continue
                for row in rows:
                    print('sample row by', uc)
                    for k in row.keys():
                        if any(x in k.lower() for x in MASK_KEYS):
                            print(f'  {k}=<masked>')
                        else:
                            print(f'  {k}={safe(row[k])}')
    conn.close()

if __name__ == '__main__':
    main()
