#!/usr/bin/env python3
"""
unpack_pak.py — 解包植物大战僵尸的 main.pak 资源包

PvZ 年度版/中文版的 main.pak 格式:
  - 整个文件按字节 XOR 0xF7 混淆
  - 解码后:
      u32 magic = 0xBAC04AC0
      u32 version (0)
      记录列表, 每条:
        u8  flag (0x00 = 记录开始, 非 0 = 列表结束)
        u8  name_len
        char name[name_len]   (Windows 风格路径, 反斜杠分隔)
        u32 size
        u64 last_write_time   (Windows FILETIME)
      之后是所有文件数据, 按记录顺序紧密排列

用法:
  python3 unpack_pak.py <main.pak> <输出目录>
"""

from __future__ import annotations

import os
import struct
import sys

XOR_KEY = 0xF7
PAK_MAGIC = 0xBAC04AC0


def unpack_pak(pak_path: str, out_dir: str) -> int:
    raw = open(pak_path, "rb").read()
    data = bytes(b ^ XOR_KEY for b in raw)

    magic, _version = struct.unpack_from("<II", data, 0)
    if magic != PAK_MAGIC:
        raise ValueError(f"不是有效的 PvZ PAK 文件 (magic={magic:#x})")

    # 解析记录列表
    off = 8
    records: list[tuple[str, int]] = []
    while True:
        flag = data[off]
        off += 1
        if flag != 0:
            break
        name_len = data[off]
        off += 1
        name = data[off:off + name_len].decode("ascii")
        off += name_len
        size, = struct.unpack_from("<I", data, off)
        off += 4
        off += 8  # FILETIME
        records.append((name, size))

    # 解出文件数据
    pos = off
    for name, size in records:
        rel = name.replace("\\", "/")
        dst = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data[pos:pos + size])
        pos += size

    if pos != len(data):
        print(f"警告: 解包结束时偏移 {pos} != 文件长度 {len(data)}")
    return len(records)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    pak_path, out_dir = sys.argv[1], sys.argv[2]
    count = unpack_pak(pak_path, out_dir)
    print(f"完成: 解出 {count} 个文件 -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
