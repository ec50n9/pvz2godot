#!/usr/bin/env python3
"""
pvz2godot.py — 将植物大战僵尸的 .reanim.compiled 动画转换为 Godot 4 场景 (.tscn)

二进制格式与求值语义依据对 toolvs.com/pvz-reanim (浏览器端解析器) 的逆向:

  外层: i32 LE 魔数 -559022380 (0xDEADFED4, 字节 d4 fe ad de) + 4 字节,
        其后全部为 zlib (deflate) 压缩数据
  内层: 8 字节头, i32 轨道数, f32 fps, 4 字节填充, i32 标记 0xC,
        轨道表 轨道数×(8 字节填充 + i32 帧数),
        每轨道: 长度前缀 UTF-8 字符串名 + i32 标记 0x2C,
               帧数 × (7×f32 {x,y,kx,ky,sx,sy,f,a} + 12 字节填充),
               帧数 × 3 个长度前缀字符串 {image, font, text}
  缺失标记: 字段值 ≈ -10000 (|v + 10000| < 0.001) 表示该帧未定义此字段
  求值: 逐帧继承 —— 缺失字段沿用上一帧的值, 初始状态
        f=0, x=0, y=0, kx=0, ky=0, sx=1, sy=1, a=1, 无 image/font/text

Godot 映射 (与 PVZ 渲染矩阵精确等价):
  PVZ 每帧矩阵:   x轴基 = (sx·cos kx°, sx·sin kx°)
                  y轴基 = (−sy·sin ky°, sy·cos ky°), 平移 (x, y)
  Godot Node2D:   x轴基 = (cos r·sx, sin r·sx)
                  y轴基 = (−sin(r+sk)·sy, cos(r+sk)·sy), 平移 position
  对比可得: rotation = radians(kx), skew = radians(ky − kx),
            scale = (sx, sy), position = (x, y)
  rotation / skew 沿时间轴做 ±π 连续化 (unwrap), 保证 Godot 线性插值走最短弧。

仅依赖 Python 标准库。

用法:
  python3 pvz2godot.py <输入文件或目录> --images <贴图目录>... --out <输出目录>
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
# 二进制解码 (对齐 toolvs 参考实现)
# ---------------------------------------------------------------------------

ZLIB_HEADER_MAGIC = -559022380  # i32 LE, 即 0xDEADFED4
HEADER_END_MARKER = 0x0C
TRACK_MARKER = 0x2C
ABSENT_VALUE = -10000.0
ABSENT_EPS = 1e-3

NUMERIC_FIELDS = ("x", "y", "kx", "ky", "sx", "sy", "f", "a")


@dataclass
class Frame:
    x: float | None = None
    y: float | None = None
    kx: float | None = None
    ky: float | None = None
    sx: float | None = None
    sy: float | None = None
    f: float | None = None
    a: float | None = None
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
    """小端二进制读取器: i32/f32 + int32 长度前缀 UTF-8 字符串 (去尾部 NUL)"""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        chunk = self.data[self.pos:self.pos + n]
        if len(chunk) != n:
            raise ValueError(f"unexpected EOF at {self.pos}")
        self.pos += n
        return chunk

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def opt_f32(self) -> float | None:
        v = self.f32()
        # 参考实现: |v - (-10000)| < 0.001 视为缺失 (而非 v <= -10000,
        # 避免把合法的屏外大负坐标误判为缺失)
        return None if abs(v - ABSENT_VALUE) < ABSENT_EPS else v

    def string(self) -> str:
        n = self.i32()
        if n <= 0:
            return ""
        return self.take(n).decode("utf-8", errors="replace").rstrip("\x00")

    def skip(self, n: int) -> None:
        self.take(n)


def decode_compiled(data: bytes, warn=print) -> Reanim:
    """解码 .reanim.compiled (外层可选 zlib 包装)"""
    if len(data) < 8:
        raise ValueError("文件太小, 不是有效的 .reanim.compiled")
    if struct.unpack_from("<i", data, 0)[0] == ZLIB_HEADER_MAGIC:
        data = zlib.decompress(data[8:])

    r = Reader(data)
    r.skip(8)  # 内层 8 字节头 (含魔数, 参考实现直接跳过)
    track_count = r.i32()
    fps = r.f32()
    r.skip(4)
    marker = r.i32()
    if marker != HEADER_END_MARKER:
        warn(f"警告: 头部结束标记应为 {HEADER_END_MARKER:#x}, 实际 {marker:#x}, 继续尝试")

    frame_counts = []
    for _ in range(track_count):
        r.skip(8)
        frame_counts.append(r.i32())

    tracks = []
    for ti, count in enumerate(frame_counts):
        name = r.string()
        marker = r.i32()
        if marker != TRACK_MARKER:
            warn(f"警告: 轨道 {ti} 标记应为 {TRACK_MARKER:#x}, 实际 {marker:#x}, 继续尝试")
        frames = []
        for _ in range(count):
            values = [r.opt_f32() for _ in NUMERIC_FIELDS]
            r.skip(12)
            frames.append(Frame(**dict(zip(NUMERIC_FIELDS, values))))
        for fr in frames:
            image, font, text = r.string(), r.string(), r.string()
            fr.image = image or None
            fr.font = font or None
            fr.text = text or None
        tracks.append(Track(name=name, frames=frames))
    return Reanim(fps=fps, tracks=tracks)


# ---------------------------------------------------------------------------
# 逐帧求值 (对齐参考实现: 继承语义 + 固定初始状态)
# ---------------------------------------------------------------------------

@dataclass
class State:
    """一个部件在某一帧的完整求值结果"""
    x: float = 0.0
    y: float = 0.0
    kx: float = 0.0
    ky: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    f: float = 0.0
    a: float = 1.0
    image: str | None = None
    font: str | None = None
    text: str | None = None

    @property
    def visible(self) -> bool:
        return self.f >= 0.0


def resolve_states(track: Track) -> list[State]:
    """缺失字段继承上一帧 (与参考实现的 J/B 函数一致)"""
    states = []
    cur = State()
    for fr in track.frames:
        for key in NUMERIC_FIELDS:
            v = getattr(fr, key)
            if v is not None:
                setattr(cur, key, v)
        if fr.image is not None:
            cur.image = fr.image
        if fr.font is not None:
            cur.font = fr.font
        if fr.text is not None:
            cur.text = fr.text
        states.append(State(**vars(cur)))
    return states


# ---------------------------------------------------------------------------
# 角度处理与 Godot 属性序列
# ---------------------------------------------------------------------------

PI = math.pi
TAU = 2.0 * math.pi


def unwrap_angles(seq: list[float]) -> list[float]:
    """numpy 风格 unwrap: 相邻值差限制在 ±π, 保留长期旋转趋势"""
    if not seq:
        return []
    out = [seq[0]]
    for v in seq[1:]:
        prev = out[-1]
        out.append(prev + (v - prev + PI) % TAU - PI)
    return out


@dataclass
class PartTimeline:
    """一个部件整条时间轴上的 Godot 属性 (角度已 unwrap)"""
    name: str
    states: list[State]
    rotation: list[float]  # 弧度
    skew: list[float]      # 弧度


def build_part_timeline(name: str, track: Track) -> PartTimeline:
    states = resolve_states(track)
    rot = unwrap_angles([math.radians(s.kx) for s in states])
    skew = unwrap_angles([math.radians(s.ky - s.kx) for s in states])
    return PartTimeline(name=name, states=states, rotation=rot, skew=skew)


# ---------------------------------------------------------------------------
# 贴图名解析 (对齐参考实现的 IMAGE_* 规则)
# ---------------------------------------------------------------------------

def image_basename(image: str) -> str:
    """IMAGE_REANIM_ZOMBIE_BODY -> zombie_body.png (全小写, 与参考实现一致)"""
    name = image.strip()
    upper = name.upper()
    if upper.startswith("IMAGE_REANIM_"):
        name = name[len("IMAGE_REANIM_"):]
    elif upper.startswith("IMAGE_"):
        name = name[len("IMAGE_"):]
    return name.lower() + ".png"


def sanitize_node_name(name: str, used: set[str]) -> str:
    if not name:
        name = "part"
    name = name[0].upper() + name[1:].replace(".", "_")
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


def find_segment(track: Track) -> Segment | None:
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
    # (NodePath 属性, update 模式, times, 已格式化 values)
    tracks: list[tuple[str, int, list[float], list[str]]] = field(default_factory=list)


UPDATE_CONTINUOUS = 0
UPDATE_DISCRETE = 1


def build_animation(
    name: str,
    parts: list[PartTimeline],
    fps: float,
    start: int,
    end: int,
    texture_ids: dict[str, str],
) -> BuiltAnimation:
    anim = BuiltAnimation(name=name, length=(end - start + 1) / fps)
    for part in parts:
        node = part.name
        n = len(part.states)
        if n == 0:
            continue
        last = min(end, n - 1)
        if start > last:
            continue

        times: dict[str, list[float]] = {k: [] for k in ("pos", "rot", "scale", "skew", "alpha")}
        values: dict[str, list[str]] = {k: [] for k in times}
        vis_times: list[float] = []
        vis_values: list[str] = []
        tex_times: list[float] = []
        tex_values: list[str] = []
        prev_visible: bool | None = None
        prev_image: str | None = None

        for idx in range(start, last + 1):
            t = (idx - start) / fps
            s = part.states[idx]
            times["pos"].append(t); values["pos"].append(vec2(s.x, s.y))
            times["rot"].append(t); values["rot"].append(fnum(part.rotation[idx]))
            times["scale"].append(t); values["scale"].append(vec2(s.sx, s.sy))
            times["skew"].append(t); values["skew"].append(fnum(part.skew[idx]))
            times["alpha"].append(t); values["alpha"].append(f"Color(1, 1, 1, {fnum(s.a)})")
            if s.visible != prev_visible:
                prev_visible = s.visible
                vis_times.append(t)
                vis_values.append("true" if s.visible else "false")
            if s.image != prev_image:
                prev_image = s.image
                fn = image_basename(s.image) if s.image else None
                if fn and fn in texture_ids:
                    tex_times.append(t)
                    tex_values.append(f'ExtResource("{texture_ids[fn]}")')

        anim.tracks.extend([
            (f"{node}:position", UPDATE_CONTINUOUS, times["pos"], values["pos"]),
            (f"{node}:rotation", UPDATE_CONTINUOUS, times["rot"], values["rot"]),
            (f"{node}:scale", UPDATE_CONTINUOUS, times["scale"], values["scale"]),
            (f"{node}:skew", UPDATE_CONTINUOUS, times["skew"], values["skew"]),
            (f"{node}:self_modulate", UPDATE_CONTINUOUS, times["alpha"], values["alpha"]),
        ])
        if vis_times:
            anim.tracks.append((f"{node}:visible", UPDATE_DISCRETE, vis_times, vis_values))
        if tex_times:
            anim.tracks.append((f"{node}:texture", UPDATE_DISCRETE, tex_times, tex_values))
    return anim


def emit_animation_subres(res_id: str, anim: BuiltAnimation, loop: bool) -> str:
    lines = [f'[sub_resource type="Animation" id="{res_id}"]']
    lines.append(f'resource_name = "{anim.name}"')
    lines.append(f"length = {fnum(anim.length)}")
    if loop:
        lines.append("loop_mode = 1")
    for i, (path, update, times, vals) in enumerate(anim.tracks):
        lines.append(f'tracks/{i}/type = "value"')
        lines.append(f"tracks/{i}/imported = false")
        lines.append(f"tracks/{i}/enabled = true")
        lines.append(f'tracks/{i}/path = NodePath("{path}")')
        lines.append(f"tracks/{i}/interp = 1")
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
    image_lookup: dict[str, tuple[str, str]],
    tex_out_dir: str,
    loop: bool,
) -> tuple[str, list[str]]:
    """返回 (tscn 文本, 警告列表)"""
    warnings: list[str] = []

    # 分离段落轨道与部件轨道
    segments: list[Segment] = []
    part_tracks: list[Track] = []
    used_names: set[str] = set()
    for tr in reanim.tracks:
        seg = find_segment(tr)
        if seg is not None:
            segments.append(seg)
        else:
            part_tracks.append(tr)

    parts = [build_part_timeline(sanitize_node_name(tr.name, used_names), tr)
             for tr in part_tracks]
    max_frames = max((len(tr.frames) for tr in part_tracks), default=1)

    # 收集用到的贴图 (按求值后的 image 序列)
    tex_files: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for s in part.states:
            if s.image:
                fn = image_basename(s.image)
                if fn not in seen:
                    seen.add(fn)
                    tex_files.append(fn)
    texture_ids = {fn: str(i + 1) for i, fn in enumerate(tex_files)}

    # 复制贴图
    os.makedirs(tex_out_dir, exist_ok=True)
    for fn in tex_files:
        hit = image_lookup.get(fn)
        if hit is None:
            warnings.append(f"贴图缺失: {fn}")
            continue
        src_dir, actual = hit
        dst = os.path.join(tex_out_dir, actual)
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(src_dir, actual), dst)

    if any(s.text for part in parts for s in part.states):
        warnings.append("包含 text 轨道, 文字元素暂不支持, 已忽略")

    # 构建动画: "all" + 各段落
    anims = [build_animation("all", parts, reanim.fps, 0, max_frames - 1, texture_ids)]
    for seg in segments:
        anims.append(build_animation(seg.name, parts, reanim.fps, seg.start, seg.end, texture_ids))

    # ---- 生成 tscn ----
    ext_count = len(tex_files)
    sub_count = len(anims) + 1  # animations + 1 library
    load_steps = 1 + ext_count + sub_count

    out: list[str] = [f"[gd_scene load_steps={load_steps} format=3]", ""]
    for fn in tex_files:
        hit = image_lookup.get(fn)
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
    for part in parts:
        if not part.states:
            continue
        s = part.states[0]
        out.append(f'[node name="{part.name}" type="Sprite2D" parent="."]')
        if s.x or s.y:
            out.append(f"position = {vec2(s.x, s.y)}")
        if part.rotation[0]:
            out.append(f"rotation = {fnum(part.rotation[0])}")
        if s.sx != 1.0 or s.sy != 1.0:
            out.append(f"scale = {vec2(s.sx, s.sy)}")
        if part.skew[0]:
            out.append(f"skew = {fnum(part.skew[0])}")
        if s.a != 1.0:
            out.append(f"self_modulate = Color(1, 1, 1, {fnum(s.a)})")
        if not s.visible:
            out.append("visible = false")
        if s.image:
            fn = image_basename(s.image)
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

def build_image_lookup(images_dirs: list[str]) -> dict[str, tuple[str, str]]:
    """递归遍历贴图目录: 小写 basename -> (所在目录, 实际文件名)"""
    lookup: dict[str, tuple[str, str]] = {}
    for root_dir in images_dirs:
        for dirpath, _dirs, files in os.walk(root_dir):
            for fn in files:
                if fn.lower().endswith((".png", ".jpg", ".gif", ".tga")):
                    lookup.setdefault(fn.lower(), (dirpath, fn))
    return lookup


def process_file(path: str, args, image_lookup: dict[str, tuple[str, str]]) -> tuple[bool, list[str]]:
    name = os.path.basename(path)
    for suffix in (".reanim.compiled", ".xml.compiled", ".compiled"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    warnings: list[str] = []
    try:
        data = open(path, "rb").read()
        reanim = decode_compiled(data, warn=lambda m: warnings.append(f"{name}: {m}"))
    except Exception as e:  # noqa: BLE001
        return False, [f"{os.path.basename(path)}: 解码失败: {e}"]
    tscn, conv_warnings = convert(
        reanim, name, args.res_prefix, image_lookup,
        os.path.join(args.out, "textures"),
        loop=not args.no_loop,
    )
    warnings.extend(conv_warnings)
    out_path = os.path.join(args.out, f"{name}.tscn")
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(tscn)
    return True, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="PVZ reanim.compiled -> Godot 4 tscn 转换器")
    ap.add_argument("input", help=".reanim.compiled 文件或包含它们的目录")
    ap.add_argument("--images", required=True, nargs="+",
                    help="贴图目录, 可多个, 递归查找 (如 pvz_assets/reanim pvz_assets/images)")
    ap.add_argument("--out", required=True, help="输出目录 (建议指向 Godot 项目内)")
    ap.add_argument("--res-prefix", default="res://pvz",
                    help="tscn 中引用贴图的 res:// 前缀 (默认 res://pvz)")
    ap.add_argument("--no-loop", action="store_true", help="动画不循环")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    image_lookup = build_image_lookup(args.images)

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
