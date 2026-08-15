#!/usr/bin/env python3
"""批量转换植物 reanim → Godot .tscn。

用法：
    python3 convert_plants.py <compiled目录> <输出目录> [--tex 贴图目录] [名字过滤...]

例：
    python3 convert_plants.py pvz_assets/compiled/reanim out/plants \
        --tex pvz_assets/reanim                          # 全部 49 种植物
    python3 convert_plants.py pvz_assets/compiled/reanim out/plants \
        pea sun wall --tex pvz_assets/reanim             # 按子串过滤

注意：--tex 是变长参数会吃掉后面的位置参数，过滤词请放在 --tex 之前。

输出：<输出目录>/<小写名>.tscn + 贴图（<输出目录>/textures/）。
"""
import argparse
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from convert import convert

# PvZ1 全部 49 种植物（含紫卡与 PeaShooterSingle 变体）
PLANTS = [
    "PeaShooter", "SunFlower", "CherryBomb", "Wallnut", "PotatoMine", "SnowPea",
    "Chomper", "Puffshroom", "SunShroom", "Fumeshroom", "Gravebuster",
    "Hypnoshroom", "ScaredyShroom", "Iceshroom", "DoomShroom", "Lilypad", "Squash",
    "ThreePeater", "Tanglekelp", "Jalapeno", "Caltrop", "Torchwood", "Tallnut",
    "SeaShroom", "Plantern", "Cactus", "Blover", "SplitPea", "Starfruit", "Pumpkin",
    "Magnetshroom", "Cabbagepult", "Pot", "Cornpult", "Coffeebean", "Garlic",
    "Umbrellaleaf", "Marigold", "Melonpult",
    # 紫卡（升级植物）
    "GatlingPea", "TwinSunFlower", "GloomShroom", "Cattail", "WinterMelon",
    "GoldMagnet", "SpikeRock", "CobCannon",
    # 变体
    "PeaShooterSingle", "Imitater",
]
# 注意：原版素材中 Repeater（双发）无独立 reanim 文件，由 GatlingPea/PeaShooter 系复用。

# 文件名特殊映射（目录里的实际文件名）
SRC_NAMES = {
    "Repeater": "PeaShooterSingle",  # 无双发独立文件时的回退，见 main 中处理
}


def find_src(src_dir: str, name: str) -> Optional[str]:
    for cand in (name, SRC_NAMES.get(name, name)):
        path = os.path.join(src_dir, f"{cand}.reanim.compiled")
        if os.path.exists(path):
            return path
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src_dir", help="compiled reanim 目录（*.reanim.compiled）")
    ap.add_argument("out_dir", help="输出目录（相对 Godot 项目根，生成 res:// 引用）")
    ap.add_argument("--tex", nargs="+", required=True, help="贴图源目录，可多个")
    ap.add_argument("filters", nargs="*", help="植物名子串过滤（不填 = 全部）")
    args = ap.parse_args()

    filters = [a.lower() for a in args.filters]
    names = [n for n in PLANTS if not filters or any(f in n.lower() for f in filters)]
    ok, failed = [], []
    for name in names:
        src = find_src(args.src_dir, name)
        if not src:
            failed.append((name, "无 reanim 文件"))
            continue
        out = os.path.join(args.out_dir, f"{name.lower()}.tscn")
        try:
            convert(src, args.tex, out, "res://" + args.out_dir + "/textures")
            ok.append(name)
        except Exception as e:  # noqa: BLE001 批量任务收集全部错误
            failed.append((name, str(e)))
    print(f"\n完成 {len(ok)}/{len(names)}")
    for name, err in failed:
        print(f"  失败 {name}: {err}")


if __name__ == "__main__":
    main()
