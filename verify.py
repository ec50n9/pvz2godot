"""转换正确性校验：tscn 动画 vs 原版语义（逐帧填充 + 相邻帧插值 + loop 回绕）。

用法：
    python3 verify.py <file.reanim.compiled> <file.tscn>
    python3 verify.py --all <compiled目录> <tscn根目录>   # 递归校验全部已转换文件

连续通道在段内密集采样（含 loop 回绕区间），离散通道检查段首锚定与逐帧值。
退出码 0 = 全部通过。
"""
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reanim_parse import parse_reanim, split_labels
from convert import unique_part_names

TOL = 0.01
SAMPLES = 200
DEFAULTS = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 1.0, 5: 1.0, 7: 1.0}


def original_filled(track, ch):
    """原版加载语义：空帧用前一帧的值填充。"""
    filled, prev = [], DEFAULTS[ch]
    for f in track.transforms:
        v = f[ch]
        if v is not None:
            prev = v
        filled.append(prev)
    return filled


def sample_filled(filled, tf):
    i = min(int(tf), len(filled) - 1)
    j = min(i + 1, len(filled) - 1)
    return filled[i] + (filled[j] - filled[i]) * (tf - i)


def sample_filled_wrap(filled, tf, start, end):
    """原版 loop 边界语义（Reanimator.cpp GetFrameTime）：末帧 before=after 钳制保持，
    回绕瞬间瞬切回首帧，无跨边界插值。对应 Godot 侧 loop_wrap=false（末键保持）。"""
    if tf >= end - 1:
        return sample_filled(filled, end - 1)
    return sample_filled(filled, tf)


def lerp(a, b, f):
    if isinstance(a, tuple):
        return tuple(x + (y - x) * f for x, y in zip(a, b))
    return a + (b - a) * f


def godot_value(keys, t, length, loop):
    # loop_wrap=false：超过末键保持末键值（与原版末帧钳制一致），回绕瞬切
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else lerp(v0, v1, (t - t0) / (t1 - t0))
    return keys[-1][1]


def parse_tscn(path):
    """{anim_name: (length, loop, {(node, prop): (discrete, [(t, value)])})}"""
    text = open(path).read()
    anims = {}
    for b in text.split('[sub_resource type="Animation"')[1:]:
        name = re.search(r'resource_name = "([^"]+)"', b).group(1)
        length = float(re.search(r'length = ([\d.]+)', b).group(1))
        loop = 'loop_mode = 1' in b
        tracks = {}
        for m in re.finditer(
                r'tracks/\d+/path = NodePath\("([^:]+):(\w+)"\).*?"times": PackedFloat32Array\(([^)]*)\).*?"update": (\d+),\s*"values": \[([^\]]*)\]',
                b, re.S):
            node, prop, times, update, vals = m.groups()
            ts = [float(x) for x in times.split(",") if x.strip()]
            if update == "1":
                vs = []
                for tok in re.finditer(r'ExtResource\("([^"]+)"\)|true|false', vals):
                    vs.append(tok.group(1) if tok.group(1) else tok.group(0) == "true")
            else:
                vs = []
                for tok in re.finditer(
                        r'Vector2\((-?[\d.]+), (-?[\d.]+)\)|Color\(1, 1, 1, (-?[\d.]+)\)|(-?[\d.]+)', vals):
                    if tok.group(1) is not None:
                        vs.append((float(tok.group(1)), float(tok.group(2))))
                    elif tok.group(3) is not None:
                        vs.append(float(tok.group(3)))
                    else:
                        vs.append(float(tok.group(4)))
            tracks[(node, prop)] = (update == "1", list(zip(ts, vs)))
        anims[name] = (length, loop, tracks)
    return anims


