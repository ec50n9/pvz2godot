"""PvZ1 compiled reanim → Godot .tscn 转换器。

用法：
    python3 convert.py <input.reanim.compiled> <贴图源目录> <输出.tscn> <项目贴图目录(res:// 相对)>

例：
    python3 convert.py \\
        pvz_assets/compiled/reanim/Zombie.reanim.compiled \\
        pvz_assets/reanim \\
        out/zombie.tscn \\
        res://assets/art/reanim/zombies/textures

生成结构（cutout 骨骼）：
    Node2D 根节点 + 每个部件轨道一个 Sprite2D（文件顺序即绘制顺序）
    AnimationPlayer + AnimationLibrary，每个 reanim 标签切分为一个独立动画
    （walk/eat/death 等，时间从 0 重排，loop 按类型设置）
"""
import math
import os
import re
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reanim_parse import parse_reanim, split_labels

# 循环播放的标签（其余为单次）。含僵尸（walk/eat）与植物（idle/sleep/armed 等）
LOOP_LABELS = {
    # 僵尸
    "anim_idle", "anim_idle2", "anim_walk", "anim_walk2",
    "anim_eat", "anim_swim", "anim_dance",
    # 植物
    "anim_full_idle", "anim_head_idle", "anim_head_idle1", "anim_head_idle2",
    "anim_head_idle3", "anim_sleep", "anim_bigsleep", "anim_bigidle",
    "anim_loop", "anim_scaredidle", "anim_idlehigh", "anim_idle_aquarium",
    "anim_nonactive_idle", "anim_nonactive_idle2", "anim_unactive_idle",
    "anim_unarmed_idle", "anim_armed", "anim_pulse", "anim_flame",
    "anim_crawl", "anim_normal", "anim_zengarden", "anim_idlehigh",
    "anim_splitpea_idle", "anim_sprout", "anim_waterplants", "anim_chew",
}
# 不生成节点/轨道的参考轨道
SKIP_TRACKS = {"_ground"}
# 显隐由运行时代码管理的轨道（不生成 visible 动画轨道）：
# 配饰（原版由游戏代码按僵尸类型显隐）+ 断臂/掉头部件（损伤时代码隐藏）。
# 若生成 visible 轨道，AnimationPlayer 播放时会覆盖代码设置的显隐状态。
RUNTIME_MANAGED_TRACKS = {
    "anim_cone", "anim_bucket", "anim_screendoor",
    "Zombie_flaghand", "Zombie_duckytube",
    "Zombie_whitewater", "Zombie_whitewater2", "Zombie_mustache",
    "Zombie_innerarm_screendoor", "Zombie_innerarm_screendoor_hand",
    "Zombie_outerarm_screendoor",
    "Zombie_outerarm_upper", "Zombie_outerarm_lower", "Zombie_outerarm_hand",
    "anim_head1", "anim_head2",
}


def image_file_name(image_id: str) -> str:
    """IMAGE_REANIM_ZOMBIE_CONE1 -> Zombie_cone1.png（librePvZ 命名规则）"""
    s = image_id.removeprefix("IMAGE_REANIM_")
    return s[0] + s[1:].lower() + ".png"


def _stem_key(filename: str) -> str:
    """贴图文件规范化键：去扩展名、小写、去尾部下划线。
    尾部下划线是解包器的命名习惯：jpg 的 alpha 遮罩或与 jpg 同名的 png 存为 Foo_.png。"""
    return os.path.splitext(filename)[0].lower().rstrip("_")


