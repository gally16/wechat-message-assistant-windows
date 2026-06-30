"""
密钥扫描公共模块
跨平台共享的内存扫描逻辑：HMAC 验证、DB 收集、hex 模式匹配与结果输出。
"""
import hashlib
import hmac as hmac_mod
import json
import os
import re
import struct
import time

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16


def verify_enc_key(enc_key, db_page1):
    """通过 HMAC-SHA512 校验 page 1 验证 enc_key 是否正确。"""
    salt = db_page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = db_page1[SALT_SZ: PAGE_SZ - 80 + 16]
    stored_hmac = db_page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def collect_db_files(db_dir):
    """遍历 db_dir 收集所有 .db 文件及其 salt。

    返回 (db_files, salt_to_dbs):
      db_files: [(rel_path, abs_path, size, salt_hex, page1_bytes), ...]
      salt_to_dbs: {salt_hex: [rel_path, ...]}
    """
    db_files = []
    salt_to_dbs = {}
    for root, dirs, files in os.walk(db_dir):
        for name in files:
            if not name.endswith(".db") or name.endswith("-wal") or name.endswith("-shm"):
                continue
            path = os.path.join(root, name)
            size = os.path.getsize(path)
            if size < PAGE_SZ:
                continue
            with open(path, "rb") as f:
                page1 = f.read(PAGE_SZ)
            rel = os.path.relpath(path, db_dir)
            salt = page1[:SALT_SZ].hex()
            db_files.append((rel, path, size, salt, page1))
            salt_to_dbs.setdefault(salt, []).append(rel)
    return db_files, salt_to_dbs


def _record_found_key(enc_key, salt_hex, db_files, salt_to_dbs, key_map,
                      remaining_salts, addr, pid, print_fn, source):
    """验证并记录指定 salt 的候选 key。"""
    if salt_hex not in remaining_salts:
        return False
    if len(enc_key) != KEY_SZ:
        return False

    for rel, path, sz, s, page1 in db_files:
        if s != salt_hex:
            continue
        if verify_enc_key(enc_key, page1):
            enc_key_hex = enc_key.hex()
            key_map[salt_hex] = enc_key_hex
            remaining_salts.discard(salt_hex)
            dbs = salt_to_dbs[salt_hex]
            print_fn(f"\n  [FOUND] salt={salt_hex} ({source})")
            print_fn(f"    enc_key={enc_key_hex}")
            print_fn(f"    PID={pid} 地址：0x{addr:016X}")
            print_fn(f"    数据库：{', '.join(dbs)}")
            return True
        break
    return False


def _record_key_for_any_salt(enc_key, db_files, salt_to_dbs, key_map,
                             remaining_salts, addr, pid, print_fn, source):
    """没有伴随 salt 时，用候选 key 依次验证剩余数据库。"""
    if len(enc_key) != KEY_SZ or not remaining_salts:
        return False
    for rel, path, sz, salt_hex, page1 in db_files:
        if salt_hex not in remaining_salts:
            continue
        if verify_enc_key(enc_key, page1):
            return _record_found_key(
                enc_key, salt_hex, db_files, salt_to_dbs, key_map,
                remaining_salts, addr, pid, print_fn, source,
            )
    return False


def _decode_hex_match(raw):
    """兼容 ASCII 与 UTF-16LE 形态的 hex 字符串。"""
    if b"\x00" in raw:
        raw = raw.replace(b"\x00", b"")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return ""


