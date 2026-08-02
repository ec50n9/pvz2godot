# pvz2godot

将《植物大战僵尸》(PopCap) 的游戏资源解包，并把 `.reanim.compiled` 动画转换为 **Godot 4** 可直接使用的场景文件 (`.tscn`)。

纯 Python 3 标准库实现，无任何第三方依赖。

## 目录

- [一、解包游戏资源](#一解包游戏资源)
- [二、转换 reanim 动画为 Godot 场景](#二转换-reanim-动画为-godot-场景)
- [三、其余资源的转换方法](#三其余资源的转换方法)
- [四、文件格式文档](#四文件格式文档)
- [原理参考](#原理参考)

---

## 一、解包游戏资源

资源提取分两层：**安装包 → 游戏文件**，**main.pak → 原始资源**。

### 1. 解包安装包 (NSIS)

PvZ 安装程序是 NSIS 自解压包，直接用 [7-Zip](https://7-zip.org/) 解开即可（无需 Windows）：

```bash
# macOS: brew install sevenzip
7zz x 植物大战僵尸中文电脑版安装程序.exe -opvz_extract
```

解出 `PlantsVsZombies.exe`、`bass.dll` 等游戏文件，以及资源包 **`main.pak`**（真正的素材都在这里面）。

### 2. 解包 main.pak

`main.pak` 整体按字节 XOR `0xF7` 混淆，内部是 PopCap PAK 格式（格式细节见[第四节](#四文件格式文档)）。用本仓库的 `unpack_pak.py` 解包：

```bash
python3 unpack_pak.py pvz_extract/main.pak pvz_assets
```

解出约 2400 个文件，目录结构：

| 目录 | 内容 | 格式 |
|---|---|---|
| `compiled/reanim/` | 骨骼动画数据（143 个） | `.reanim.compiled` 二进制，**需转换** |
| `compiled/particles/` | 粒子特效定义 | 编译后的 XML 二进制，需转换 |
| `reanim/` | 动画部件贴图（1500+ 张） | PNG，**直接用** |
| `images/` | 背景、界面图 | PNG/JPG/GIF，**直接用** |
| `sounds/` | 音效与音乐 | OGG（直接用）/ MO3（需转换）/ AU |
| `data/`、`properties/` | 关卡、植物僵尸属性等数据 | 文本/XML/LawnStrings |

---

## 二、转换 reanim 动画为 Godot 场景

用本仓库的 `pvz2godot.py` 把 `compiled/reanim/` 下的动画批量转成 Godot 4 `.tscn`：

```bash
python3 pvz2godot.py pvz_assets/compiled/reanim \
    --images pvz_assets/reanim pvz_assets/images \
    --out /path/to/godot-project/pvz --res-prefix res://pvz
```

之后在 Godot 中把生成的 `.tscn` 拖入场景，选中 `AnimationPlayer` 播放 `walk`、`idle` 等动画即可。已用 Godot 4.6 无头实测 143 个动画全部加载成功。

### 参数

| 参数 | 说明 |
|---|---|
| `input` | 单个 `.reanim.compiled` 文件，或包含它们的目录（批量） |
| `--images` | 贴图目录，可传多个（部件图在 `reanim/`，界面图在 `images/`） |
| `--out` | 输出目录，建议直接指向 Godot 项目内 |
| `--res-prefix` | tscn 中引用贴图的 `res://` 前缀，默认 `res://pvz` |
| `--no-loop` | 动画不循环（默认循环） |

### 生成结构

```
Node2D (如 Zombie)
├── Zombie_body (Sprite2D)      # 各部件节点, 默认值已填首帧状态
├── Zombie_outerarm_upper ...   # 编辑器中打开即是拼装好的造型
└── AnimationPlayer
    └── 动画库: all + 自动拆分的子动画 (walk/idle/eat/death/dance…)
```

### 动画轨道映射

| reanim 字段 | Godot 轨道 | 说明 |
|---|---|---|
| `x` / `y` | `position` | 连续轨道 |
| `kx` | `rotation` | 度→弧度，±π 回绕保持连续 |
| `ky` | `skew` | `skew = ky - kx` |
| `sx` / `sy` | `scale` | 连续轨道 |
| `a` | `self_modulate` | `Color(1,1,1,a)` |
| `f` | `visible` | 离散轨道，`>=0` 显示，`-1` 隐藏 |
| `i` | `texture` | 离散轨道，`IMAGE_REANIM_XXX` → `Xxx.png` |

---

## 三、其余资源的转换方法

| 资源 | 状态 | 方法 |
|---|---|---|
| 部件贴图 (`reanim/*.png`) | ✅ 直接用 | 拖入 Godot 项目即可 |
| 界面图 (`images/`) | ✅ 直接用 | PNG/JPG/GIF 均为通用格式 |
| 音效 (`sounds/*.ogg`) | ✅ 直接用 | Godot 原生支持 OGG Vorbis |
| 音乐 (`sounds/*.mo3`) | ⚠️ 需转换 | MO3 是 BASS 音频库的压缩 Tracker 模块。用 [OpenMPT](https://openmpt.org/) 打开导出 WAV/OGG，或用 [unmo3](https://github.com/8bitbubsy/unmo3) 先解为 MOD/XM 再转 |
| 粒子特效 (`compiled/particles/*.xml.compiled`) | ⚠️ 需转换 | PopCap 编译 XML 格式。可参考 [Sen](https://github.com/Haruma-VN/Sen) 或 Taiji 的解码逻辑解为 XML，再手动映射到 Godot `GPUParticles2D`（发射速率、生命周期、重力、颜色曲线等参数基本一一对应） |
| 字体 (`font/*.txt`) | ⚠️ 需转换 | BMFont 文本格式描述 + 字图，可用 [BMFont 工具](https://www.angelcode.com/products/bmfont/) 或脚本重建，Godot 支持直接导入 `.fnt` |
| 关卡/属性数据 (`data/`、`properties/`) | ✅ 可读 | 文本与 XML，`LawnStrings.txt` 是全部文案 |
| Flash 素材 (`*.swf`) | ⚠️ 仅供查看 | 可用 [JPEXS](https://github.com/jindrapetrik/jpexs-decompiler) 导出 |

---

## 四、文件格式文档

### main.pak（PopCap PAK）

整个文件按字节 **XOR `0xF7`** 混淆，解码后结构：

```
u32 magic = 0xBAC04AC0
u32 version (0)
记录列表, 每条:
  u8  flag               (0x00 = 记录开始, 非 0 = 列表结束)
  u8  name_len
  char name[name_len]    (Windows 风格路径, 反斜杠分隔)
  u32 size
  u64 last_write_time    (Windows FILETIME)
之后是所有文件数据, 按记录顺序紧密排列
```

### .reanim.compiled（骨骼动画）

```
[可选 zlib 外层]  D4 FE AD DE + 4字节 + zlib 数据
header            u32 magic = 0xB393B4C0
                  4 字节填充
                  u32 track_count
                  f32 fps
                  4 字节填充
                  u32 magic = 0x0C
frame_counts      track_count × (8 字节填充 + u32 frame_count)
tracks            track_count × {
                    string name            (u32 长度 + UTF-8)
                    u32 magic = 0x2C
                    frame_count × Transform {
                      8 × optional f32     (x y kx ky sx sy f a，值 <= -10000 表示缺失)
                      12 字节填充
                    }
                    frame_count × Elements {
                      string image, string font, string text
                    }
                  }
```

语义规则：

- 帧中缺失的字段继承上一帧的值（初始：`pos=(0,0)`、`scale=(1,1)`、`rot/skew=0`、`alpha=1`、可见）
- `kx` 为旋转角（度），`ky` 为 y 轴角度（度），`rotation = kx`、`skew = ky - kx`，均需做 ±π 回绕
- 名为 `anim_*` 的轨道不是精灵部件，而是子动画段落标记：`f=0` 为起点、`f=-1` 前一帧为终点

---

## 原理参考

本项目为独立重实现，格式与转换思路参考了以下开源项目：

- [librePvZ/librePvZ](https://github.com/librePvZ/librePvZ)（Rust, AGPL-3.0）— `.reanim.compiled` 二进制解码
- [HYTommm/PVZ_reanim2godot_animation](https://github.com/HYTommm/PVZ_reanim2godot_animation)（C, GPL-3.0）— reanim → Godot 动画的转换逻辑

## 声明

本工具仅用于学习与研究。《植物大战僵尸》游戏素材版权归 PopCap / EA 所有，请勿将解包素材用于商业用途或公开分发。

## License

GPL-3.0
