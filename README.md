# pvz2godot

《植物大战僵尸》(PopCap) 游戏资源 → **Godot 4** 的完整转换管线：解包 main.pak，骨骼动画 / 粒子特效 / 字体 / 音乐一键转换。

```bash
# 1. 解包 main.pak（安装程序是 NSIS 包，先用 7zz x 解压拿到 main.pak）
python3 unpack_pak.py main.pak pvz_assets

# 2. 骨骼动画 → .tscn（Node2D + Sprite2D + AnimationPlayer，拖进 Godot 即用）
python3 convert_all.py pvz_assets/compiled/reanim assets/art/reanim \
    --tex pvz_assets/reanim pvz_assets/seanim pvz_assets/images

# 3. 粒子特效/拖尾 → .tscn（GPUParticles2D / Line2D）
python3 particles2godot.py pvz_assets/compiled/particles \
    --images pvz_assets/particles pvz_assets/qarticles pvz_assets/images \
    --out assets/art/particles --res-prefix res://assets/art/particles --json

# 4. 字体 → BMFont .fnt + 字图
python3 font2fnt.py pvz_assets/eata --images pvz_assets/data pvz_assets/eata --out assets/fonts

# 5. MO3 音乐 → WAV（再 ffmpeg 转 OGG）
python3 mo3towav.py pvz_assets/sounds --out wav_out --bass /path/to/libbass.dylib
ffmpeg -i wav_out/mainmusic.wav -c:a vorbis -strict -2 mainmusic.ogg
```