def _try_hex_candidate(hex_str, db_files, salt_to_dbs, key_map,
                       remaining_salts, addr, pid, print_fn, source):
    """验证一个 64+ 长度的 hex 候选。"""
    hex_str = hex_str.strip()
    hex_len = len(hex_str)
    if hex_len < 64 or hex_len % 2:
        return False

    # 纯 64 位 hex：它可能就是 enc_key。
    if hex_len == 64:
        try:
            return _record_key_for_any_salt(
                bytes.fromhex(hex_str), db_files, salt_to_dbs, key_map,
                remaining_salts, addr, pid, print_fn, source,
            )
        except ValueError:
            return False

    found = False
    salts_snapshot = list(remaining_salts)
    for salt_hex in salts_snapshot:
        idx = hex_str.find(salt_hex)
        while idx >= 0:
            candidate_starts = {
                idx - 64,      # key + salt
                idx + 32,      # salt + key
                0,             # 老版本常见：key 在长串头部
                hex_len - 64,  # key 在长串尾部
            }
            for start in candidate_starts:
                if start < 0 or start + 64 > hex_len:
                    continue
                key_hex = hex_str[start:start + 64]
                if key_hex == salt_hex * 2:
                    continue
                try:
                    enc_key = bytes.fromhex(key_hex)
                except ValueError:
                    continue
                if _record_found_key(
                    enc_key, salt_hex, db_files, salt_to_dbs, key_map,
                    remaining_salts, addr + max(0, start // 2), pid, print_fn, source,
                ):
                    found = True
                    break
            if found:
                break
            idx = hex_str.find(salt_hex, idx + 1)

    # 长 hex 里没有 salt 时，仍尝试头/尾 32 字节作为 key。
    if not found:
        for key_hex, offset in ((hex_str[:64], 0), (hex_str[-64:], max(0, (hex_len - 64) // 2))):
            try:
                enc_key = bytes.fromhex(key_hex)
            except ValueError:
                continue
            if _record_key_for_any_salt(
                enc_key, db_files, salt_to_dbs, key_map,
                remaining_salts, addr + offset, pid, print_fn, source,
            ):
                found = True
                break

    return found


def _looks_like_key(candidate):
    if len(candidate) != KEY_SZ:
        return False
    # 避免对明显填充区域做大量 PBKDF2/HMAC。
    return not (candidate == b"\x00" * KEY_SZ or candidate == b"\xff" * KEY_SZ)


def _scan_raw_keys_near_salts(data, db_files, salt_to_dbs, key_map,
                              remaining_salts, base_addr, pid, print_fn):
    """新版微信可能不再保留 x'hex' 字符串，尝试在 salt 附近验证 raw 32-byte key。"""
    if not remaining_salts:
        return 0

    matches = 0
    radius = 512
    max_candidates_per_salt = 4096
    for salt_hex in list(remaining_salts):
        salt_bytes = bytes.fromhex(salt_hex)
        pos = data.find(salt_bytes)
        tested = set()
        tested_count = 0
        while pos >= 0 and salt_hex in remaining_salts:
            matches += 1

            # 先验证最常见的紧邻布局，再扫描附近窗口。
            starts = [pos - KEY_SZ, pos + SALT_SZ]
            starts.extend(range(max(0, pos - radius), min(len(data) - KEY_SZ + 1, pos + radius + 1)))

            for start in starts:
                if start < 0 or start + KEY_SZ > len(data):
                    continue
                if start in tested:
                    continue
                tested.add(start)
                candidate = data[start:start + KEY_SZ]
                if not _looks_like_key(candidate):
                    continue
                tested_count += 1
                if _record_found_key(
                    candidate, salt_hex, db_files, salt_to_dbs, key_map,
                    remaining_salts, base_addr + start, pid, print_fn, "raw-near-salt",
                ):
                    break
                if tested_count >= max_candidates_per_salt:
                    break

            if tested_count >= max_candidates_per_salt:
                break
            pos = data.find(salt_bytes, pos + 1)

    return matches


def scan_memory_for_keys(data, hex_re, db_files, salt_to_dbs, key_map,
                         remaining_salts, base_addr, pid, print_fn):
    """扫描一段内存数据，匹配 hex 模式并验证密钥。

    返回本次扫描匹配到的 hex 模式数量。
    """
    matches = 0
    patterns = hex_re
    if hasattr(hex_re, "finditer"):
        patterns = [("hex", hex_re)]

    for source, pattern in patterns:
        if not remaining_salts:
            break
        for m in pattern.finditer(data):
            hex_str = _decode_hex_match(m.group(1))
            if not hex_str:
                continue
            matches += 1
            _try_hex_candidate(
                hex_str, db_files, salt_to_dbs, key_map,
                remaining_salts, base_addr + m.start(), pid, print_fn, source,
            )

    matches += _scan_raw_keys_near_salts(
        data, db_files, salt_to_dbs, key_map,
        remaining_salts, base_addr, pid, print_fn,
    )

    return matches


def cross_verify_keys(db_files, salt_to_dbs, key_map, print_fn):
    """用已找到的 key 交叉验证未匹配的 salt。"""
    missing_salts = set(salt_to_dbs.keys()) - set(key_map.keys())
    if not missing_salts or not key_map:
        return
    print_fn(f"\n还有 {len(missing_salts)} 个 salt 未匹配，尝试交叉验证...")
    for salt_hex in list(missing_salts):
        for rel, path, sz, s, page1 in db_files:
            if s == salt_hex:
                for known_salt, known_key_hex in key_map.items():
                    enc_key = bytes.fromhex(known_key_hex)
                    if verify_enc_key(enc_key, page1):
                        key_map[salt_hex] = known_key_hex
                        print_fn(f"  [CROSS] salt={salt_hex} 可用 key from salt={known_salt}")
                        missing_salts.discard(salt_hex)
                break


def save_results(db_files, salt_to_dbs, key_map, db_dir, out_file, print_fn):
    """输出扫描结果并保存 JSON。"""
    print_fn(f"\n{'=' * 60}")
    print_fn(f"结果：{len(key_map)}/{len(salt_to_dbs)} salts 找到密钥")

    result = {}
    for rel, path, sz, salt_hex, page1 in db_files:
        if salt_hex in key_map:
            result[rel] = {
                "enc_key": key_map[salt_hex],
                "salt": salt_hex,
                "size_mb": round(sz / 1024 / 1024, 1)
            }
            print_fn(f"  OK: {rel} ({sz / 1024 / 1024:.1f}MB)")
        else:
            print_fn(f"  MISSING: {rel} (salt={salt_hex})")

    if not result:
        print_fn(f"\n[!] 未提取到任何密钥，保留已有的 {out_file}（如存在）")
        raise RuntimeError("未能从任何微信进程中提取到密钥")

    result["_db_dir"] = db_dir
    out_dir = os.path.dirname(os.path.abspath(out_file))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print_fn(f"\n密钥保存到：{out_file}")

    missing = [rel for rel, path, sz, salt_hex, page1 in db_files if salt_hex not in key_map]
    if missing:
        print_fn(f"\n未找到密钥的数据库:")
        for rel in missing:
            print_fn(f"  {rel}")
