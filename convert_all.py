#!/usr/bin/env python3
"""全量转换 compiled reanim 目录 → Godot .tscn。

按素材用途分组输出（每组一个子目录，贴图统一进该组的 textures/）：
    plants/     植物（清单见 convert_plants.PLANTS）
    zombies/    僵尸及僵尸相关（Zombie*、LawnMoweredZombie、ZombiesWon）
    credits/    制作人员名单演出（Credits_*）
    zengarden/  禅境花园（ZenGarden_*、Stinky、TreeFood、TreeOfWisdom*）
    items/      可拾取物（Coin_*、Diamond、Sun）
    misc/       其余（小推车、特效、界面动画等）

用法：
    python3 convert_all.py <compiled目录> <输出根目录> [过滤子串] [--tex 贴图目录 ...]

例：
    python3 convert_all.py pvz_assets/compiled/reanim out/ balloon \
        --tex pvz_assets/reanim pvz_assets/seanim pvz_assets/images

贴图源目录按优先级排列：同名贴图取排前面的目录。常见形态：
  1. 普通 .png：直接可用；
  2. .jpg + 灰度 <名>_.png：合成 RGBA png（需要 Pillow）；
  3. 单独的 .jpg：无透明，直接用。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from convert import convert
from convert_plants import PLANTS

PLANT_NAMES = {p.lower() for p in PLANTS}

ZOMBIE_EXTRA = {"lawnmoweredzombie", "zombieswon"}
ZEN_EXTRA = {"stinky", "treefood", "treeofwisdom", "treeofwisdomclouds"}
ITEMS = {"coin_gold", "coin_silver", "diamond", "sun"}


def group_of(stem: str) -> str:
    n = stem.lower()
    if n in PLANT_NAMES:
        return "plants"
    if n.startswith("zombie") or n in ZOMBIE_EXTRA:
        return "zombies"
    if n.startswith("credits_"):
        return "credits"
    if n.startswith("zengarden") or n in ZEN_EXTRA:
        return "zengarden"
    if n in ITEMS:
        return "items"
    return "misc"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src_dir", help="compiled reanim 目录（*.reanim.compiled）")
    ap.add_argument("out_base", help="输出根目录（相对 Godot 项目根，生成 res:// 引用）")
    ap.add_argument("pattern", nargs="?", default="", help="只转换文件名含此子串的")
    ap.add_argument("--tex", nargs="+", required=True,
                    help="贴图源目录，可多个（按优先级排列）")
    args = ap.parse_args()

    pattern = args.pattern.lower()
    ok, failed = 0, []
    for f in sorted(os.listdir(args.src_dir)):
        if not f.endswith(".reanim.compiled"):
            continue
        stem = f[: -len(".reanim.compiled")]
        if pattern and pattern not in stem.lower():
            continue
        group = group_of(stem)
        out_dir = os.path.join(args.out_base, group)
        try:
            convert(os.path.join(args.src_dir, f), args.tex,
                    os.path.join(out_dir, stem.lower() + ".tscn"),
                    "res://" + out_dir + "/textures")
            ok += 1
        except Exception as e:  # noqa: BLE001 - 单个失败不应中断全量转换
            failed.append((stem, str(e)))
    print("\n完成 %d 个" % ok)
    for stem, err in failed:
        print("失败 %s: %s" % (stem, err))


if __name__ == "__main__":
    main()
