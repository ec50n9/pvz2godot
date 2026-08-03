# pvz2godot

将《植物大战僵尸》(PopCap) 的游戏资源解包，并转换为 **Godot 4** 可直接使用的资源。

纯 Python 3 标准库实现（仅字体图集转码、音频转 OGG 需要 ffmpeg）。

## 工具一览

| 脚本 | 功能 | 输出 |
|---|---|---|
| `unpack_pak.py` | 解包 `main.pak` 资源包 | 原始资源文件 |
| `pvz2godot.py` | 骨骼动画 `.reanim.compiled` → Godot 场景 | `.tscn` (Sprite2D + AnimationPlayer) |
| `particles2godot.py` | 粒子特效 `.xml.compiled` / 拖尾 `.trail.compiled` → Godot 场景 | `.tscn` (GPUParticles2D / Line2D) + 无损 JSON |
| `font2fnt.py` | 字体描述 txt → BMFont | `.fnt` + RGBA8 PNG 字图 |
| `mo3towav.py` | MO3 音乐 → WAV（经官方 libbass 解码） | `.wav` |

## 目录

- [一、解包游戏资源](#一解包游戏资源)
- [二、转换为 Godot 资源](#二转换为-godot-资源)
- [三、文件格式文档](#三文件格式文档)
- [原理参考](#原理参考)

---

## 一、解包游戏资源

### 1. 解包安装包 (NSIS)

PvZ 安装程序是 NSIS 自解压包，用 [7-Zip](https://7-zip.org/) 解开（无需 Windows）：

```bash
# macOS: brew install sevenzip
7zz x 植物大战僵尸中文电脑版安装程序.exe -opvz_extract
```

解出 `PlantsVsZombies.exe`、DLL 等游戏文件，以及资源包 **`main.pak`**。

### 2. 解包 main.pak

`main.pak` 整体按字节 XOR `0xF7` 混淆，内部是 PopCap PAK 格式（见[格式文档](#三文件格式文档)）：

```bash
python3 unpack_pak.py pvz_extract/main.pak pvz_assets
```

解出约 2400 个文件：

| 目录 | 内容 | 处理方式 |
|---|---|---|
| `compiled/reanim/` | 骨骼动画（143 个） | `pvz2godot.py` |
| `compiled/particles/` | 粒子特效 / 拖尾（107 个） | `particles2godot.py` |
| `reanim/` | 动画部件贴图（1500+ 张） | 直接用 |
| `images/`、`particles/`、`qarticles/` | 界面图、粒子贴图 | 直接用 |
| `sounds/` | 音效 OGG（直接用）/ MO3 音乐 / AU | `mo3towav.py` / ffmpeg |
| `eata/` + `data/` | 字体描述 txt + 字图 | `font2fnt.py` |
| `properties/`、`data/` | resources.xml、关卡与文案 | 直接读取 |

> 注：`jmages/seanim/eata/qarticles/qroperties` 是中文版 pak 里目录名各字母 +1 的重复目录（如 `eata` ≈ `data`），存放字体等本地化资源。

---

## 二、转换为 Godot 资源

### 骨骼动画 → Godot 场景

```bash
python3 pvz2godot.py pvz_assets/compiled/reanim \
    --images pvz_assets/reanim pvz_assets/images \
    --out /path/to/godot-project/pvz --res-prefix res://pvz
```

每个动画生成一个 `.tscn`：`Node2D` 根节点 + 每部件 `Sprite2D` + `AnimationPlayer`（含 `all` 及自动拆分的 `walk/idle/eat/death…` 子动画）。节点默认值已填首帧状态，编辑器中打开即是拼装好的造型。已用 Godot 4.6 实测 143 个全部加载成功。

动画轨道映射：

| reanim 字段 | Godot 轨道 | 说明 |
|---|---|---|
| `x` / `y` | `position` | 连续轨道 |
| `kx` | `rotation` | 度→弧度，±π 回绕保持连续 |
| `ky` | `skew` | `skew = ky - kx` |
| `sx` / `sy` | `scale` | 连续轨道 |
| `a` | `self_modulate` | `Color(1,1,1,a)` |
| `f` | `visible` | 离散轨道，`>=0` 显示，`-1` 隐藏 |
| `i` | `texture` | 离散轨道，`IMAGE_REANIM_XXX` → `Xxx.png` |

### 粒子特效 / 拖尾 → Godot 场景

```bash
python3 particles2godot.py pvz_assets/compiled/particles \
    --images pvz_assets/particles pvz_assets/qarticles pvz_assets/images pvz_assets/reanim \
    --out /path/to/godot-project/pvz/particles --res-prefix res://pvz --json
```

- 每个 `.xml.compiled` → 一个 `.tscn`：`Node2D` + 每发射器一个 `GPUParticles2D`（`ParticleProcessMaterial`）
- `.trail.compiled`（拖尾）→ `Line2D` 模板（纹理平铺 + 宽度曲线 + 透明度渐变）
- `--json` 额外输出无损 JSON，包含全部轨道数据，便于手动微调
- 实例化后调用 `GPUParticles2D.restart()` 即可播放

映射规则（近似，已用 Godot 4.6 实测 107 个全部加载成功）：

| PvZ 粒子参数 | Godot | 说明 |
|---|---|---|
| `SpawnMaxActive` / `SpawnRate` | `amount` | MaxActive 缺失时用 速率×寿命估算 |
| `ParticleDuration` | `lifetime` | 单位 1/100 秒 → 秒 |
| `SystemDuration`+循环标志 | `one_shot` | |
| `EmitterType` + Radius/Box | `emission_shape` | 圆/盒/环 |
| `LaunchAngle` | `direction` + `spread` | 弧度→度（均为 y 向下坐标系） |
| `LaunchSpeed` | `initial_velocity` | px/s |
| `Field`(加速度/摩擦) | `gravity` / `damping` | |
| `ParticleRed/Green/Blue/Alpha` | `color_ramp` | × System 颜色，GradientTexture1D |
| `ParticleScale` | `scale_amount_curve` | CurveTexture |
| `ParticleSpinAngle/Speed` | `initial_rotation` / `angular_velocity` | |
| `ImageCol/Row` > 1 | `CanvasItemMaterial` 翻页动画 | h/v frames |
| `PARTICLE_ADDITIVE` | `blend_mode = add` | |
| `PARTICLE_DONT_FOLLOW` | `local_coords = false` | |

未映射：stretch、clip、emitter_path、碰撞等（转换时会给出警告，数据保留在 JSON 里）。

### 字体 → BMFont

```bash
python3 font2fnt.py pvz_assets/eata \
    --images pvz_assets/data pvz_assets/eata \
    --out /path/to/godot-project/fonts
```

生成 `.fnt`（BMFont 文本格式）+ RGBA8 PNG 字图，Godot 4 可直接作为字体导入（已实测加载为 `FontFile`）。GBK 编码的中文字符会正确转为 Unicode codepoint。`BrianneTod32Black` 等变体自动回退使用基础字体图集。

### 音乐 / 音效

```bash
# MO3 -> WAV (需要 libbass, 见下)
python3 mo3towav.py pvz_assets/sounds --out wav_out --bass /path/to/libbass.dylib
# WAV -> OGG
ffmpeg -i wav_out/mainmusic.wav -c:a vorbis -strict -2 mainmusic.ogg
# AU -> OGG
ffmpeg -i diamond.au -ac 2 -c:a vorbis -strict -2 diamond.ogg
```

MO3 是 BASS 音频库的私有压缩 Tracker 格式，ffmpeg 不支持，本脚本通过 ctypes 调用官方 libbass 解码（游戏本体也用 bass.dll 播放）。获取 libbass：

- 官网 <https://www.un4seen.com/bass.html>
- 或 NuGet 包 `ppy.osu.Framework.NativeLibs`（osu! 同款，含各平台 arm64/x64 libbass），nupkg 即 zip，在 `runtimes/<平台>/native/` 下

---

## 三、文件格式文档

### main.pak（PopCap PAK）

整个文件按字节 **XOR `0xF7`** 混淆，解码后：

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

### 粒子 .xml.compiled（PvZ PC 版粒子）

```
[可选 zlib 外层]  D4 FE AD DE + 4字节(uncompressed size) + zlib 数据
header            i32 magic = 1092589901 (0x411F994D)
                  i32 (未知)
                  i32 emitter_count
                  i32 id = 0x164
发射器头表        emitter_count × {
                    4 字节跳过
                    i32 image_col, i32 image_row, i32 image_frames(默认1),
                    i32 animated, i32 particle_flags, i32 emitter_type(0圆/1盒/3,4环)
                    8 字节跳过
                    22×8 字节跳过
                    4 字节跳过
                    i32 field_count
                    4 字节跳过
                    i32 system_field_count
                    16×8 字节跳过
                  }
发射器数据        emitter_count × {
                    string image ("IMAGE_XXX"), string name
                    track system_duration, string on_duration
                    20 条 track (顺序见 particles2godot.py TRACK_ORDER)
                    i32 id = 0x14
                    field_count × (i32 field_type + 16 字节跳过)
                    field_count × (track x, track y)        # 粒子场
                    i32 id = 0x14
                    system_field_count × (i32 field_type + 16 字节跳过)
                    system_field_count × (track x, track y) # 系统场
                    15 条 track (particle_red … animation_rate)
                  }

track = i32 count (0 = 缺失, 用默认值)
      + count × { f32 time, f32 low, f32 high, i32 curve(默认1), i32 distribution(默认1) }
```

语义（源自 PvZ 反编译，带默认值）：

- 轨道时间 `time` 为归一化进度（系统轨道相对系统时长，粒子轨道相对粒子寿命）
- 时间单位为 **1/100 秒**（`ParticleDuration` 默认 100 = 1 秒）；颜色/缩放类轨道默认 1，其余默认 0，`SpawnMin/MaxActive/MaxLaunched` 默认 -1（不限）
- 轨道节点在 `[low, high]` 区间取值，曲线类型见 TodCurves 枚举
- 场类型：1=摩擦，2=加速度，3=弹性，4=限速，5=匀速，6=定位，7=系统定位，8=地面约束，9=震动，10=引力(圆周)，11=斥力
- flags 位：`0x1` 随机发射旋转，`0x2` 旋转对齐发射方向，`0x8` 系统循环，`0x10` 粒子循环，`0x20` 粒子不跟随，`0x100` 叠加混合

### 拖尾 .trail.compiled

```
[可选 zlib 外层]  同上
i32 magic = -1416928589 (0xAB8B62B3)
i32 (未知)
i32 max_points (默认2)
f32 min_point_distance (默认1)
i32 trail_flags
5×8 字节跳过
string image
5 条 track: width_over_length, width_over_time,
            alpha_over_length, alpha_over_time, trail_duration
```

### 字体 txt（GBK 编码文本）

```
Define CharList   ( 'A', 'B', …, "'", "中", … );   # 单引号字符用双引号包围
Define WidthList  ( 9, 10, … );                    # 每个字符的步进宽度
Define RectList   ( (x, y, w, h), … );             # 每个字符在字图中的区域
Define OffsetList ( (ox, oy), … );                 # 每个字符的绘制偏移
```

字图命名通常为 `_<字体名>.png/.gif`；带后缀的变体（如 `BrianneTod32Black`）共用基础字体的图。

---

## 原理参考

本项目为独立重实现，格式与转换思路参考了以下开源项目：

- [librePvZ/librePvZ](https://github.com/librePvZ/librePvZ)（Rust, AGPL-3.0）— `.reanim.compiled` 二进制解码
- [HYTommm/PVZ_reanim2godot_animation](https://github.com/HYTommm/PVZ_reanim2godot_animation)（C, GPL-3.0）— reanim → Godot 动画的转换逻辑
- [YingFengTingYu/PopStudio](https://github.com/YingFengTingYu/PopStudio)（C#）— 粒子/拖尾 compiled 二进制布局
- [InLiothixi/stabledecompile](https://github.com/InLiothixi/stabledecompile)（PvZ 反编译）— 粒子字段语义、默认值与枚举
- [un4seen BASS](https://www.un4seen.com/) — MO3 解码（通过官方动态库）

## 声明

本工具仅用于学习与研究。《植物大战僵尸》游戏素材版权归 PopCap / EA 所有，请勿将解包素材用于商业用途或公开分发。BASS 音频库有自己的许可条款（非商业用途免费）。

## License

GPL-3.0
