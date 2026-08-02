#!/usr/bin/env python3
"""
pvz2godot.py — 将植物大战僵尸的 .reanim.compiled 动画转换为 Godot 4 场景 (.tscn)

原理参考:
  - 二进制解码: librePvZ/reanim-decode (Ruifeng Xie, Rust)
    https://github.com/librePvZ/librePvZ
  - Godot 动画转换: HYTommm/PVZ_reanim2godot_animation (C)
    https://github.com/HYTommm/PVZ_reanim2godot_animation

仅依赖 Python 标准库。

用法:
  python3 pvz2godot.py <输入文件或目录> --images <贴图目录> --out <输出目录>
      [--res-prefix res://pvz] [--no-loop]

输出:
  每个 reanim 生成一个 .tscn (Node2D + 各部件 Sprite2D + AnimationPlayer)
  使用到的贴图复制到 <out>/textures/ 并以 <res-prefix>/textures/ 引用
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import struct
import sys
import zlib
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 二进制解码 (对应 librePvZ reanim-decode 的 stream.rs / reanim.rs)
# ---------------------------------------------------------------------------

ZLIB_MAGIC = b"\xd4\xfe\xad\xde"
REANIM_MAGIC = 0xB393B4C0
HEADER_END_MAGIC = 0x0C
TRACK_MAGIC = 0x2C
ABSENT_SENTINEL = -10000.0  # f32 <= 此值表示字段缺失


@dataclass
class Frame:
    x: float | None = None
    y: float | None = None
    kx: float | None = None  # 旋转角(度)
    ky: float | None = None  # y轴角(度), skew = ky - kx
    sx: float | None = None
    sy: float | None = None
    f: float | None = None   # 可见性: >=0 显示, -1 隐藏
    a: float | None = None   # 透明度
    image: str | None = None
    font: str | None = None
    text: str | None = None


@dataclass
class Track:
    name: str
    frames: list[Frame]


@dataclass
class Reanim:
    fps: float
    tracks: list[Track]


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        chunk = self.data[self.pos:self.pos + n]
        if len(chunk) != n:
            raise ValueError(f"unexpected EOF at {self.pos}")
        self.pos += n
        return chunk

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def opt_f32(self) -> float | None:
        v = self.f32()
        return None if v <= ABSENT_SENTINEL else v

    def string(self) -> str:
        n = self.u32()
        return self.take(n).decode("utf-8", errors="replace") if n else ""

    def skip(self, n: int) -> None:
        self.take(n)

    def check_magic(self, expected: int) -> None:
        got = self.u32()
        if got != expected:
            raise ValueError(f"magic mismatch: expect {expected:#x}, got {got:#x} @ {self.pos - 8}")


def decode_compiled(data: bytes) -> Reanim:
    """解码 .reanim.compiled (支持可选的 zlib 外层)"""
    if data[:4] == ZLIB_MAGIC:
        data = zlib.decompress(data[8:])
    r = Reader(data)
    r.check_magic(REANIM_MAGIC)
    r.skip(4)  # padding after-magic
    track_count = r.u32()
    fps = r.f32()
    r.skip(4)  # padding "prop"
    r.check_magic(HEADER_END_MAGIC)
    frame_counts = []
    for _ in range(track_count):
        r.skip(8)  # padding "frame"
        frame_counts.append(r.u32())
    tracks = []
    for n in frame_counts:
        name = r.string()
        r.check_magic(TRACK_MAGIC)
        frames = []
        for _ in range(n):
            frames.append(Frame(
                x=r.opt_f32(), y=r.opt_f32(),
                kx=r.opt_f32(), ky=r.opt_f32(),
                sx=r.opt_f32(), sy=r.opt_f32(),
                f=r.opt_f32(), a=r.opt_f32(),
            ))
            r.skip(12)  # padding "transform"
        for fr in frames:
            image, font, text = r.string(), r.string(), r.string()
            fr.image = image or None
            fr.font = font or None
            fr.text = text or None
        tracks.append(Track(name=name, frames=frames))
    return Reanim(fps=fps, tracks=tracks)


# ---------------------------------------------------------------------------
# 语义累积 (对应 HYTommm 的 inherit 帧模式)
# ---------------------------------------------------------------------------

PI = math.pi


def wrap_near(value: float, reference: float) -> float:
    """把角度值回绕到 reference 的 ±π 区间内 (对应 C 代码的 SetKx/SetKy)"""
    while value - reference > PI:
        value -= 2 * PI
    while value - reference < -PI:
        value += 2 * PI
    return value


@dataclass
class PartState:
    """一个部件(轨道)随帧推进的累积状态"""
    x: float = 0.0
    y: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0   # 弧度
    skew: float = 0.0  # 弧度
    alpha: float = 1.0
    visible: bool = True
    image: str | None = None
    blend_add: bool = False

    def apply(self, fr: Frame) -> dict[str, bool]:
        """应用一帧, 返回各离散属性是否变化"""
        changed = {"visible": False, "image": False, "blend": False}
        if fr.x is not None:
            self.x = fr.x
        if fr.y is not None:
            self.y = fr.y
        if fr.sx is not None:
            self.sx = fr.sx
        if fr.sy is not None:
            self.sy = fr.sy
        if fr.kx is not None:
            new_rot = wrap_near(math.radians(fr.kx), self.rot)
            self.skew += self.rot - new_rot
            self.rot = new_rot
        if fr.ky is not None:
            self.skew = wrap_near(math.radians(fr.ky % 360.0) - self.rot, self.skew)
        if fr.a is not None:
            self.alpha = fr.a
        if fr.f is not None:
            new_vis = fr.f >= 0.0
            changed["visible"] = new_vis != self.visible
            self.visible = new_vis
        if fr.image is not None:
            changed["image"] = True
            self.image = fr.image
        if fr.text or fr.font:
            # 文字元素暂不支持, 忽略
            pass
        return changed


def image_to_filename(image: str) -> str:
    """IMAGE_REANIM_ZOMBIE_BODY -> Zombie_body.png (与 C 代码一致: 首字母保留, 其余小写)"""
    prefix = "IMAGE_REANIM_"
    if image.startswith(prefix) and len(image) > len(prefix):
        rest = image[len(prefix):]
        return rest[0] + rest[1:].lower() + ".png"
    return image


def sanitize_node_name(name: str, used: set[str]) -> str:
    """首字母大写, '.' 替换为 '_', 重名加数字后缀 (与 C 代码一致)"""
    if not name:
        name = "part"
    name = name[0].upper() + name[1:]
    name = name.replace(".", "_")
    candidate, i = name, 0
    while candidate in used:
        i += 1
        candidate = f"{name}{i}"
    used.add(candidate)
    return candidate


@dataclass
class Segment:
    name: str
    start: int
    end: int  # 含


def find_segments(track: Track) -> Segment | None:
    """anim_* 轨道是子动画段落标记: f==0 处开始, f==-1 前一帧结束"""
    if not track.name.startswith("anim_"):
        return None
    start, end = 0, len(track.frames) - 1
    start_found = False
    for i, fr in enumerate(track.frames):
        if fr.f is None:
            continue
        v = int(fr.f)
        if v == 0 and not start_found:
            start, start_found = i, True
        elif v == -1:
            end = i - 1
            break
    if end < start:
        end = len(track.frames) - 1
    return Segment(name=track.name[5:], start=start, end=end)


# ---------------------------------------------------------------------------
# Godot .tscn 生成
# ---------------------------------------------------------------------------

def fnum(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def vec2(x: float, y: float) -> str:
    return f"Vector2({fnum(x)}, {fnum(y)})"


@dataclass
class BuiltAnimation:
    name: str
    length: float
    # 每条 Godot 轨道: (path, interp, update, times, values已格式化为tscn文本)
    tracks: list[tuple[str, int, int, list[float], list[str]]] = field(default_factory=list)


def build_animation(
    name: str,
    parts: list[tuple[str, Track]],  # (节点名, 轨道)
    fps: float,
    start: int,
    end: int,
    texture_ids: dict[str, str],     # 贴图文件名 -> ext_resource id
    loop: bool,
) -> BuiltAnimation:
    anim = BuiltAnimation(name=name, length=(end - start + 1) / fps)
    for node, track in parts:
        state = PartState()
        # 先快进到段落起点, 得到初始状态
        for fr in track.frames[:start]:
            state.apply(fr)

        times: dict[str, list[float]] = {k: [] for k in
            ("pos", "rot", "scale", "skew", "alpha", "vis", "tex", "mat")}
        values: dict[str, list[str]] = {k: [] for k in times}

        def push_dense(t: float) -> None:
            times["pos"].append(t); values["pos"].append(vec2(state.x, state.y))
            times["rot"].append(t); values["rot"].append(fnum(state.rot))
            times["scale"].append(t); values["scale"].append(vec2(state.sx, state.sy))
            times["skew"].append(t); values["skew"].append(fnum(state.skew))
            times["alpha"].append(t)
            values["alpha"].append(f"Color(1, 1, 1, {fnum(state.alpha)})")

        has_tex_key = False
        has_mat_key = False
        for idx in range(start, min(end + 1, len(track.frames))):
            t = (idx - start) / fps
            fr = track.frames[idx]
            changed = state.apply(fr)
            push_dense(t)
            if fr.f is not None:
                times["vis"].append(t)
                values["vis"].append("true" if state.visible else "false")
            if changed["image"] and state.image:
                fn = image_to_filename(state.image)
                if fn in texture_ids:
                    has_tex_key = True
                    times["tex"].append(t)
                    values["tex"].append(f'ExtResource("{texture_ids[fn]}")')
        if times["vis"] and (end - start) / fps not in times["vis"]:
            pass  # 离散轨道无需收尾

        node_tracks: list[tuple[str, int, int, list[float], list[str]]] = [
            (f"{node}:position", 1, 0, times["pos"], values["pos"]),
            (f"{node}:rotation", 1, 0, times["rot"], values["rot"]),
            (f"{node}:scale", 1, 0, times["scale"], values["scale"]),
            (f"{node}:skew", 1, 0, times["skew"], values["skew"]),
            (f"{node}:self_modulate", 1, 0, times["alpha"], values["alpha"]),
        ]
        if times["vis"]:
            node_tracks.append((f"{node}:visible", 1, 1, times["vis"], values["vis"]))
        if has_tex_key:
            node_tracks.append((f"{node}:texture", 1, 1, times["tex"], values["tex"]))
        anim.tracks.extend(node_tracks)
    return anim


def emit_animation_subres(res_id: str, anim: BuiltAnimation, loop: bool) -> str:
    lines = [f'[sub_resource type="Animation" id="{res_id}"]']
    lines.append(f'resource_name = "{anim.name}"')
    lines.append(f"length = {fnum(anim.length)}")
    if loop:
        lines.append("loop_mode = 1")
    for i, (path, interp, update, times, vals) in enumerate(anim.tracks):
        lines.append(f'tracks/{i}/type = "value"')
        lines.append(f"tracks/{i}/imported = false")
        lines.append(f"tracks/{i}/enabled = true")
        lines.append(f'tracks/{i}/path = NodePath("{path}")')
        lines.append(f"tracks/{i}/interp = {interp}")
        lines.append(f"tracks/{i}/loop_wrap = true")
        times_s = ", ".join(fnum(t) for t in times)
        trans_s = ", ".join("1" for _ in times)
        vals_s = ", ".join(vals)
        lines.append(f"tracks/{i}/keys = {{")
        lines.append(f'"times": PackedFloat32Array({times_s}),')
        lines.append(f'"transitions": PackedFloat32Array({trans_s}),')
        lines.append(f'"update": {update},')
        lines.append(f'"values": [{vals_s}]')
        lines.append("}")
    return "\n".join(lines)


def convert(
    reanim: Reanim,
    scene_name: str,
    res_prefix: str,
    image_lookup: dict[str, str],  # 小写文件名 -> 实际文件名
    out_dir: str,
    tex_out_dir: str,
    images_dir: str,
    loop: bool,
) -> tuple[str, list[str]]:
    """返回 (tscn 文本, 警告列表)"""
    warnings: list[str] = []

    # 分离段落轨道与部件轨道
    segments: list[Segment] = []
    parts: list[tuple[str, Track]] = []
    used_names: set[str] = set()
    for tr in reanim.tracks:
        seg = find_segments(tr)
        if seg is not None:
            segments.append(seg)
        else:
            parts.append((sanitize_node_name(tr.name, used_names), tr))

    max_frames = max((len(tr.frames) for tr in reanim.tracks), default=1)

    # 收集用到的贴图, 建立 ext_resource
    tex_files: list[str] = []
    seen: set[str] = set()
    for _, tr in parts:
        for fr in tr.frames:
            if fr.image:
                fn = image_to_filename(fr.image)
                if fn not in seen:
                    seen.add(fn)
                    tex_files.append(fn)
    texture_ids = {fn: f"{i + 1}" for i, fn in enumerate(tex_files)}

    # 复制贴图 (大小写不敏感查找)
    os.makedirs(tex_out_dir, exist_ok=True)
    for fn in tex_files:
        hit = image_lookup.get(fn.lower())
        if hit is None:
            warnings.append(f"贴图缺失: {fn}")
            continue
        src_dir, actual = hit
        dst = os.path.join(tex_out_dir, actual)
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(src_dir, actual), dst)

    # 各部件首帧状态 (用于节点默认值, 让场景在编辑器里直接拼好)
    initial: dict[str, PartState] = {}
    for node, tr in parts:
        st = PartState()
        if tr.frames:
            st.apply(tr.frames[0])
        initial[node] = st

    # 构建动画: "all" + 各段落
    anims: list[BuiltAnimation] = [
        build_animation("all", parts, reanim.fps, 0, max_frames - 1, texture_ids, loop)
    ]
    for seg in segments:
        anims.append(build_animation(seg.name, parts, reanim.fps, seg.start, seg.end, texture_ids, loop))

    # ---- 生成 tscn ----
    ext_count = len(tex_files)
    sub_count = len(anims) + 1  # animations + 1 library
    load_steps = 1 + ext_count + sub_count

    out: list[str] = [f'[gd_scene load_steps={load_steps} format=3]', ""]
    for fn in tex_files:
        hit = image_lookup.get(fn.lower())
        actual = hit[1] if hit else fn
        out.append(f'[ext_resource type="Texture2D" path="{res_prefix}/textures/{actual}" id="{texture_ids[fn]}"]')
    out.append("")

    anim_ids = [f"Animation_{i}" for i in range(len(anims))]
    lib_id = "AnimationLibrary_0"
    for res_id, anim in zip(anim_ids, anims):
        out.append(emit_animation_subres(res_id, anim, loop))
        out.append("")

    lib_entries = ",\n".join(f'"{a.name}": SubResource("{rid}")' for a, rid in zip(anims, anim_ids))
    out.append(f'[sub_resource type="AnimationLibrary" id="{lib_id}"]')
    out.append("_data = {")
    out.append(lib_entries)
    out.append("}")
    out.append("")

    out.append(f'[node name="{scene_name}" type="Node2D"]')
    out.append("")
    for node, _tr in parts:
        st = initial[node]
        out.append(f'[node name="{node}" type="Sprite2D" parent="."]')
        if st.x or st.y:
            out.append(f"position = {vec2(st.x, st.y)}")
        if st.rot:
            out.append(f"rotation = {fnum(st.rot)}")
        if st.sx != 1.0 or st.sy != 1.0:
            out.append(f"scale = {vec2(st.sx, st.sy)}")
        if st.skew:
            out.append(f"skew = {fnum(st.skew)}")
        if not st.visible:
            out.append("visible = false")
        if st.image:
            fn = image_to_filename(st.image)
            if fn in texture_ids:
                out.append(f'texture = ExtResource("{texture_ids[fn]}")')
        out.append("")

    out.append('[node name="AnimationPlayer" type="AnimationPlayer" parent="."]')
    out.append("libraries = {")
    out.append(f'&"": SubResource("{lib_id}")')
    out.append("}")
    return "\n".join(out) + "\n", warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_image_lookup(images_dir: str) -> dict[str, str]:
    lookup = {}
    for fn in os.listdir(images_dir):
        if fn.lower().endswith((".png", ".jpg", ".gif")):
            lookup[fn.lower()] = (images_dir, fn)
    return lookup


def process_file(path: str, args, image_lookup: dict[str, str]) -> tuple[bool, list[str]]:
    name = os.path.basename(path)
    for suffix in (".reanim.compiled", ".xml.compiled", ".compiled"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    try:
        reanim = decode_compiled(open(path, "rb").read())
    except Exception as e:  # noqa: BLE001
        return False, [f"{os.path.basename(path)}: 解码失败: {e}"]
    tscn, warnings = convert(
        reanim, name, args.res_prefix, image_lookup,
        args.out, os.path.join(args.out, "textures"), args.images,
        loop=not args.no_loop,
    )
    out_path = os.path.join(args.out, f"{name}.tscn")
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(tscn)
    return True, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="PVZ reanim.compiled -> Godot 4 tscn 转换器")
    ap.add_argument("input", help=".reanim.compiled 文件或包含它们的目录")
    ap.add_argument("--images", required=True, nargs="+",
                    help="贴图 PNG 目录, 可多个 (如 pvz_assets/reanim pvz_assets/images)")
    ap.add_argument("--out", required=True, help="输出目录 (建议指向 Godot 项目内)")
    ap.add_argument("--res-prefix", default="res://pvz",
                    help="tscn 中引用贴图的 res:// 前缀 (默认 res://pvz)")
    ap.add_argument("--no-loop", action="store_true", help="动画不循环")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    image_lookup = {}
    for d in args.images:
        image_lookup.update(build_image_lookup(d))

    if os.path.isdir(args.input):
        files = [os.path.join(args.input, f) for f in sorted(os.listdir(args.input))
                 if f.endswith(".compiled")]
    else:
        files = [args.input]

    ok, fail = 0, 0
    all_warnings: list[str] = []
    for path in files:
        success, warnings = process_file(path, args, image_lookup)
        if success:
            ok += 1
        else:
            fail += 1
        all_warnings.extend(warnings)

    print(f"完成: {ok} 成功, {fail} 失败, 输出目录 {args.out}")
    if all_warnings:
        uniq = sorted(set(all_warnings))
        print(f"警告 {len(all_warnings)} 条 (去重 {len(uniq)}):")
        for w in uniq[:20]:
            print("  -", w)
        if len(uniq) > 20:
            print(f"  ... 其余 {len(uniq) - 20} 条省略")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
