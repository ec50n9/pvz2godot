#!/usr/bin/env python3
"""
font2fnt.py — 将植物大战僵尸的字体描述 (FontTxt) 转换为 BMFont .fnt (Godot 可导入)

PvZ 字体由两部分组成:
  - 文本描述 (GBK 编码): Define CharList / WidthList / RectList / OffsetList
  - 字图 (PNG/GIF): 通常名为 _<字体名>.png

输出的 .fnt 为 BMFont 文本格式, Godot 4 可直接作为字体导入使用。
GIF 字图会自动转为 PNG (需要 ffmpeg)。

用法:
  python3 font2fnt.py <txt文件或目录> --images <字图目录> [更多目录...] --out <输出目录>
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys


def parse_font_txt(path: str) -> dict:
    text = open(path, "rb").read().decode("gbk", errors="replace")

    def section(name: str) -> str:
        m = re.search(rf"Define\s+{name}\s*\((.*?)\)\s*;", text, re.S)
        if not m:
            raise ValueError(f"{os.path.basename(path)}: 缺少 Define {name}")
        return m.group(1)

    chars = [m[0] or m[1] for m in re.findall(
        r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", section("CharList"))]
    widths = [int(x) for x in re.findall(r"-?\d+", section("WidthList"))]
    rects = [(int(a), int(b), int(c), int(d)) for a, b, c, d in
             re.findall(r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)",
                        section("RectList"))]
    offsets = [(int(a), int(b)) for a, b in
               re.findall(r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", section("OffsetList"))]
    if not (len(chars) == len(widths) == len(rects)):
        raise ValueError(f"{os.path.basename(path)}: CharList/WidthList/RectList 数量不一致 "
                         f"({len(chars)}/{len(widths)}/{len(rects)})")
    if len(offsets) < len(chars):
        offsets += [(0, 0)] * (len(chars) - len(offsets))
    return {"chars": chars, "widths": widths, "rects": rects, "offsets": offsets}


def image_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as f:
        head = f.read(26)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", head[6:10])
    raise ValueError(f"无法识别的图片格式: {path}")


def find_atlas(name: str, dirs: list[str]) -> str | None:
    # 名字可能带变体后缀 (如 BrianneTod32Black 用的是 BrianneTod32 的图),
    # 逐次削去尾部大写单词重试
    candidates = [name]
    while True:
        shorter = re.sub(r"[A-Z][a-z]+$", "", candidates[-1])
        if shorter == candidates[-1] or not shorter:
            break
        candidates.append(shorter)
    for cand in candidates:
        for d in dirs:
            for fn in os.listdir(d):
                stem, ext = os.path.splitext(fn)
                if stem.lstrip("_").lower() == cand.lower() and ext.lower() in (".png", ".gif"):
                    return os.path.join(d, fn)
    return None


def convert(txt_path: str, image_dirs: list[str], out_dir: str) -> str:
    name = os.path.splitext(os.path.basename(txt_path))[0]
    font = parse_font_txt(txt_path)
    atlas = find_atlas(name, image_dirs)
    if atlas is None:
        raise ValueError(f"找不到字图: _{name}.png / {name}.png")

    # 统一转为 RGBA8 PNG (Godot BMFont 导入器要求 FORMAT_RGBA8)
    atlas_name = name + ".png"
    atlas_out = os.path.join(out_dir, atlas_name)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", atlas, "-frames:v", "1", "-update", "1",
                    "-pix_fmt", "rgba", atlas_out], check=True)
    w, h = image_size(atlas_out)

    line_height = max(r[3] + font["offsets"][i][1] for i, r in enumerate(font["rects"]))
    lines = [
        f'info face="{name}" size={line_height} bold=0 italic=0 charset="" unicode=1 '
        f'stretchH=100 smooth=0 aa=1 padding=0,0,0,0 spacing=0,0',
        f'common lineHeight={line_height} base={line_height} scaleW={w} scaleH={h} pages=1 packed=0',
        f'page id=0 file="{atlas_name}"',
        f'chars count={len(font["chars"])}',
    ]
    for ch, adv, (x, y, cw, chh), (ox, oy) in zip(
            font["chars"], font["widths"], font["rects"], font["offsets"]):
        if not ch:
            continue
        code = ord(ch[0])
        lines.append(f"char id={code} x={x} y={y} width={cw} height={chh} "
                     f"xoffset={ox} yoffset={oy} xadvance={adv} page=0 chnl=15")
    fnt_path = os.path.join(out_dir, name + ".fnt")
    with open(fnt_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    return fnt_path


def main() -> int:
    ap = argparse.ArgumentParser(description="PVZ 字体描述 -> BMFont .fnt 转换器")
    ap.add_argument("input", help="字体 txt 文件或目录")
    ap.add_argument("--images", required=True, nargs="+", help="字图目录, 可多个")
    ap.add_argument("--out", required=True, help="输出目录")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    if os.path.isdir(args.input):
        files = [os.path.join(args.input, f) for f in sorted(os.listdir(args.input))
                 if f.endswith(".txt")]
    else:
        files = [args.input]

    ok = fail = 0
    for path in files:
        try:
            out = convert(path, args.images, args.out)
            ok += 1
            print("OK", os.path.basename(out))
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print("FAIL", os.path.basename(path), "-", exc)
    print(f"完成: {ok} 成功, {fail} 失败 -> {args.out}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
