#!/usr/bin/env python3
"""
particles2godot.py — 将植物大战僵尸的粒子特效 (.xml.compiled) 转换为 Godot 4 粒子场景 (.tscn)

原理参考:
  - 二进制解码: YingFengTingYu/PopStudio (C#, Particles/PC.cs)
    https://github.com/YingFengTingYu/PopStudio
  - 字段语义/默认值: InLiothixi/stabledecompile (PvZ 反编译, TodParticle)
    https://github.com/InLiothixi/stabledecompile

每个 .xml.compiled 生成一个 .tscn: Node2D 根节点 + 每个发射器一个
GPUParticles2D (ParticleProcessMaterial), 同时可选输出无损 JSON 供手动调整。

用法:
  python3 particles2godot.py <输入文件或目录> --images <贴图目录> [更多目录...]
      --out <输出目录> [--res-prefix res://pvz] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import sys
import zlib

# ---------------------------------------------------------------------------
# 二进制解码 (对应 PopStudio 的 Particles/PC.cs Decode)
# ---------------------------------------------------------------------------

ZLIB_MAGIC = -559022380          # 0xDEADFED4 (bytes: D4 FE AD DE)
INNER_MAGIC = 1092589901         # 0x411F994D
TRAIL_MAGIC = -1416928589        # 0xAB8B62B3 (trail.compiled)
EMITTER_HEADER_ID = 0x164
FIELD_ID = 0x14

# ParticleFlags (TodParticle.h)
FLAG_RANDOM_LAUNCH_SPIN = 0x1
FLAG_ALIGN_LAUNCH_SPIN = 0x2
FLAG_SYSTEM_LOOPS = 0x8
FLAG_PARTICLE_LOOPS = 0x10
FLAG_DONT_FOLLOW = 0x20
FLAG_ADDITIVE = 0x100

# EmitterType (ConstEnums.h)
EMITTER_CIRCLE = 0
EMITTER_BOX = 1
EMITTER_BOX_PATH = 2
EMITTER_CIRCLE_PATH = 3
EMITTER_CIRCLE_EVEN_SPACING = 4

# ParticleFieldType (TodParticle.h)
FIELD_FRICTION = 1
FIELD_ACCELERATION = 2

# 轨道默认值 (TodParticleLoadADef)
TRACK_DEFAULTS = {
    "system_duration": 0.0, "cross_fade_duration": 0.0, "spawn_rate": 0.0,
    "spawn_min_active": -1.0, "spawn_max_active": -1.0, "spawn_max_launched": -1.0,
    "emitter_radius": 0.0, "emitter_offset_x": 0.0, "emitter_offset_y": 0.0,
    "emitter_box_x": 0.0, "emitter_box_y": 0.0, "emitter_path": 0.0,
    "emitter_skew_x": 0.0, "emitter_skew_y": 0.0,
    "particle_duration": 100.0,   # 单位: 1/100 秒
    "launch_speed": 0.0, "launch_angle": 0.0,
    "system_red": 1.0, "system_green": 1.0, "system_blue": 1.0,
    "system_alpha": 1.0, "system_brightness": 1.0,
    "particle_red": 1.0, "particle_green": 1.0, "particle_blue": 1.0,
    "particle_alpha": 1.0, "particle_brightness": 1.0,
    "particle_spin_angle": 0.0, "particle_spin_speed": 0.0,
    "particle_scale": 1.0, "particle_stretch": 1.0,
    "collision_reflect": 0.0, "collision_spin": 0.0,
    "clip_top": 0.0, "clip_bottom": 0.0, "clip_left": 0.0, "clip_right": 0.0,
    "animation_rate": 0.0,
}

TRACK_ORDER = [
    "system_duration", "cross_fade_duration", "spawn_rate", "spawn_min_active",
    "spawn_max_active", "spawn_max_launched", "emitter_radius", "emitter_offset_x",
    "emitter_offset_y", "emitter_box_x", "emitter_box_y", "emitter_path",
    "emitter_skew_x", "emitter_skew_y", "particle_duration",
    "system_red", "system_green", "system_blue", "system_alpha", "system_brightness",
    "launch_speed", "launch_angle",
    # 之后是 Field / SystemField, 再之后:
    "particle_red", "particle_green", "particle_blue", "particle_alpha",
    "particle_brightness", "particle_spin_angle", "particle_spin_speed",
    "particle_scale", "particle_stretch", "collision_reflect", "collision_spin",
    "clip_top", "clip_bottom", "clip_left", "clip_right", "animation_rate",
]
FIELD_SPLIT = TRACK_ORDER.index("particle_red")  # Field 块插入位置


class Reader:
    def __init__(self, data: bytes):
        self.d, self.p = data, 0

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def skip(self, n: int) -> None:
        self.p += n

    def string(self) -> str | None:
        n = self.i32()
        if n <= 0:
            return None
        v = self.d[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return v

    def expect(self, v: int) -> None:
        got = self.i32()
        if got != v:
            raise ValueError(f"id mismatch: {got:#x} != {v:#x} @ {self.p - 8}")


def read_track(r: Reader) -> list[dict] | None:
    n = r.i32()
    if n == 0:
        return None
    nodes = []
    for _ in range(n):
        nodes.append({
            "time": r.f32(), "low": r.f32(), "high": r.f32(),
            "curve": r.i32(), "distribution": r.i32(),
        })
    return nodes


def read_fields(r: Reader, count: int) -> list[dict]:
    types = []
    for _ in range(count):
        types.append(r.i32())
        r.skip(16)
    return [{"type": t, "x": read_track(r), "y": read_track(r)} for t in types]


def decode_trail(data: bytes) -> dict:
    """Trail (拖尾) 格式, 对应 PopStudio 的 Trail/PC.cs"""
    r = Reader(data)
    r.skip(8)
    trail = {
        "_type": "trail",
        "max_points": r.i32(),
        "min_point_distance": r.f32(),
        "flags": r.i32(),
    }
    r.skip(5 * 8)
    trail["image"] = r.string()
    for k in ("width_over_length", "width_over_time",
              "alpha_over_length", "alpha_over_time", "trail_duration"):
        trail[k] = read_track(r)
    return trail


def decode_particles(data: bytes) -> list[dict] | dict:
    if struct.unpack("<i", data[:4])[0] == ZLIB_MAGIC:
        data = zlib.decompress(data[8:])
    inner = struct.unpack("<i", data[:4])[0]
    if inner == TRAIL_MAGIC:
        return decode_trail(data)
    if inner != INNER_MAGIC:
        raise ValueError("不是有效的粒子 compiled 文件")
    r = Reader(data)
    r.skip(8)
    count = r.i32()
    r.expect(EMITTER_HEADER_ID)
    headers = []
    for _ in range(count):
        r.skip(4)
        h = {
            "image_col": r.i32(), "image_row": r.i32(),
            "image_frames": r.i32(), "animated": r.i32(),
            "flags": r.i32(), "emitter_type": r.i32(),
        }
        r.skip(8)
        r.skip(22 * 8)
        r.skip(4)
        h["field_count"] = r.i32()
        r.skip(4)
        h["system_field_count"] = r.i32()
        r.skip(16 * 8)
        headers.append(h)

    emitters = []
    for h in headers:
        e = dict(h)
        e["image"] = r.string()
        e["name"] = r.string() or "emitter"
        e["tracks"] = {}
        e["on_duration"] = None
        pre, post = TRACK_ORDER[:FIELD_SPLIT], TRACK_ORDER[FIELD_SPLIT:]
        for k in pre:
            e["tracks"][k] = read_track(r)
            if k == "system_duration":
                e["on_duration"] = r.string()  # OnDuration 在 SystemDuration 之后
        r.expect(FIELD_ID)
        e["fields"] = read_fields(r, h["field_count"])
        r.expect(FIELD_ID)
        e["system_fields"] = read_fields(r, h["system_field_count"])
        for k in post:
            e["tracks"][k] = read_track(r)
        emitters.append(e)
    return emitters


# ---------------------------------------------------------------------------
# 轨道求值
# ---------------------------------------------------------------------------

def node_value(node: dict) -> float:
    return (node["low"] + node["high"]) / 2.0


def track_nodes(e: dict, name: str) -> list[dict]:
    t = e["tracks"].get(name)
    if t:
        return t
    return [{"time": 0.0, "low": TRACK_DEFAULTS[name], "high": TRACK_DEFAULTS[name],
             "curve": 1, "distribution": 1}]


def t0(e: dict, name: str) -> tuple[float, float]:
    """轨道首节点的 (low, high)"""
    n = track_nodes(e, name)[0]
    return n["low"], n["high"]


def avg0(e: dict, name: str) -> float:
    lo, hi = t0(e, name)
    return (lo + hi) / 2.0


def image_to_filename(image: str) -> str:
    """IMAGE_AWARDRAYS2 -> AwardRays2.png (大小写交给目录查找)"""
    name = image[len("IMAGE_"):] if image.startswith("IMAGE_") else image
    return name + ".png"


def find_texture(image: str | None, image_lookup: dict[str, tuple[str, str]]) \
        -> tuple[str, str] | None:
    """按 IMAGE_ 名称查找贴图, 依次尝试 png/jpg/gif"""
    if not image:
        return None
    base = image_to_filename(image)[:-4]
    candidates = [base]
    if base.startswith("REANIM_"):
        # IMAGE_REANIM_XXX -> reanim 目录命名规则 (与 pvz2godot.py 一致)
        rest = base[len("REANIM_"):]
        candidates.append(rest[0] + rest[1:].lower())
    for b in candidates:
        for ext in (".png", ".jpg", ".gif"):
            hit = image_lookup.get((b + ext).lower())
            if hit:
                return hit
    return None


# ---------------------------------------------------------------------------
# Godot .tscn 生成 (GPUParticles2D + ParticleProcessMaterial)
# ---------------------------------------------------------------------------

def fnum(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return s if s else "0"


def sanitize(name: str, used: set[str]) -> str:
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    if not out or out[0].isdigit():
        out = "E" + out
    cand, i = out, 0
    while cand in used:
        i += 1
        cand = f"{out}{i}"
    used.add(cand)
    return cand


def color(e: dict, base: str, t: float = 0.0) -> tuple[float, float, float, float]:
    """particle 颜色轨道 × system 颜色, 取离 t 最近的节点"""
    def pick(track: str) -> float:
        nodes = track_nodes(e, track)
        node = min(nodes, key=lambda n: abs(n["time"] - t))
        return node_value(node)
    r = pick(f"particle_{base}") * avg0(e, f"system_{base}") if base != "brightness" else 1.0
    return r


def build_emitter_tscn(
    e: dict,
    node_name: str,
    texture_id: str | None,
    sub_ids: dict[str, str],   # 预分配的 sub_resource id
    warnings: list[str],
) -> tuple[list[str], dict[str, str]]:
    """返回 (节点文本行, 需要生成的 sub_resource 文本 {id: 内容})"""
    lines: list[str] = []
    subs: dict[str, str] = {}
    flags = e["flags"]

    # ---- ParticleProcessMaterial ----
    pm: list[str] = []
    dur_lo, dur_hi = t0(e, "particle_duration")
    dur_s = (dur_lo + dur_hi) / 2.0 * 0.01
    if dur_s <= 0:
        dur_s = 1.0

    # 发射形状
    etype = e["emitter_type"]
    radius = avg0(e, "emitter_radius")
    if etype == EMITTER_CIRCLE and radius > 0:
        pm.append("emission_shape = 1")
        pm.append(f"emission_sphere_radius = {fnum(radius)}")
    elif etype in (EMITTER_CIRCLE_PATH, EMITTER_CIRCLE_EVEN_SPACING) and radius > 0:
        pm.append("emission_shape = 6")
        pm.append(f"emission_ring_radius = {fnum(radius)}")
        pm.append(f"emission_ring_inner_radius = {fnum(radius)}")
    elif etype in (EMITTER_BOX, EMITTER_BOX_PATH):
        bx, by = avg0(e, "emitter_box_x"), avg0(e, "emitter_box_y")
        if bx > 0 or by > 0:
            pm.append("emission_shape = 3")
            pm.append(f"emission_box_extents = Vector3({fnum(bx)}, {fnum(by)}, 1)")

    # 发射方向与速度
    ang_lo, ang_hi = t0(e, "launch_angle")
    if ang_hi - ang_lo >= math.tau - 0.01:
        pm.append("spread = 180")
    else:
        mid = (ang_lo + ang_hi) / 2.0
        pm.append(f"direction = Vector3({fnum(math.cos(mid))}, {fnum(math.sin(mid))}, 0)")
        pm.append(f"spread = {fnum(min(180.0, math.degrees((ang_hi - ang_lo) / 2.0)))}")
    spd_lo, spd_hi = t0(e, "launch_speed")
    pm.append(f"initial_velocity_min = {fnum(spd_lo)}")
    pm.append(f"initial_velocity_max = {fnum(max(spd_lo, spd_hi))}")

    # 场: 加速度 -> 重力, 摩擦 -> 阻尼
    for fld in e["fields"] + e["system_fields"]:
        if fld["type"] == FIELD_ACCELERATION and fld["x"] and fld["y"]:
            gx = node_value(fld["x"][0]) * 100.0  # 1/100s² -> px/s²
            gy = node_value(fld["y"][0]) * 100.0
            pm.append(f"gravity = Vector3({fnum(gx)}, {fnum(gy)}, 0)")
        elif fld["type"] == FIELD_FRICTION and fld["x"]:
            damp = node_value(fld["x"][0]) * 100.0
            pm.append(f"damping_min = {fnum(damp)}")
            pm.append(f"damping_max = {fnum(damp)}")

    # 旋转
    if flags & FLAG_RANDOM_LAUNCH_SPIN:
        pm.append("initial_rotation_min = -180")
        pm.append("initial_rotation_max = 180")
    else:
        sa_lo, sa_hi = t0(e, "particle_spin_angle")
        pm.append(f"initial_rotation_min = {fnum(math.degrees(sa_lo))}")
        pm.append(f"initial_rotation_max = {fnum(math.degrees(sa_hi))}")
    ss_lo, ss_hi = t0(e, "particle_spin_speed")
    pm.append(f"angular_velocity_min = {fnum(math.degrees(ss_lo))}")
    pm.append(f"angular_velocity_max = {fnum(math.degrees(ss_hi))}")
    if flags & FLAG_ALIGN_LAUNCH_SPIN:
        pm.append("particle_flag_align_y = true")

    # 颜色渐变 (particle 生命周期内)
    ramp_id = sub_ids.get("ramp")
    if ramp_id:
        pm.append(f'color_ramp = SubResource("{ramp_id}")')
        grad_points = []
        nodes = sorted(track_nodes(e, "particle_alpha"), key=lambda n: n["time"])
        times = sorted({max(0.0, min(1.0, n["time"])) for n in nodes} | {0.0, 1.0})
        offsets, colors = [], []
        for t in times:
            def ch(base: str) -> float:
                ns = track_nodes(e, f"particle_{base}")
                n = min(ns, key=lambda x: abs(x["time"] - t))
                sys_v = avg0(e, f"system_{base}")
                return node_value(n) * sys_v
            offsets.append(t)
            colors.append((ch("red"), ch("green"), ch("blue"), ch("alpha")))
        grad = ['[sub_resource type="Gradient" id="Gradient_1"]']
        grad.append("offsets = PackedFloat32Array(" + ", ".join(fnum(o) for o in offsets) + ")")
        grad.append("colors = PackedColorArray(" + ", ".join(
            fnum(c) for rgba in colors for c in rgba) + ")")
        subs["Gradient_1"] = "\n".join(grad)
        subs[ramp_id] = ('[sub_resource type="GradientTexture1D" id="%s"]\n'
                         'gradient = SubResource("Gradient_1")') % ramp_id

    # 缩放曲线 (particle 生命周期内)
    curve_id = sub_ids.get("scale_curve")
    if curve_id:
        pm.append(f'scale_amount_curve = SubResource("{curve_id}")')
        nodes = sorted(track_nodes(e, "particle_scale"), key=lambda n: n["time"])
        pts = ", ".join(f"Vector2({fnum(max(0.0, min(1.0, n['time'])))}, {fnum(node_value(n))}), 0.0, 0.0, 0, 0"
                        for n in nodes)
        subs["Curve_1"] = ('[sub_resource type="Curve" id="Curve_1"]\n'
                           f"_data = [{pts}]")
        subs[curve_id] = ('[sub_resource type="CurveTexture" id="%s"]\n'
                          'curve = SubResource("Curve_1")') % curve_id

    pm_id = sub_ids["process_material"]
    subs[pm_id] = (f'[sub_resource type="ParticleProcessMaterial" id="{pm_id}"]\n'
                   + "\n".join(pm))

    # ---- GPUParticles2D 节点 ----
    max_active = avg0(e, "spawn_max_active")
    rate = avg0(e, "spawn_rate")
    if max_active > 0:
        amount = int(round(max_active))
    else:
        amount = int(math.ceil(rate * dur_s)) if rate > 0 else 8
    max_launched = avg0(e, "spawn_max_launched")
    if max_launched > 0:
        amount = min(amount, int(round(max_launched)))
    amount = max(1, min(amount, 512))

    sys_dur = avg0(e, "system_duration") * 0.01
    one_shot = sys_dur > 0 and not (flags & FLAG_SYSTEM_LOOPS)

    lines.append(f'[node name="{node_name}" type="GPUParticles2D" parent="."]')
    ox, oy = avg0(e, "emitter_offset_x"), avg0(e, "emitter_offset_y")
    if ox or oy:
        lines.append(f"position = Vector2({fnum(ox)}, {fnum(oy)})")
    lines.append(f"amount = {amount}")
    lines.append(f"lifetime = {fnum(dur_s)}")
    if dur_lo != dur_hi and dur_s > 0:
        lines.append(f"lifetime_randomness = {fnum(min(1.0, (dur_hi - dur_lo) / 2 * 0.01 / dur_s))}")
    if one_shot:
        lines.append("one_shot = true")
    if flags & FLAG_DONT_FOLLOW:
        lines.append("local_coords = false")
    lines.append(f'process_material = SubResource("{pm_id}")')
    if texture_id:
        lines.append(f'texture = ExtResource("{texture_id}")')
    mat_id = sub_ids.get("canvas_material")
    if mat_id:
        lines.append(f'material = SubResource("{mat_id}")')
        cm = [f'[sub_resource type="CanvasItemMaterial" id="{mat_id}"]']
        if flags & FLAG_ADDITIVE:
            cm.append("blend_mode = 1")
        col, row = e["image_col"], e["image_row"]
        if col > 0 and row > 0 and col * row > 1:
            cm.append("particles_animation = true")
            cm.append(f"particles_anim_h_frames = {col}")
            cm.append(f"particles_anim_v_frames = {row}")
            cm.append(f"particles_anim_loop = {'true' if flags & FLAG_PARTICLE_LOOPS else 'false'}")
        subs[mat_id] = "\n".join(cm)

    ignored = []
    if avg0(e, "particle_stretch") != 1.0:
        ignored.append("stretch")
    if any(avg0(e, f"clip_{s}") != 0.0 for s in ("top", "bottom", "left", "right")):
        ignored.append("clip")
    if e["tracks"].get("emitter_path"):
        ignored.append("emitter_path")
    if ignored:
        warnings.append(f"{node_name}: 未映射属性 {','.join(ignored)}")
    return lines, subs


def convert_trail(
    trail: dict,
    scene_name: str,
    res_prefix: str,
    image_lookup: dict[str, tuple[str, str]],
    tex_out_dir: str,
) -> tuple[str, list[str]]:
    """Trail -> Line2D 模板场景 (纹理平铺 + 宽度曲线 + 透明度渐变)"""
    warnings: list[str] = []
    ext_lines: list[str] = []
    tex_ref = None
    hit = find_texture(trail["image"], image_lookup)
    if trail["image"] and hit is None:
        warnings.append(f"贴图缺失: {image_to_filename(trail['image'])}")
    if hit:
        os.makedirs(tex_out_dir, exist_ok=True)
        dst = os.path.join(tex_out_dir, hit[1])
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(hit[0], hit[1]), dst)
        ext_lines.append(f'[ext_resource type="Texture2D" path="{res_prefix}/textures/{hit[1]}" id="1"]')
        tex_ref = 'ExtResource("1")'

    subs: list[str] = []
    lines = [f'[node name="{scene_name}" type="Line2D"]']
    lines.append("points = PackedVector2Array(0, 0, 100, 0)  # 占位, 运行时替换")
    wol = trail.get("width_over_length")
    if wol:
        w = max(node_value(n) for n in wol)
        lines.append(f"width = {fnum(w)}")
        pts = ", ".join(f"Vector2({fnum(max(0.0, min(1.0, n['time'])))}, {fnum(node_value(n) / w if w else 0)}), 0.0, 0.0, 0, 0"
                        for n in sorted(wol, key=lambda n: n["time"]))
        subs.append('[sub_resource type="Curve" id="Curve_1"]\n_data = [' + pts + ']')
        lines.append('width_curve = SubResource("Curve_1")')
    aol = trail.get("alpha_over_length")
    if aol:
        nodes = sorted(aol, key=lambda n: n["time"])
        offsets = ", ".join(fnum(max(0.0, min(1.0, n["time"]))) for n in nodes)
        colors = ", ".join(fnum(c) for n in nodes for c in (1.0, 1.0, 1.0, node_value(n)))
        subs.append('[sub_resource type="Gradient" id="Gradient_1"]\n'
                    f"offsets = PackedFloat32Array({offsets})\n"
                    f"colors = PackedColorArray({colors})")
        lines.append('gradient = SubResource("Gradient_1")')
    if tex_ref:
        lines.append(f"texture = {tex_ref}")
        lines.append("texture_mode = 2  # tile")
    lines.append("joint_mode = 2\nend_cap_mode = 2\nbegin_cap_mode = 2")

    load_steps = 1 + len(ext_lines) + len(subs)
    out = [f"[gd_scene load_steps={load_steps} format=3]", ""] + ext_lines + [""] + subs + [""] + lines
    return "\n".join(out) + "\n", warnings


def convert(
    emitters: list[dict],
    scene_name: str,
    res_prefix: str,
    image_lookup: dict[str, tuple[str, str]],
    tex_out_dir: str,
) -> tuple[str, list[str]]:
    warnings: list[str] = []

    # 收集贴图 (key = image 原始名, value = ext id; 实际文件名经 find_texture 解析)
    texture_ids: dict[str, str] = {}
    for e in emitters:
        if e["image"] and e["image"] not in texture_ids:
            texture_ids[e["image"]] = str(len(texture_ids) + 1)
    actual_names: dict[str, str] = {}
    os.makedirs(tex_out_dir, exist_ok=True)
    for image in texture_ids:
        hit = find_texture(image, image_lookup)
        if hit is None:
            warnings.append(f"贴图缺失: {image_to_filename(image)}")
            actual_names[image] = image_to_filename(image)
            continue
        src_dir, actual = hit
        actual_names[image] = actual
        dst = os.path.join(tex_out_dir, actual)
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(src_dir, actual), dst)

    # 预分配 sub_resource id
    sections: list[tuple[str, str]] = []  # (id, 内容) 按出现顺序
    node_blocks: list[str] = []
    used: set[str] = set()
    for idx, e in enumerate(emitters):
        node_name = sanitize(e["name"], used)
        has_ramp = any(e["tracks"].get(k) for k in
                       ("particle_red", "particle_green", "particle_blue", "particle_alpha"))
        scale_track = e["tracks"].get("particle_scale")
        has_curve = bool(scale_track) and any(abs(node_value(n) - 1.0) > 1e-6 for n in scale_track)
        need_canvas = bool(e["flags"] & FLAG_ADDITIVE) or (e["image_col"] * e["image_row"] > 1)
        sub_ids: dict[str, str] = {"process_material": f"PM_{idx}"}
        if has_ramp:
            sub_ids["ramp"] = f"Ramp_{idx}"
        if has_curve:
            sub_ids["scale_curve"] = f"ScaleCurve_{idx}"
        if need_canvas:
            sub_ids["canvas_material"] = f"CM_{idx}"
        tex_id = texture_ids.get(e["image"]) if e["image"] else None
        lines, subs = build_emitter_tscn(e, node_name, tex_id, sub_ids, warnings)
        node_blocks.append("\n".join(lines))
        # Gradient/Curve 内层 sub_resource 必须先声明
        for inner in ("Gradient_1", "Curve_1"):
            if inner in subs:
                sections.append((f"{inner}_{idx}", subs.pop(inner).replace(f'id="{inner}"', f'id="{inner}_{idx}"')))
        for sid, content in subs.items():
            content = content.replace('SubResource("Gradient_1")', f'SubResource("Gradient_1_{idx}")')
            content = content.replace('SubResource("Curve_1")', f'SubResource("Curve_1_{idx}")')
            sections.append((sid, content))

    ext_count = len(texture_ids)
    load_steps = 1 + ext_count + len(sections)
    out: list[str] = [f"[gd_scene load_steps={load_steps} format=3]", ""]
    for image, tid in texture_ids.items():
        out.append(f'[ext_resource type="Texture2D" path="{res_prefix}/textures/{actual_names[image]}" id="{tid}"]')
    out.append("")
    out.extend(content for _, content in sections)
    if sections:
        out.append("")
    out.append(f'[node name="{scene_name}" type="Node2D"]')
    out.append("")
    out.extend(node_blocks)
    return "\n\n".join(out) + "\n", warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_image_lookup(dirs: list[str]) -> dict[str, tuple[str, str]]:
    lookup = {}
    for d in dirs:
        for fn in os.listdir(d):
            if fn.lower().endswith((".png", ".jpg", ".gif")):
                lookup[fn.lower()] = (d, fn)
    return lookup


def process_file(path: str, args, image_lookup) -> tuple[bool, list[str]]:
    name = os.path.basename(path)
    for suffix in (".xml.compiled", ".trail.compiled", ".compiled"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    try:
        decoded = decode_particles(open(path, "rb").read())
    except Exception as exc:  # noqa: BLE001
        return False, [f"{os.path.basename(path)}: 解码失败: {exc}"]
    tex_dir = os.path.join(args.out, "textures")
    if isinstance(decoded, dict) and decoded.get("_type") == "trail":
        tscn, warnings = convert_trail(decoded, name, args.res_prefix, image_lookup, tex_dir)
    else:
        tscn, warnings = convert(decoded, name, args.res_prefix, image_lookup, tex_dir)
    with open(os.path.join(args.out, f"{name}.tscn"), "w", encoding="utf-8") as fp:
        fp.write(tscn)
    if args.json:
        with open(os.path.join(args.out, f"{name}.json"), "w", encoding="utf-8") as fp:
            json.dump(decoded, fp, ensure_ascii=False, indent=1)
    return True, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="PVZ 粒子特效 -> Godot 4 GPUParticles2D 转换器")
    ap.add_argument("input", help=".xml.compiled 文件或目录")
    ap.add_argument("--images", required=True, nargs="+", help="贴图目录, 可多个")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--res-prefix", default="res://pvz", help="贴图 res:// 前缀")
    ap.add_argument("--json", action="store_true", help="同时输出无损 JSON")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    image_lookup = build_image_lookup(args.images)
    if os.path.isdir(args.input):
        files = [os.path.join(args.input, f) for f in sorted(os.listdir(args.input))
                 if f.endswith(".compiled")]
    else:
        files = [args.input]

    ok = fail = 0
    warns: list[str] = []
    for path in files:
        success, w = process_file(path, args, image_lookup)
        ok, fail = ok + success, fail + (not success)
        warns.extend(w)
    print(f"完成: {ok} 成功, {fail} 失败 -> {args.out}")
    for w in sorted(set(warns))[:20]:
        print("  -", w)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
