"""PvZ1 compiled reanim 解析器。

格式（参考 librePvZ reanim-decode）：
  可选压缩头：D4 FE AD DE + 4 字节，其后为 zlib 流
  头部：u32 magic(0xB393B4C0) + 4 pad + u32 track_count + f32 fps + 4 pad + u32(0x0C)
  每条轨道：8 pad + u32 frame_count；随后
    string name（u32 长度 + 字节）+ u32(0x2C)
    frame_count × Transform（8 个 optional f32：x y kx ky sx sy f a，-10000=None，+12 pad = 44 字节）
    frame_count × Elements（3 个 string：image/font/text）
"""
import zlib
import struct

SENTINEL = -10000.0
COMPRESS_MAGIC = bytes([0xD4, 0xFE, 0xAD, 0xDE])


class Track:
    __slots__ = ("name", "transforms", "images")

    def __init__(self, name, transforms, images):
        self.name = name                # str
        self.transforms = transforms    # list[[x,y,kx,ky,sx,sy,f,a] None=缺省]
        self.images = images            # list[str] 每帧图片（可空串）

    @property
    def frame_count(self):
        return len(self.transforms)

    def show_ranges(self):
        """由 f 事件计算可见区间（f>=0 显示）。返回 [(start,end), ...]，end 开区间。"""
        vis, start, ranges = True, 0, []
        for i, t in enumerate(self.transforms):
            if t[6] is None:
                continue
            show = t[6] >= 0
            if vis and not show:
                ranges.append((start, i))
            elif not vis and show:
                start = i
            vis = show
        if vis:
            ranges.append((start, self.frame_count))
        return ranges

    def has_transform(self):
        return any(any(f[i] is not None for i in (0, 1, 2, 3, 4, 5)) for f in self.transforms)


def _read_string(data, pos):
    n, = struct.unpack_from("<I", data, pos)
    pos += 4
    s = data[pos:pos + n].decode("ascii", "replace")
    return s, pos + n


def parse_reanim(path):
    """解析 .reanim / .reanim.compiled，返回 (fps, [Track])。"""
    raw = open(path, "rb").read()
    data = zlib.decompress(raw[8:]) if raw[:4] == COMPRESS_MAGIC else raw
    pos = 4 + 4                                    # magic + padding
    track_count, = struct.unpack_from("<I", data, pos); pos += 4
    fps, = struct.unpack_from("<f", data, pos); pos += 4
    pos += 4 + 4                                   # padding + 0x0C
    frame_counts = []
    for _ in range(track_count):
        pos += 8
        fc, = struct.unpack_from("<I", data, pos); pos += 4
        frame_counts.append(fc)
    tracks = []
    for fc in frame_counts:
        name, pos = _read_string(data, pos)
        magic, = struct.unpack_from("<I", data, pos); pos += 4
        assert magic == 0x2C, f"轨道 {name} 魔术字错位: {magic:#x}"
        transforms = []
        for _ in range(fc):
            vals = struct.unpack_from("<8f", data, pos); pos += 44
            transforms.append([None if v == SENTINEL else v for v in vals])
        images = []
        for _ in range(fc):
            img, pos = _read_string(data, pos)
            _, pos = _read_string(data, pos)       # font
            _, pos = _read_string(data, pos)       # text
            images.append(img)
        tracks.append(Track(name, transforms, images))
    return fps, tracks


def split_labels(tracks):
    """拆分标签（段定义）与部件轨道。

    原版语义（Reanimator.cpp GetFramesForLayer）：PlayReanim("anim_X") 的段范围 =
    anim_X 轨道上 [首个显示帧, 末个显示帧 + 1)，与该轨道是否含图片无关。
    段标记轨道的显示区间互不重叠、拼满时间线；含图部件轨道（anim_cone/
    anim_head1 等）的可见区间跨段重叠，不是段标记。
    按显示帧数升序贪心接受互不重叠的 anim_ 轨道为段标记；含图片的段标记
    （SunFlower anim_idle = 葵花头）同时仍是渲染部件。

    返回 (labels, parts)：
      labels: {anim_名字: (start, end)}  end 开区间
      parts:  [Track] 所有含图片的轨道 + 无图非标签轨道（anim_stem 等参考轨道）
    """
    candidates = []
    for t in tracks:
        if t.name.startswith("anim_"):
            rs = [r for r in t.show_ranges() if r[1] - r[0] > 0]
            if rs:
                candidates.append((t, rs))
    labels = {}
    accepted = []  # 已接受段标记的区间列表
    for t, rs in sorted(candidates, key=lambda c: sum(b - a for a, b in c[1])):
        if all(e <= s2 or s >= e2
               for prev in accepted for (s, e) in rs for (s2, e2) in prev):
            labels[t.name] = (rs[0][0], rs[-1][1])
            accepted.append(rs)
    parts = [t for t in tracks if t.name not in labels or any(t.images)]
    return labels, parts


if __name__ == "__main__":
    import sys
    fps, tracks = parse_reanim(sys.argv[1])
    labels, parts = split_labels(tracks)
    print(f"fps={fps}  轨道={len(tracks)}  标签={len(labels)}  部件={len(parts)}")
    for n, r in sorted(labels.items(), key=lambda kv: kv[1][0] if isinstance(kv[1], tuple) else 0):
        print(f"  标签 {n:28} {r}")
    for t in parts:
        imgs = sorted({i.replace("IMAGE_REANIM_", "") for i in t.images if i})
        print(f"  部件 {t.name:32} 帧={t.frame_count} 图片={imgs}")
