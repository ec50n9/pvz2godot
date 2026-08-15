# reanim2godot

PvZ1（植物大战僵尸）原版 compiled reanim 动画 → Godot 4 cutout 场景（.tscn）转换器。

```bash
# 转一个动画（僵尸行走/啃食/死亡全在里面）
python3 convert.py pvz_assets/compiled/reanim/Zombie.reanim.compiled \
    pvz_assets/reanim \
    out/zombie.tscn \
    res://assets/art/reanim/zombies/textures

# 校验转换结果是否与原版逐帧一致（退出码 0 = 通过）
python3 verify.py pvz_assets/compiled/reanim/Zombie.reanim.compiled out/zombie.tscn
```

生成的 `.tscn` 直接拖进 Godot 项目即可用：根节点 Node2D + 每个部件一个 Sprite2D（文件顺序即绘制顺序）+ AnimationPlayer，每个 reanim 标签（`anim_walk`/`anim_eat`/`anim_death`…）切分为独立动画：

```gdscript
var zombie = load("res://assets/art/reanim/zombies/zombie.tscn").instantiate()
add_child(zombie)
zombie.get_node("AnimationPlayer").play("walk")
```

## 安装

- Python 3.9+，无强制依赖
- 可选：`pip install Pillow` —— 解包素材中「jpg + 灰度遮罩 png」形态的贴图需要 Pillow 合成 RGBA；未安装时退化为不透明 jpg

## 输入素材从哪来

- `.reanim.compiled`：PvZ1 main.pak 解包产物（可用 [pvz2godot](https://github.com/ec50n9/pvz2godot) 的 `unpack_pak.py` 解包）
- 贴图：解包目录下的 `reanim/`（拆件）、`seanim/`、`images/`、`jmages/` 等平行目录，支持三种形态：
  1. 普通 `.png`：直接用
  2. `.jpg` + 灰度 `<名>_.png`：jpg 存 RGB、遮罩存 alpha，自动合成 RGBA png
  3. 单独的 `.jpg`：无透明，直接用

## 批量转换

```bash
# 全量转换，按用途分组（plants/zombies/credits/zengarden/items/misc）
python3 convert_all.py pvz_assets/compiled/reanim assets/art/reanim \
    --tex pvz_assets/reanim pvz_assets/seanim pvz_assets/images

# 只转文件名含 balloon 的
python3 convert_all.py pvz_assets/compiled/reanim assets/art/reanim balloon \
    --tex pvz_assets/reanim

# 只转 49 种植物（或按子串过滤，注意过滤词放在 --tex 之前）
python3 convert_plants.py pvz_assets/compiled/reanim assets/art/reanim/plants \
    pea sun wall --tex pvz_assets/reanim

# 批量校验整个输出目录
python3 verify.py --all pvz_assets/compiled/reanim assets/art/reanim
```

## 原版语义保证

转换不是近似拟合，而是逐帧等价还原 Reanimator.cpp 的加载/播放语义：

- **空帧逐帧填充**：reanim 空帧 = 沿用前一帧的值，加载时填充，播放时仅在相邻帧间插值
- **成对出键**：在每个变化点前后各出一个键，Godot 线性插值只发生在该 1 帧内，与原版一致
- **`loop_wrap = false`**：原版循环末帧钳制、回绕瞬切首帧；开启回绕插值会导致一次性动画（如 rake/splash）闪变
- **离散通道段首锚定**：visible/texture 由全局进位决定，与播哪段无关；不锚定会在切换动画后残留状态
- **标签切分**：`anim_*` 标签轨道的显示区间即段范围；无标签文件（SunFlower 等）整线一段，首尾闭合才判定为循环
- **同名轨道去重**：reanim 存在同名轨道（WinterMelon 的 frontleaf_tip_left 等），Godot NodePath 只命中第一个，自动改名为 `name__2`
- **运行时管理轨道**：配饰（铁桶/路障/纱门）与断臂/掉头部件的显隐由游戏代码控制，不生成 visible 动画轨道（见 `convert.py` 的 `RUNTIME_MANAGED_TRACKS`）

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `reanim_parse.py` | compiled reanim 二进制解析器（含 zlib 压缩头处理、标签/部件轨道切分） |
| `convert.py` | 单文件转换器，核心逻辑 |
| `convert_all.py` | 全量批量转换，按用途分组 |
| `convert_plants.py` | 49 种植物批量转换 |
| `verify.py` | 转换正确性校验：对 tscn 密集采样与原版逐帧语义比对 |
