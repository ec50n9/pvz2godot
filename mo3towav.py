#!/usr/bin/env python3
"""
mo3towav.py — 将 MO3 音乐 (BASS 压缩 Tracker 模块) 解码为 WAV

MO3 是 un4seen BASS 音频库的私有格式, ffmpeg 不支持。
本脚本通过 ctypes 调用官方 libbass 解码 (PvZ 游戏本体也是用 bass.dll 播放的)。

获取 libbass (任选其一):
  - 官网: https://www.un4seen.com/bass.html (下载 macOS/Linux/Windows 版本)
  - NuGet: ppy.osu.Framework.NativeLibs 包内含各平台 libbass (osu! 使用的同款):
    https://www.nuget.org/packages/ppy.osu.Framework.NativeLibs
    解压 nupkg (zip) 后在 runtimes/<平台>/native/ 下找到 libbass.*

用法:
  python3 mo3towav.py <mo3文件或目录> --out <输出目录> [--bass /path/to/libbass.dylib]

之后可用 ffmpeg 转 OGG 供 Godot 使用:
  ffmpeg -i mainmusic.wav -c:a vorbis -strict -2 mainmusic.ogg
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import wave

BASS_MUSIC_DECODE = 0x200000
BASS_MUSIC_PRESCAN = 0x20000

LIB_CANDIDATES = [
    "./libbass.dylib", "./libbass.so", "./libbass.dll",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "libbass.dylib"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "libbass.so"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "libbass.dll"),
]


def load_bass(path: str | None) -> ctypes.CDLL:
    candidates = [path] if path else []
    candidates += [os.environ.get("BASS_LIB", "")] + LIB_CANDIDATES
    for cand in candidates:
        if cand and os.path.exists(cand):
            return ctypes.CDLL(cand)
    raise FileNotFoundError(
        "找不到 libbass, 请用 --bass 指定路径 (获取方式见脚本头部注释)")


def decode_mo3(lib: ctypes.CDLL, src: str, dst: str, freq: int = 44100) -> float:
    data = open(src, "rb").read()
    buf = ctypes.create_string_buffer(data, len(data))
    h = lib.BASS_MusicLoad(True, buf, 0, len(data),
                           BASS_MUSIC_DECODE | BASS_MUSIC_PRESCAN, 0)
    if not h:
        raise RuntimeError(f"BASS_MusicLoad 失败, 错误码 {lib.BASS_ErrorGetCode()}")
    total = lib.BASS_ChannelGetLength(h)  # prescan 后的精确字节数
    chunk = ctypes.create_string_buffer(65536)
    with wave.open(dst, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(freq)
        got = 0
        while got < total:
            want = min(65536, total - got)
            n = lib.BASS_ChannelGetData(h, chunk, want)
            if n in (0, 0xFFFFFFFF):
                break
            w.writeframes(chunk.raw[:n])
            got += n
    lib.BASS_MusicFree(ctypes.c_uint32(h))
    return total / 4 / freq


def main() -> int:
    ap = argparse.ArgumentParser(description="MO3 -> WAV 解码器 (基于 libbass)")
    ap.add_argument("input", help=".mo3 文件或目录")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--bass", help="libbass 动态库路径")
    ap.add_argument("--freq", type=int, default=44100, help="采样率 (默认 44100)")
    args = ap.parse_args()

    lib = load_bass(args.bass)
    lib.BASS_ErrorGetCode.restype = ctypes.c_int
    lib.BASS_Init.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32,
                              ctypes.c_void_p, ctypes.c_void_p]
    lib.BASS_Init.restype = ctypes.c_bool
    lib.BASS_MusicLoad.argtypes = [ctypes.c_bool, ctypes.c_void_p, ctypes.c_uint64,
                                   ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    lib.BASS_MusicLoad.restype = ctypes.c_uint32
    lib.BASS_ChannelGetLength.argtypes = [ctypes.c_uint32]
    lib.BASS_ChannelGetLength.restype = ctypes.c_uint64
    lib.BASS_ChannelGetData.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    lib.BASS_ChannelGetData.restype = ctypes.c_uint32
    if not lib.BASS_Init(0, args.freq, 0, None, None):  # 设备 0 = 无声解码设备
        raise RuntimeError(f"BASS_Init 失败, 错误码 {lib.BASS_ErrorGetCode()}")

    os.makedirs(args.out, exist_ok=True)
    if os.path.isdir(args.input):
        files = [os.path.join(args.input, f) for f in sorted(os.listdir(args.input))
                 if f.lower().endswith(".mo3")]
    else:
        files = [args.input]

    ok = fail = 0
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        dst = os.path.join(args.out, name + ".wav")
        try:
            secs = decode_mo3(lib, path, dst, args.freq)
            ok += 1
            print(f"OK {name}.wav ({secs:.1f} 秒)")
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print("FAIL", os.path.basename(path), "-", exc)
    lib.BASS_Free()
    print(f"完成: {ok} 成功, {fail} 失败 -> {args.out}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
