# pvz2godot

将《植物大战僵尸》(PopCap) 的 `.reanim.compiled` 动画文件转换为 **Godot 4** 可直接使用的场景文件 (`.tscn`)。

纯 Python 3 标准库实现，无任何第三方依赖。

## 功能

- 解码 `.reanim.compiled` 二进制动画（含 zlib 压缩外层）
- 自动拆分 `anim_*` 子动画段落（walk / idle / eat / death …）
- 生成 Godot 4 `.tscn`：`Node2D` 根节点 + 每个部件一个 `Sprite2D` + `AnimationPlayer`（含动画库）
- 场景节点默认填入首帧状态，在编辑器中打开即是拼装好的造型
- 自动收集并复制用到的贴图，支持多贴图目录、大小写不敏感匹配

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

## 使用方法

```bash
python3 pvz2godot.py <输入文件或目录> \
    --images <贴图目录> [更多贴图目录...] \
    --out <输出目录> \
    [--res-prefix res://pvz] [--no-loop]
```

示例（从 `main.pak` 解出的资源目录转换全部动画）：

```bash
python3 pvz2godot.py pvz_assets/compiled/reanim \
    --images pvz_assets/reanim pvz_assets/images \
    --out /path/to/godot-project/pvz --res-prefix res://pvz
```

之后在 Godot 中把生成的 `.tscn` 拖入场景，选中 `AnimationPlayer` 播放 `walk`、`idle` 等动画即可。动画默认循环播放，`--no-loop` 可关闭。

### 参数

| 参数 | 说明 |
|---|---|
| `input` | 单个 `.reanim.compiled` 文件，或包含它们的目录（批量） |
| `--images` | 贴图目录，可传多个（PvZ 部件图通常在 `reanim/`，界面图在 `images/`） |
| `--out` | 输出目录，建议直接指向 Godot 项目内 |
| `--res-prefix` | tscn 中引用贴图的 `res://` 前缀，默认 `res://pvz` |
| `--no-loop` | 动画不循环 |

## `.reanim.compiled` 二进制格式

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

## 原理参考

本项目为独立重实现，格式与转换思路参考了以下开源项目：

- [librePvZ/librePvZ](https://github.com/librePvZ/librePvZ)（Rust, AGPL-3.0）— `.reanim.compiled` 二进制解码
- [HYTommm/PVZ_reanim2godot_animation](https://github.com/HYTommm/PVZ_reanim2godot_animation)（C, GPL-3.0）— reanim → Godot 动画的转换逻辑

## 声明

本工具仅用于学习与研究。《植物大战僵尸》游戏素材版权归 PopCap / EA 所有，请勿将解包素材用于商业用途或公开分发。

## License

GPL-3.0