def _palette_is_gray(path: str) -> bool:
    """调色板 PNG 的 PLTE 是否全为 R=G=B → 判定为 alpha 遮罩（否则是独立贴图）。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        pos = 8
        while pos + 8 <= len(data):
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            ctype = data[pos + 4:pos + 8]
            if ctype == b"PLTE":
                plte = data[pos + 8:pos + 8 + length]
                return all(plte[i] == plte[i + 1] == plte[i + 2]
                           for i in range(0, len(plte) - 2, 3))
            pos += 12 + length
            if ctype == b"IEND":
                break
    except OSError:
        pass
    return False


def _compose_rgba(jpg_path: str, mask_path: str, out_path: str) -> bool:
    """jpg(RGB) + 灰度遮罩(alpha) 合成 RGBA png。需要 Pillow，缺失时返回 False。"""
    try:
        from PIL import Image
    except ImportError:
        return False
    rgb = Image.open(jpg_path).convert("RGB")
    mask = Image.open(mask_path).convert("L")
    if mask.size != rgb.size:
        mask = mask.resize(rgb.size)
    rgb.putalpha(mask)
    rgb.save(out_path)
    return True


def build_tex_index(tex_src_dirs) -> dict:
    """扫描多个贴图源目录：规范化名 -> (rgb 完整路径, alpha 遮罩路径或 None)。

    解包素材的几种形态（同名按优先级）：
      1. 普通 .png：直接可用（含彩色调色板 + tRNS 的）。
      2. .jpg + 灰度 <名>_.png：jpg 存 RGB、遮罩存 alpha，需合成 RGBA。
      3. 单独的 .jpg：无透明，直接用。
      4. 彩色的 <名>_.png：与 jpg 同名的独立贴图，直接用。
    """
    normal, underscored, jpg = {}, {}, {}
    try:
        import PIL  # noqa: F401
        has_pil = True
    except ImportError:
        has_pil = False
        print("[警告] 未安装 Pillow，jpg+遮罩 贴图将退化为不透明 jpg（pip install Pillow）")
    for d in tex_src_dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            stem, ext = os.path.splitext(f)
            ext = ext.lower()
            full = os.path.join(d, f)
            if ext == ".png":
                (underscored if stem.endswith("_") else normal).setdefault(_stem_key(f), full)
            elif ext in (".jpg", ".jpeg"):
                jpg.setdefault(_stem_key(f), full)
    index = {}
    for key, p in normal.items():
        index[key] = (p, None)
    for key, p in underscored.items():
        if key in index:
            continue
        if _palette_is_gray(p) and key in jpg:
            # 灰度遮罩 + jpg → 合成（无 Pillow 时退化为不透明 jpg）
            index[key] = (jpg[key], p) if has_pil else (jpg[key], None)
        else:
            index[key] = (p, None)
    for key, p in jpg.items():
        index.setdefault(key, (p, None))
    return index


def fmt_time(v: float) -> str:
    """时间键格式化。需要比 fmt_f 更高的精度：5 位小数的舍入误差在
    陡坡段（如传送式循环的回绕段，斜率数百 px/帧）会被插值比例放大。"""
    r = round(v, 7)
    if abs(r - round(r)) < 1e-7:
        return str(int(round(r)))
    return repr(r)


def fmt_f(v: float) -> str:
    """浮点字面量格式化。必须始终带小数点：values 数组是无类型 Variant 数组，
    整数值写成 0 会被解析成 INT，与 FLOAT 键混排时 AnimationMixer 无法混合，
    整条轨道静默失效（position 用 Vector2() 构造不受影响，rotation/skew 中招）。"""
    r = round(v, 5)
    if abs(r - round(r)) < 1e-5:
        return str(int(round(r))) + ".0"
    return repr(r)


def fmt_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) or isinstance(v, int):
        return fmt_f(float(v))
    if isinstance(v, tuple):
        if v[0] == "v2":
            return f"Vector2({fmt_f(v[1])}, {fmt_f(v[2])})"
        if v[0] == "color":
            return f"Color(1, 1, 1, {fmt_f(v[1])})"
    if isinstance(v, str):  # ExtResource id
        return f'ExtResource("{v}")'
    raise ValueError(v)


class PartState:
    """单条部件轨道的进位状态（None = 沿用上一帧）。"""

    def __init__(self, track):
        self.x = self.y = self.kx = self.ky = 0.0
        self.sx = self.sy = 1.0
        self.a = 1.0
        self.visible = True
        self.img = next((i for i in track.images if i), "")
        # 该轨道拥有哪些数据通道（决定是否生成对应动画轨道）
        self.has_pos = any(f[0] is not None or f[1] is not None for f in track.transforms)
        self.has_rot = any(f[2] is not None or f[3] is not None for f in track.transforms)
        self.has_scale = any(f[4] is not None or f[5] is not None for f in track.transforms)
        self.has_alpha = any(f[7] is not None for f in track.transforms)
        self.has_vis = track.name not in RUNTIME_MANAGED_TRACKS and \
            any(f[6] is not None for f in track.transforms)
        self.has_img = len({i for i in track.images if i}) > 1

    def feed(self, frame):
        for i, attr in enumerate(("x", "y", "kx", "ky", "sx", "sy")):
            if frame[i] is not None:
                setattr(self, attr, frame[i])
        if frame[6] is not None:
            self.visible = frame[6] >= 0
        if frame[7] is not None:
            self.a = frame[7]

    def props(self, tex_ids: dict) -> dict:
        p = {}
        if self.has_pos:
            p["position"] = ("v2", self.x, self.y)
        if self.has_rot:
            p["rotation"] = math.radians(self.kx)
            p["skew"] = math.radians(self.ky - self.kx)
        if self.has_scale:
            p["scale"] = ("v2", self.sx, self.sy)
        if self.has_alpha:
            p["self_modulate"] = ("color", self.a)
        if self.has_vis:
            p["visible"] = self.visible
        if self.has_img:
            p["texture"] = tex_ids.get(self.img) if self.img else None
        return p


def build_animation(parts, start, end, fps, loop, tex_ids):
    """把主时间线 [start,end) 段切分为一个 Animation 的文本轨道。

    原版语义（Reanimator.cpp）：加载时空帧逐帧用前值填充，播放时仅在相邻帧间插值。
    等价转换：逐帧采样进位状态，在每个变化点前后成双出键 —— 平段无键，
    变化处两键间隔恰为 1 帧，Godot 的线性插值只发生在该帧内，与原版一致。
    """
    n = end - start
    anim = {
        "length": n / fps,
        "loop": loop,
        "tracks": [],  # (path, prop, discrete, [(t, value), ...])
    }
    for part in parts:
        st = PartState(part["track"])
        # 进位到段起点
        for i in range(start):
            st.feed(part["track"].transforms[i])
            if part["track"].images[i]:
                st.img = part["track"].images[i]
        # 逐帧采样完整状态
        samples = []
        for i in range(start, end):
            st.feed(part["track"].transforms[i])
            if part["track"].images[i]:
                st.img = part["track"].images[i]
            samples.append(st.props(tex_ids))
        # 连续通道：变化点前后成双出键
        for prop in ("position", "rotation", "skew", "scale", "self_modulate"):
            if prop not in samples[0]:
                continue
            vals = [s[prop] for s in samples]
            ks = []
            for i, v in enumerate(vals):
                changed_here = i == 0 or v != vals[i - 1]
                changed_next = i < n - 1 and v != vals[i + 1]
                if changed_here or changed_next:
                    ks.append((i / fps, v))
            if ks:
                anim["tracks"].append((part["name"], prop, False, ks))
        # 离散通道：段首强制锚定 + 变化时出键。
        # 原版可见性/贴图由全局进位决定，与播哪段无关；不锚定的话，
        # 切换动画后 Godot 节点会残留上一个动画的状态（贴图/显存错乱）。
        for prop in ("visible", "texture"):
            if prop not in samples[0]:
                continue
            ks, last = [], None
            for i, s in enumerate(samples):
                v = s[prop]
                if v is not None and (i == 0 or v != last):
                    ks.append((i / fps, v))
                    last = v
            if ks:
                anim["tracks"].append((part["name"], prop, True, ks))
    return anim


def anim_to_text(anim_id, anim, name):
    lines = [f'[sub_resource type="Animation" id="{anim_id}"]',
             f'resource_name = "{name}"',
             f'length = {fmt_time(anim["length"])}']
    if anim["loop"]:
        lines.append("loop_mode = 1")
    for ti, (node, prop, discrete, ks) in enumerate(anim["tracks"]):
        times = ", ".join(fmt_time(t) for t, _ in ks)
        vals = ", ".join(fmt_value(v) for _, v in ks)
        lines += [
            f'tracks/{ti}/type = "value"',
            f'tracks/{ti}/imported = false',
            f'tracks/{ti}/enabled = true',
            f'tracks/{ti}/path = NodePath("{node}:{prop}")',
            f'tracks/{ti}/interp = 1',
            # 回绕不插值：原版循环在末帧钳制、回绕时瞬切回首帧（Reanimator.cpp
            # GetFrameTime 末帧 before=after）。若开启，[末键, length] 区间会向
            # 首键线性插值，一次性动画（rake/splash）会出现位置/透明度闪变。
            f'tracks/{ti}/loop_wrap = false',
            f'tracks/{ti}/keys = {{',
            f'"times": PackedFloat32Array({times}),',
            f'"transitions": PackedFloat32Array({", ".join("1" for _ in ks)}),',
            f'"update": {1 if discrete else 0},',
            f'"values": [{vals}]',
            '}',
        ]
    return "\n".join(lines)


def unique_part_names(part_tracks) -> list:
    """部件轨道去重命名。

    reanim 中存在同名轨道（WinterMelon 的 frontleaf_tip_left、Zombie_charred 的
    pile2 等），Godot 同名兄弟节点的 NodePath 只会命中第一个，必须改名。
    返回 [{"name": 唯一名, "track": Track}]。
    """
    parts, used = [], set()
    for t in part_tracks:
        if t.name in SKIP_TRACKS:
            continue
        name, n = t.name, 2
        while name in used:
            name = f"{t.name}__{n}"
            n += 1
        used.add(name)
        parts.append({"name": name, "track": t})
    return parts


def _is_cyclic(parts) -> bool:
    """无标签文件的循环性判定：所有部件首帧与末帧的进位状态近似一致 → 循环。
    （idle 摇摆类首尾闭合；rake/splash 等一次性动画首尾差异大。）"""
    for p in parts:
        st = PartState(p["track"])
        first = None
        for i in range(p["track"].frame_count):
            st.feed(p["track"].transforms[i])
            if p["track"].images[i]:
                st.img = p["track"].images[i]
            if i == 0:
                first = (st.x, st.y, st.kx, st.ky, st.sx, st.sy, st.a, st.visible, st.img)
        last = (st.x, st.y, st.kx, st.ky, st.sx, st.sy, st.a, st.visible, st.img)
        if first is None or first[7] != last[7] or first[8] != last[8]:
            return False
        # 角度按模 360 比较（0° 与 360° 视为相同）
        ang = lambda a, b: abs((a - b + 180) % 360 - 180)
        if abs(first[0] - last[0]) > 0.5 or abs(first[1] - last[1]) > 0.5:
            return False
        if ang(first[2], last[2]) > 0.5 or ang(first[3], last[3]) > 0.5:
            return False
        if abs(first[4] - last[4]) > 0.01 or abs(first[5] - last[5]) > 0.01:
            return False
        if abs(first[6] - last[6]) > 0.01:
            return False
    return True


def convert(src, tex_src_dirs, out_tscn, res_tex_base):
    if isinstance(tex_src_dirs, str):
        tex_src_dirs = [tex_src_dirs]
    fps, tracks = parse_reanim(src)
    labels, part_tracks = split_labels(tracks)
    parts = unique_part_names(part_tracks)
    loop_labels = LOOP_LABELS
    if not labels and parts:
        # 无标签轨道（SunFlower/Tallnut 等）：整个时间线视为一段。
        # 首尾闭合的（idle 摇摆类）为循环；rake/splash 等一次性动画不循环，
        # 否则循环播放时首尾瞬切 + 回绕段保持会让观众看到状态残留。
        labels = {"anim_idle": (0, max(p["track"].frame_count for p in parts))}
        if not _is_cyclic(parts):
            loop_labels = LOOP_LABELS - {"anim_idle"}
    tex_index = build_tex_index(tex_src_dirs)

    def resolve(image_id: str):
        """IMAGE_REANIM_XX -> (rgb 源路径, alpha 遮罩路径或 None, 输出文件名)。"""
        key = _stem_key(image_file_name(image_id))
        entry = tex_index.get(key)
        assert entry, f"缺贴图 {image_id}（{tex_src_dirs}）"
        src_path, mask_path = entry
        out_name = os.path.basename(src_path)
        if mask_path is not None:
            out_name = os.path.splitext(out_name)[0] + ".png"  # 合成产物为 png
        return src_path, mask_path, out_name

    stem = os.path.splitext(os.path.basename(src))[0].replace(".reanim", "")
    # 节点名合法化 + 去重（NodePath 不能含 . : / @ % 等；重名轨道需唯一命名）
    seen = {}
    for p in parts:
        n = re.sub(r'[./:@%"]', "_", p["name"])
        if n in seen:
            seen[n] += 1
            n = f"{n}_{seen[n]}"
        else:
            seen[n] = 1
        p["name"] = n
    # 收集图片（tex_ids: image_id -> ExtResource id；img_files: image_id -> 输出文件名）
    tex_ids, img_files, img_srcs, ext_lines = {}, {}, {}, []
    for p in parts:
        for img in {i for i in p["track"].images if i}:
            if img in tex_ids:
                continue
            src_path, mask_path, out_name = resolve(img)
            img_files[img] = out_name
            img_srcs[img] = (src_path, mask_path)
            tex_ids[img] = str(len(tex_ids) + 1)
    for img, tid in tex_ids.items():
        fn = img_files[img]
        ext_lines.append(f'[ext_resource type="Texture2D" path="{res_tex_base}/{fn}" id="{tid}"]')

    # 生成动画
    anim_blocks, lib_entries = [], []
    for li, (label, rng) in enumerate(sorted(labels.items(), key=lambda kv: kv[1][0])):
        start, end = rng
        name = label.removeprefix("anim_")
        anim = build_animation(parts, start, end, fps, label in loop_labels, tex_ids)
        anim_id = f"Animation_{name}"
        anim_blocks.append(anim_to_text(anim_id, anim, name))
        lib_entries.append(f'&"{name}": SubResource("{anim_id}")')

    # 节点初始状态（主时间线帧 0 的进位状态）
    node_blocks = []
    for p in parts:
        st = PartState(p["track"])
        st.feed(p["track"].transforms[0])
        if p["track"].images[0]:
            st.img = p["track"].images[0]
        props = st.props(tex_ids)
        lines = [f'[node name="{p["name"]}" type="Sprite2D" parent="."]',
                 'centered = false']  # reanim 的 (x,y) 是图片左上角，旋转/缩放亦绕左上角
        if not props.get("visible", True):
            lines.append("visible = false")
        if "position" in props:
            lines.append(f"position = {fmt_value(props['position'])}")
        if "rotation" in props and abs(props["rotation"]) > 1e-6:
            lines.append(f"rotation = {fmt_f(props['rotation'])}")
        if "scale" in props and props["scale"] != ("v2", 1.0, 1.0):
            lines.append(f"scale = {fmt_value(props['scale'])}")
        tex = tex_ids.get(st.img) if st.img else None
        if tex:
            lines.append(f'texture = ExtResource("{tex}")')
        node_blocks.append("\n".join(lines))

    lib_id = "AnimationLibrary_0"
    lib = f'[sub_resource type="AnimationLibrary" id="{lib_id}"]\n_data = {{\n' + \
          ",\n".join(lib_entries) + "\n}"
    player = (f'[node name="AnimationPlayer" type="AnimationPlayer" parent="."]\n'
              f'libraries = {{\n&"": SubResource("{lib_id}")\n}}')

    load_steps = 1 + len(tex_ids) + len(anim_blocks) + 1
    content = f'[gd_scene load_steps={load_steps} format=3]\n\n' + \
              "\n\n".join(ext_lines) + "\n\n" + \
              "\n\n".join(anim_blocks) + "\n\n" + lib + "\n\n" + \
              f'[node name="{stem}" type="Node2D"]\n\n' + \
              "\n\n".join(node_blocks) + "\n\n" + player + "\n"
    os.makedirs(os.path.dirname(out_tscn), exist_ok=True)
    with open(out_tscn, "w") as f:
        f.write(content)

    # 复制贴图
    dst_dir = res_tex_base.removeprefix("res://")
    os.makedirs(dst_dir, exist_ok=True)
    for img in tex_ids:
        src_path, mask_path = img_srcs[img]
        dst = os.path.join(dst_dir, img_files[img])
        if mask_path is not None:
            _compose_rgba(src_path, mask_path, dst)  # jpg + 灰度遮罩 → RGBA png
        else:
            shutil.copy2(src_path, dst)
    print(f"OK {stem}: 部件 {len(parts)}，动画 {len(anim_blocks)}，贴图 {len(tex_ids)} -> {out_tscn}")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