**依赖**：Python 3.9+，纯标准库。可选：Pillow（jpg+遮罩贴图合成 RGBA）、ffmpeg（GIF 字图转码 / WAV→OGG）、libbass（MO3 解码，[获取方式](#音乐--音效)）。

## 工具一览

| 脚本 | 功能 | 输出 |
|---|---|---|
| `unpack_pak.py` | 解包 `main.pak` 资源包（XOR 0xF7 + PopCap PAK） | 原始资源文件 |
| `convert.py` | 单个骨骼动画 `.reanim.compiled` → Godot 场景 | `.tscn` |
| `convert_all.py` / `convert_plants.py` | reanim 批量转换（按用途分组 / 49 种植物） | `.tscn` + 贴图 |
| `verify.py` | reanim 转换正确性校验（与原版逐帧语义比对） | 退出码 0 = 通过 |
| `reanim_parse.py` | compiled reanim 二进制解析器（库） | — |
| `particles2godot.py` | 粒子 `.xml.compiled` / 拖尾 `.trail.compiled` → Godot 场景 | `.tscn` + 无损 JSON |
| `font2fnt.py` | 字体描述 txt（GBK）→ BMFont | `.fnt` + RGBA8 PNG 字图 |
| `mo3towav.py` | MO3 音乐 → WAV（ctypes 调官方 libbass） | `.wav` |

## 骨骼动画（reanim）

```bash
# 单文件转换
python3 convert.py pvz_assets/compiled/reanim/Zombie.reanim.compiled \
    pvz_assets/reanim out/zombie.tscn res://assets/art/reanim/zombies/textures

# 校验转换结果是否与原版逐帧一致
python3 verify.py pvz_assets/compiled/reanim/Zombie.reanim.compiled out/zombie.tscn

# 批量校验整个输出目录
python3 verify.py --all pvz_assets/compiled/reanim assets/art/reanim

# 只转 49 种植物（或按子串过滤，注意过滤词放在 --tex 之前）
python3 convert_plants.py pvz_assets/compiled/reanim assets/art/reanim/plants \
    pea sun wall --tex pvz_assets/reanim
```

生成的 `.tscn`：根节点 Node2D + 每个部件一个 Sprite2D（文件顺序即绘制顺序，`centered = false`，与 reanim 的左上角锚点一致）+ AnimationPlayer，每个 reanim 标签（`anim_walk`/`anim_eat`/`anim_death`…）切分为独立动画：

```gdscript
var zombie = load("res://assets/art/reanim/zombies/zombie.tscn").instantiate()
add_child(zombie)
zombie.get_node("AnimationPlayer").play("walk")
```

贴图支持三种形态（多目录按优先级查找）：普通 `.png` 直接用；`.jpg` + 灰度 `<名>_.png` 遮罩自动合成 RGBA（需 Pillow）；单独 `.jpg` 无透明直接用。

### 原版语义保证

转换不是近似拟合，而是逐帧等价还原 Reanimator.cpp 的加载/播放语义：

- **空帧逐帧填充**：reanim 空帧 = 沿用前一帧的值，加载时填充，播放时仅在相邻帧间插值
- **成对出键**：在每个变化点前后各出一个键，Godot 线性插值只发生在该 1 帧内，与原版一致
- **`loop_wrap = false`**：原版循环末帧钳制、回绕瞬切首帧；开启回绕插值会导致一次性动画（如 rake/splash）闪变
- **离散通道段首锚定**：visible/texture 由全局进位决定，与播哪段无关；不锚定会在切换动画后残留状态
- **标签切分**：`anim_*` 标签轨道的显示区间即段范围（[首个显示帧, 末个显示帧+1)）；含图片的 `anim_*` 轨道（葵花头、路障等）仍是渲染部件，不做段标记；无标签文件（SunFlower 等）整线一段，首尾闭合才判定为循环
- **同名轨道去重**：reanim 存在同名轨道（WinterMelon 的 frontleaf_tip_left 等），Godot NodePath 只命中第一个，自动改名为 `name__2`
- **运行时管理轨道**：配饰（铁桶/路障/纱门）与断臂/掉头部件的显隐由游戏代码控制，不生成 visible 动画轨道（见 `convert.py` 的 `RUNTIME_MANAGED_TRACKS`）
- **浮点格式化**：所有浮点字面量强制带小数点——values 是无类型 Variant 数组，整数值会被解析成 INT 导致轨道静默失效

## 粒子特效 / 拖尾

```bash
python3 particles2godot.py pvz_assets/compiled/particles \
    --images pvz_assets/particles pvz_assets/qarticles pvz_assets/images pvz_assets/reanim \
    --out assets/art/particles --res-prefix res://assets/art/particles --json
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

## 字体

```bash
python3 font2fnt.py pvz_assets/eata --images pvz_assets/data pvz_assets/eata --out assets/fonts
```

生成 `.fnt`（BMFont 文本格式）+ RGBA8 PNG 字图，Godot 4 可直接作为字体导入（已实测加载为 `FontFile`）。GBK 编码的中文字符正确转为 Unicode codepoint；`BrianneTod32Black` 等变体自动回退使用基础字体图集。GIF 字图需 ffmpeg 转 PNG。

## 音乐 / 音效

MO3 是 BASS 音频库的私有压缩 Tracker 格式，ffmpeg 不支持，`mo3towav.py` 通过 ctypes 调用官方 libbass 解码（游戏本体也用 bass.dll 播放）。获取 libbass：

- 官网 <https://www.un4seen.com/bass.html>
- 或 NuGet 包 `ppy.osu.Framework.NativeLibs`（osu! 同款，含各平台 libbass），nupkg 即 zip，在 `runtimes/<平台>/native/` 下

```bash
python3 mo3towav.py pvz_assets/sounds --out wav_out --bass /path/to/libbass.dylib
ffmpeg -i wav_out/mainmusic.wav -c:a vorbis -strict -2 mainmusic.ogg   # WAV → OGG
ffmpeg -i diamond.au -ac 2 -c:a vorbis -strict -2 diamond.ogg          # AU 音效 → OGG
```

## main.pak 解包说明

PvZ 安装程序是 NSIS 自解压包，用 [7-Zip](https://7-zip.org/) 解开（无需 Windows）：

```bash
# macOS: brew install sevenzip
7zz x 植物大战僵尸中文电脑版安装程序.exe -opvz_extract
python3 unpack_pak.py pvz_extract/main.pak pvz_assets
```

解出约 2400 个文件：

| 目录 | 内容 | 处理方式 |
|---|---|---|
| `compiled/reanim/` | 骨骼动画（143 个） | `convert_all.py` |
| `compiled/particles/` | 粒子特效 / 拖尾（107 个） | `particles2godot.py` |
| `reanim/` | 动画部件贴图（1500+ 张） | 直接用 |
| `images/`、`particles/`、`qarticles/` | 界面图、粒子贴图 | 直接用 |
| `sounds/` | 音效 OGG（直接用）/ MO3 音乐 / AU | `mo3towav.py` / ffmpeg |
| `eata/` + `data/` | 字体描述 txt + 字图 | `font2fnt.py` |
| `properties/`、`data/` | resources.xml、关卡与文案 | 直接读取 |

> 注：`jmages/seanim/eata/qarticles/qroperties` 是中文版 pak 里目录名各字母 +1 的重复目录（如 `eata` ≈ `data`），存放字体等本地化资源。

## 文件格式文档

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
                    string name            (u32 长度 + 字节)
                    u32 magic = 0x2C
                    frame_count × Transform {
                      8 × optional f32     (x y kx ky sx sy f a，值 = -10000 表示缺失)
                      12 字节填充
                    }
                    frame_count × Elements {
                      string image, string font, string text
                    }
                  }
```

语义规则：

- 帧中缺失的字段继承上一帧的值（初始：`pos=(0,0)`、`scale=(1,1)`、`rot/skew=0`、`alpha=1`、可见）
- `kx` 为旋转角（度），`ky` 为 y 轴角度（度）。PVZ 矩阵 x轴基=`(sx·cos kx, sx·sin kx)`、y轴基=`(−sy·sin ky, sy·cos ky)`，与 Godot `Transform2D(rot, scale, skew, pos)` 对比可得 `rotation = kx`、`skew = ky − kx`（精确等价）
- reanim 的 (x, y) 与旋转/缩放均相对**图片左上角**（对应 Godot `Sprite2D.centered = false`）
- `anim_*` 轨道是子动画段落标记：段范围 = 该轨道上 [首个显示帧, 末个显示帧+1)（`f>=0` 为显示）。段标记轨道的显示区间互不重叠、拼满时间线；含图片的 `anim_*` 轨道（如 SunFlower 的 anim_idle = 葵花头、Zombie 的 anim_cone）同时仍是渲染部件
- 循环播放时末帧钳制、回绕瞬间瞬切回首帧，无跨边界插值（对应 Godot `loop_wrap = false`）

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

## 原理参考

本项目为独立重实现，格式与转换思路参考了以下开源项目：

- Reanimator.cpp（PvZ 反编译）— reanim 加载/播放语义的权威参照：空帧逐帧填充、段范围界定、末帧钳制回绕
- [librePvZ/librePvZ](https://github.com/librePvZ/librePvZ)（Rust, AGPL-3.0）— `.reanim.compiled` 二进制解码、贴图命名规则
- [YingFengTingYu/PopStudio](https://github.com/YingFengTingYu/PopStudio)（C#）— 粒子/拖尾 compiled 二进制布局
- [InLiothixi/stabledecompile](https://github.com/InLiothixi/stabledecompile)（PvZ 反编译）— 粒子字段语义、默认值与枚举
- [un4seen BASS](https://www.un4seen.com/) — MO3 解码（通过官方动态库）

## 声明

本工具仅用于学习与研究。《植物大战僵尸》游戏素材版权归 PopCap / EA 所有，请勿将解包素材用于商业用途或公开分发。BASS 音频库有自己的许可条款（非商业用途免费）。

## License

GPL-3.0
