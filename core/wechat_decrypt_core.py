"""
微信数据库解密核心模块

从 wechat-decrypt/monitor_web.py 提取的独立解密模块
不依赖任何外部配置，专门用于 PyInstaller 打包
"""

import os
import sys
import time
import struct
import sqlite3
from Crypto.Cipher import AES

# 常量定义
PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
RESERVE_SZ = 80
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24
SQLITE_HDR = b'SQLite format 3\x00'


def decrypt_page(enc_key, page_data, pgno):
    """
    解密单个加密页面
    
    Args:
        enc_key: 解密密钥（字节）
        page_data: 加密页面数据（4096 字节）
        pgno: 页面编号（从 1 开始）
    
    Returns:
        解密后的页面数据（4096 字节）
    """
    iv = page_data[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + 16]
    
    if pgno == 1:
        # 第 1 页包含 SQLite 头部
        encrypted = page_data[SALT_SZ: PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return bytearray(SQLITE_HDR + decrypted + b'\x00' * RESERVE_SZ)
    else:
        # 其他页
        encrypted = page_data[:PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return decrypted + b'\x00' * RESERVE_SZ


def full_decrypt(db_path, out_path, enc_key):
    """
    首次全量解密数据库文件
    
    Args:
        db_path: 加密数据库文件路径
        out_path: 输出解密文件路径
        enc_key: 解密密钥（字节）
    
    Returns:
        (页面数，耗时毫秒)
    """
    t0 = time.perf_counter()
    file_size = os.path.getsize(db_path)
    total_pages = file_size // PAGE_SZ
    
    # 确保输出目录存在
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    
    with open(db_path, 'rb') as fin, open(out_path, 'wb') as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                if len(page) > 0:
                    page = page + b'\x00' * (PAGE_SZ - len(page))
                else:
                    break
            fout.write(decrypt_page(enc_key, page, pgno))
    
    ms = (time.perf_counter() - t0) * 1000
    return total_pages, ms


def decrypt_wal_full(wal_path, out_path, enc_key):
    """
    解密 WAL 当前有效 frame，patch 到已解密的 DB 副本
    
    WAL 是预分配固定大小 (4MB)，包含当前有效 frame 和上一轮遗留的旧 frame。
    通过 WAL header 中的 salt 值区分：只有 frame header 的 salt 匹配 WAL header 的才是有效 frame。
    
    Args:
        wal_path: WAL 文件路径
        out_path: 已解密的 DB 副本路径（会被 patch）
        enc_key: 解密密钥（字节）
    
    Returns:
        (patched_pages, elapsed_ms)
    """
    t0 = time.perf_counter()
    
    if not os.path.exists(wal_path):
        return 0, 0
    
    wal_size = os.path.getsize(wal_path)
    if wal_size <= WAL_HEADER_SZ:
        return 0, 0
    
    frame_size = WAL_FRAME_HEADER_SZ + PAGE_SZ  # 24 + 4096 = 4120
    patched = 0
    
    with open(wal_path, 'rb') as wf, open(out_path, 'r+b') as df:
        # 读 WAL header，获取当前 salt 值
        wal_hdr = wf.read(WAL_HEADER_SZ)
        wal_salt1 = struct.unpack('>I', wal_hdr[16:20])[0]
        wal_salt2 = struct.unpack('>I', wal_hdr[20:24])[0]
        
        while wf.tell() + frame_size <= wal_size:
            fh = wf.read(WAL_FRAME_HEADER_SZ)
            if len(fh) < WAL_FRAME_HEADER_SZ:
                break
            
            pgno = struct.unpack('>I', fh[0:4])[0]
            frame_salt1 = struct.unpack('>I', fh[8:12])[0]
            frame_salt2 = struct.unpack('>I', fh[12:16])[0]
            
            ep = wf.read(PAGE_SZ)
            if len(ep) < PAGE_SZ:
                break
            
            # 校验：pgno 有效 且 salt 匹配当前 WAL 周期
            if pgno == 0 or pgno > 1000000:
                continue
            if frame_salt1 != wal_salt1 or frame_salt2 != wal_salt2:
                continue  # 旧周期遗留的 frame，跳过
            
            dec = decrypt_page(enc_key, ep, pgno)
            df.seek((pgno - 1) * PAGE_SZ)
            df.write(dec)
            patched += 1
    
    ms = (time.perf_counter() - t0) * 1000
    return patched, ms