def verify(src, tscn):
    fps, tracks = parse_reanim(src)
    labels, part_tracks = split_labels(tracks)
    parts = unique_part_names(part_tracks)
    if not labels and parts:
        labels = {"anim_idle": (0, max(p["track"].frame_count for p in parts))}
    by_name = {p["name"]: p["track"] for p in parts}
    filled = {}
    anims = parse_tscn(tscn)
    errors = []

    def ov(track, ch, tf):
        key = (id(track), ch)
        if key not in filled:
            filled[key] = original_filled(track, ch)
        return sample_filled(filled[key], tf)

    def ov_wrap(track, ch, tf, start, end):
        key = (id(track), ch)
        if key not in filled:
            filled[key] = original_filled(track, ch)
        return sample_filled_wrap(filled[key], tf, start, end)

    def expected(prop, track, tf_nowrap, tf_wrap, loop):
        """返回 (期望值, 比较函数)"""
        get = (lambda ch: ov_wrap(track, ch, tf_wrap[0], *tf_wrap[1]) if loop else ov(track, ch, tf_nowrap))
        if prop == "rotation":
            return math.radians(get(2)), lambda gv, ev: abs(gv - ev)
        if prop == "skew":
            return math.radians(get(3) - get(2)), lambda gv, ev: abs(gv - ev)
        if prop == "position":
            return (get(0), get(1)), lambda gv, ev: max(abs(gv[0] - ev[0]), abs(gv[1] - ev[1]))
        if prop == "scale":
            return (get(4), get(5)), lambda gv, ev: max(abs(gv[0] - ev[0]), abs(gv[1] - ev[1]))
        if prop == "self_modulate":
            return get(7), lambda gv, ev: abs(gv - ev)
        return None, None

    for label, (start, end) in labels.items():
        anim = label.removeprefix("anim_")
        if anim not in anims:
            errors.append(f"缺动画 {anim}")
            continue
        length, loop, gt = anims[anim]
        n = end - start
        for (node, prop), (discrete, keys) in gt.items():
            track = by_name.get(node)
            if track is None or not keys:
                continue
            if discrete:
                # 段首锚定
                if keys[0][0] > 1e-4:
                    errors.append(f"{anim}/{node}:{prop} 缺段首锚定键（首键 t={keys[0][0]}）")
                continue  # 离散值正确性由连续通道与可见区间覆盖
            for k in range(SAMPLES):
                i = (n - 0.01) * k / SAMPLES
                tf_nowrap = min(start + i, end - 1)
                tf_wrap = (start + i, (start, end))
                exp, cmpf = expected(prop, track, tf_nowrap, tf_wrap, loop)
                if exp is None:
                    continue
                gv = godot_value(keys, i / fps, length, loop)
                err = cmpf(gv, exp)
                if err > TOL:
                    errors.append(f"{anim}/{node}:{prop} 帧{i:.2f} 误差 {err:.4f}")
                    break
    return errors


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--all":
        if len(sys.argv) != 4:
            print(__doc__)
            sys.exit(2)
        src_dir, tscn_root = sys.argv[2], sys.argv[3]
        # 递归扫描 tscn 根目录，按小写文件名映射源 compiled
        compiled = {f[: -len(".reanim.compiled")].lower(): os.path.join(src_dir, f)
                    for f in os.listdir(src_dir) if f.endswith(".reanim.compiled")}
        cases = []
        for root, _, files in os.walk(tscn_root):
            for f in sorted(files):
                if f.endswith(".tscn"):
                    src = compiled.get(f[: -len(".tscn")])
                    if src:
                        cases.append((src, os.path.join(root, f)))
                    else:
                        print("[警告] %s: 找不到对应源文件" % f)
    elif len(sys.argv) == 3:
        cases = [(sys.argv[1], sys.argv[2])]
    else:
        print(__doc__)
        sys.exit(2)
    bad = 0
    for src, tscn in cases:
        errors = verify(src, tscn)
        name = os.path.basename(tscn)
        if errors:
            bad += 1
            print(f"FAIL {name}: {len(errors)} 个问题")
            for e in errors[:5]:
                print(f"  {e}")
        else:
            print(f"OK   {name}")
    print(f"\n{len(cases) - bad}/{len(cases)} 通过")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
