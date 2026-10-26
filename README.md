# 泰坦陨落2 陪玩 —— N.E.K.O 插件

一个按 **N.E.K.O Plugin SDK v2 / Market** 规范制作的 Python 插件。

装好后，你**先手动打开《泰坦陨落 2》并进入关卡**，对猫娘说：

> 「去玩泰坦陨落2」

她会判断游戏窗口、前台状态、画面里的橙色任务标记、HUD、中央运动与受伤迹象，
然后用猫娘的口吻主动说明：

1. 我现在看到了什么；
2. 我判断当前该做什么；
3. 我下一步准备按什么。

接着她会真的接管键鼠：跑墙、二段跳、滑铲、冲刺、短点射、换弹、按 E、
召唤泰坦、在泰坦里开火/放副武器。你随时可以：

* 直接碰 WASD / 鼠标 / E —— 她在下一次 100 ms 检查里松手，把操作还给你；
* 按 **F8** —— 硬暂停 / 继续；
* 说「停手」「别玩了」「我来」—— 立刻松开所有键；
* 说「继续」「交给你」—— 重新接管；
* 说「往前走」「跳」「按 E」「开火」—— 和她一起操作；
* 说「先打这个」「沿任务标记走」「上泰坦」—— 改她的阶段目标。

> **重要边界（不骗人）**：这是一个「视觉 + 反应式策略 + 对话 LLM」的陪玩插件，
> 不是读内存的外挂，也不是保证 100% 通关的速通脚本。它能持续观察、推进、
> 战斗、跑酷和尝试完成任务，也会主动接受你的口头指挥；但复杂关卡、Boss 机制、
> 剧情解谜仍可能需要 LLM 或你介入。详见文末「已知限制」。

---

## 交付物

| 文件 | 说明 |
|------|------|
| **`dist/titanfall2_player.neko-plugin`** | 可直接在 N.E.K.O「插件管理 → 导入」的包（v1.0.0） |
| [`titanfall2_player/`](.) | 插件源码（11 个 Python 模块 + i18n + 配置 schema + 测试） |
| [`PUBLISHING.md`](PUBLISHING.md) | 市场发布手册：已验证项、剩余账号步骤、审核关注点 |
| [`DESIGN.md`](DESIGN.md) | 架构、策略、实测依据与刻意不做的事 |

---

## 安装与第一次使用

1. 打开 **N.E.K.O → 插件管理 → 导入**，选择 `dist/titanfall2_player.neko-plugin`。
2. 启用「泰坦陨落2 陪玩」。
3. 先点一次 **「泰坦陨落2环境自检」**，确认能截到画面、输入 ABI 正常。
4. **你自己打开《泰坦陨落 2》**（Steam 或 EA App 都行），进入关卡/读档。
5. 对猫娘说「去玩泰坦陨落2」。

插件默认：

```toml
[plugin_runtime]
auto_start = false

[titanfall2]
autopilot_enabled = false
require_foreground = true
start_in_game_required = true
max_session_minutes = 45.0
```

也就是说：**仅仅启用插件不会抢键盘、不会启动游戏、不会点「新游戏」**。
必须显式开始。

> 第一次建议把 `dry_run` 改成 `true` 跑一遍：只观察、解说，不按键；
> 确认她能看见画面、认得菜单，再关掉 dry_run 让她真正玩。

---

## 她怎么“自己玩”

每个 tick（默认 0.30 秒）：

```text
截屏（DXGI → GDI → PIL）
  → 画面特征（亮度/运动/中央局部运动/受伤红边/中央红色/命中亮闪）
  → 橙色任务标记检测（紧凑暖色簇 + 置信度）
  → HUD 稳定性 + 泰坦计量暖色提示
  → 菜单 / 黑屏 / 失焦守卫
  → 反应式策略（赶路 / 战斗 / 泰坦 / 卡住阶梯）
  → 键鼠宏（W+Shift、W+Space、ADS+开火、E、R、V…）
```

同时后台叙事循环会：

* 每 8 秒把一张 JPEG 画面推进对话 LLM 视觉上下文（`ai_behavior="read"`）；
* 每 25 秒推一条「实时战况 + 我的判断 + 下一步」，触发猫娘主动开口
  （`ai_behavior="respond"`）；
* 开始、等待窗口、停手、被玩家接管时各推一条短提示。

### 策略状态

| 状态 | 触发 | 行为 |
|------|------|------|
| `waiting` | 没窗口 / 未进关卡 | 零按键，等待 |
| `menu` | 主菜单 / 暂停菜单 | 零按键；进入过游戏后只按 Esc 关暂停 |
| `blind` | 连续全黑 | 零按键 + 报告真实截图错误 |
| `objective` | 看到橙色任务标记 | 把标记带回画面中心，W+Shift 赶路，周期性跳/滑铲 |
| `combat` | 中央局部运动 / 中央红色 / 受伤 | 瞄准 + ADS 短点射 + 横移 + 换弹 + 战术/副武器 |
| `titan` | LLM 指定 `mode=titan` | 主炮/副武器/冲刺/核心，挨打时后退举盾 |
| `stuck` | 画面与运动长时间不变 | 阶梯：转视角 → 退跳 → 侧跳/跑墙 → E/滑铲 → 召唤泰坦/跳 |
| `paused` | F8 / stop / 玩家硬停 | 松开所有键，不再操作 |

**战斗证据是保守的**：只有「中央局部运动且全局运动不大」「中央红色目标/命中」
「屏幕边缘泛红」「HUD 血量区偏红」才进入战斗，避免把镜头平移误判成敌人。

**任务标记也是保守的**：只用小而紧凑的高饱和暖色簇，超过 10% 画面的大面积橙色
（夕阳、火海）会被拒绝；置信度不足就回到安全前进，不会对着夕阳转圈。

### 键位与跑酷

默认键位按公开 PC 默认值：

```text
WASD 移动 · Shift 冲刺 · Space 跳/二段跳/跑墙 · Ctrl 蹲/滑铲
E 互动/登机 · R 换弹 · LMB 开火 · RMB 瞄准
V 召唤泰坦/泰坦核心 · Q 战术 · G 副武器 · F 近战 · C 强化 · X 弹射
```

> **F / Q / G / C 在公开攻略里有分歧**（不同版本/攻略写法不一致）。
> 插件把核心移动、射击、互动、换弹、召唤泰坦作为主路径；这几个可选动作
> 都可以在 `[titanfall2.keys]` 里覆盖。第一次使用请打开游戏设置核对一次。

跑酷不是「按一下 W 再按一下 Space」：策略用 **chord** 同时按住
`W+Shift`、`W+Space` 或 `W+Shift+Space`，所以跑墙、二段跳和滑铲是连贯的。

---

## 你能对她说什么

| 你说 | 她的行为 |
|------|----------|
| 去玩泰坦陨落2 / 神兵泰坦2 / 你来打泰坦陨落 | 选游戏并接管（没开就等窗口） |
| 往前走 / 后退 / 往左 / 往右 / 冲刺 | 按住对应方向（前进带 Shift） |
| 跳 / 二段跳 / 蹲下 / 滑铲 / 跑墙 | 对应动作；跑墙是 W+Shift+Space 组合 |
| 按 E / 开门 / 登机 / 上泰坦 / 互动 | 按 E（登机是长按） |
| 开火 / 打 / 射击 / 瞄准 / 换弹 | 对应鼠标键与 R |
| 手雷 / 副武器 / 近战 / 战术技能 | Q / G / F / Q |
| 召唤泰坦 / 泰坦核心 / 弹射 | V / V / X |
| 看右边 / 往左转 / 看上面 | 转视角 |
| 停手 / 别玩了 / 我来 / 暂停 | 松开所有键，硬停 |
| 继续 / 你接着玩 / 交给你 | 重新接管 |
| 现在什么情况 / 打到哪了 | `titan_status` / `titan_look` |
| 先打这个 / 沿任务标记走 / 上泰坦 | `titan_directive` 改策略模式 |
| 试一下：跑墙→跳→开火 | `titan_sequence` 执行短序列 |

---

## 工具与入口（SDK 面）

**LLM 工具（12 个）**

| 工具 | 用途 |
|------|------|
| `titan_play` | 开始 / 接管（不启动游戏） |
| `titan_command` | 立即做一个动作 |
| `titan_look` | 立刻截图并送进视觉上下文 |
| `titan_aim` | 转向归一化坐标，可同时开火 |
| `titan_shoot` | 左键开火 / 右键瞄准 |
| `titan_directive` | 设置阶段目标与模式（explore/fight/titan/objective/wait） |
| `titan_sequence` | 最多 12 步、约 8 秒的连续动作 |
| `titan_stop` / `titan_resume` | 停手 / 继续 |
| `titan_status` | 状态、窗口、前台、截图、策略、HUD、错误 |
| `titan_missions` | 9 章战役与一句话打法 |
| `titan_selfcheck` | 只读环境自检 |

**Plugin Center / runtime entries（7 个）**

`titan_play`、`titan_stop`、`titan_resume`、`titan_status`、
`titan_look`、`titan_selfcheck`、`titan_missions`。

所有 `@plugin_entry` 都返回 `Ok` / `Err`；所有 runtime 入口都是 `async def`。

---

## 安全设计

| 风险 | 处理 |
|------|------|
| 打字到别的窗口 | 只有游戏窗口在前台才注入；`require_foreground=true` |
| 游戏最小化 / 1×1 窗口 | 明确拒绝，不按键；提示切回前台 |
| 全黑画面 | 连续 8 帧全黑 → 停止按键，报告真实截图错误 |
| 主菜单乱点 | 没进入过游戏时只等待；进入过后只按 **Esc** 关暂停菜单，**绝不按 Enter 点新游戏** |
| 玩家突然接手 | 轮询 WASD/开火/E/R/Q/F/V/G/C/X/Shift 等物理键（排除自己按住的键）→ 100 ms 内松手；停手 12 秒后自动恢复（可关） |
| 硬暂停 | **F8**；停手后控制循环每 tick 兜底 `release_all()` |
| 会话失控 | `max_session_minutes=45` 硬停 |
| 作弊 / 改存档 | 不读内存、不写存档、不执行控制台命令、不代发成就 |
| 独占全屏截不到 | 自带纯 ctypes DXGI Desktop Duplication，不依赖 cv2/dxcam |
| 键位不确定 | 核心动作走稳定默认；可选动作可配置；自检会列出完整键位 |

---

## 配置

配置文件在 N.E.K.O 插件状态目录：

```text
<storage-root>/plugins/titanfall2_player/config/plugin.toml
```

也可以在 **插件管理 → 泰坦陨落2 陪玩 → 配置** 里改。`config.schema.json`
提供中文标题、说明、范围和下拉选项，改完由 `config_change` 生命周期热重载。

常用键：

| 键 | 默认 | 含义 |
|----|------|------|
| `capture_backend` | `auto` | `auto/dxgi/gdi/pil`，失败后端会被隔离并降级 |
| `target_fps` | `4.0` | 观察频率 |
| `frame_stream_interval_seconds` | `8.0` | 画面推进 LLM 的间隔 |
| `commentary_interval_seconds` | `25.0` | 主动解说间隔 |
| `require_foreground` | `true` | 前台守卫 |
| `autopilot_enabled` | `false` | 是否自动操作（用 `titan_play` 开始更安全） |
| `dry_run` | `false` | 只观察不按键 |
| `max_session_minutes` | `45` | 单次会话上限 |
| `auto_call_titan` | `true` | HUD 泰坦提示稳定时按 V 尝试召唤 |
| `look_units_per_degree` | `11.0` | 鼠标灵敏度换算；转太多就调大 |
| `yield_on_human_input` | `true` | 玩家碰键就松手 |
| `human_input_grace_seconds` | `12` | 玩家停手多久后自动恢复 |
| `pause_hotkey_enabled` | `true` | F8 硬暂停 |
| `start_in_game_required` | `true` | 没进游戏不操作 |
| `[titanfall2.keys]` | 空 | 逐动作覆盖键位 |

---

## 验证结果

### 已完成

```text
离线测试                                            48 passed
官方 CLI check --release --market-release        passed（tests=passed, hash 校验通过）
打包解包 / metadata                                 payload_hash_verified=True
真实 N.E.K.O 宿主导入                               upload-and-install 成功
真实 N.E.K.O 宿主启动                               running，7 entries
LLM 工具注册                                        12 个 titan_* 工具注册到 3 个角色
真实宿主只读/等待工具                               status / missions / play(等待) / selfcheck / look / stop 全部正常
真实宿主 LLM 工具调用                               titan_command 返回 accepted=true
真实截图后端                                        DXGI/GDI/PIL 均出图 2560×1600
输入 ABI                                            INPUT=40 字节
发送器                                              纯 ctypes SendInput，扫描码
GitHub Actions Verify                               success（Linux，48 tests）
GitHub Release v1.0.0                               success（3 个资产）
```

Release：<https://github.com/mintpixiu/n.e.k.o_plugin_titanfall2_player/releases/tag/v1.0.0>

官方 Release 证据：

```text
package_sha256=3938d8525f3e70d603054d7ef7e9cb302bca3eb0effaeaf71379063e5f789650
payload_hash=d9dcb5d14910f5948de8fda24df869b208bb1197729a2b71ab74c8b9538feec8
size=133884
```

### 未完成 / 必须诚实说明

* **本机没有安装《泰坦陨落 2》**（Steam 库里只有 Half-Life、Portal 2 等）。
  因此没有做「真实游戏画面 → 真实按键 → 真实关卡推进」的实机验证。
  当前的视觉阈值是对合成帧 + 公开截图做的，不是对你这台机器上的真实 HUD 调的。
* 因此也不能声称「已通关」「HUD 像素完全适配」。第一次运行请先 `dry_run`，
  再用 `titan_selfcheck` / `titan_look` 校准。
* **市场 stable 版本尚未发布**。GitHub 仓库、CI、v1.0.0 Release 都已完成；
  但市场首次审核需要作者账号在 <https://market.project-neko.cn/#/upload> 登录提交。
  官方通知接口当前返回 `release_repo_mismatch`（Release 未绑定到已通过审核的插件），
  这正是“还差首次审核”的证据。详见 [`PUBLISHING.md`](PUBLISHING.md)。

---

## 已知限制（请先看）

1. **不是读内存的外挂**。插件只能看画面、按键；不知道精确血量、弹药、地图坐标、
   Boss 血条。能读到的数字由 LLM 从截图里读，读不到会明说。
2. **不能保证通关**。它能在没有玩家操作时持续赶路、战斗、跑酷、按 E、尝试召唤泰坦，
   但《泰坦陨落 2》的复杂关卡/Boss 机制需要 LLM 或你介入。工具有
   `titan_directive` / `titan_sequence` 让猫娘把「看画面 → 定策略 → 做组合动作」串起来。
3. **主菜单不自动点**。只在你已经进入关卡后接管；在主菜单会明确让你先读档/进关。
4. **菜单判定依赖游戏的 ClipCursor/光标行为**。独占全屏下信息更少；
   如果她一直说「在菜单」，切无边框窗口，或暂时关掉 `auto_resume_from_menu`。
5. **HUD 数字未 OCR**。`titan_meter` / `health_red` 是颜色/稳定性启发式，不是读数。
6. **任务标记是启发式**。大面积橙色会被拒绝；但极端光照、UI 皮肤、
   分辨率差异仍可能漏检/误检。可以让她用 `titan_look` + `titan_directive` 接管。
7. **键位可能需核对**。尤其 F/Q/G/C；核心操作不受影响，可选动作可在配置里改。
8. **死亡/重生没有自动盲按**。当前只在你说「重生」时按 Space；
   因为分不清死亡画面和过场，自动按 Space 有跳过剧情的风险。
9. **不代发 Steam 成就**。成就是游戏自己授予的。
10. **只支持单人战役**。多人模式可以用同一套键位，但本插件的目标、解说和
    策略是为单人战役写的，不承诺多人表现。

---

## 排障

| 现象 | 先看什么 |
|------|----------|
| 说去玩但没反应 | 游戏是否已进入关卡？看 `titan_status` 的 `menu_active` / `focused` |
| 截不到画面 | 跑 `titan_selfcheck`，看 `capture` 每个后端的真实错误 |
| 按键没反应 | 窗口是否前台？`INPUT` 是否 40 字节？游戏是否在菜单？ |
| 视角转太多/太少 | 调 `look_units_per_degree`，或让她用 `titan_aim` 二次微调 |
| 她一直说“菜单” | 独占全屏/光标可见性误判；切无边框，或关 `auto_resume_from_menu` |
| 她抢键盘 | 按 F8；或保持 `yield_on_human_input=true` |
| 解说太频繁 | 调大 `commentary_interval_seconds` / `frame_stream_interval_seconds` |
| 控制台/日志里是乱码 | 这是 Windows 控制台编码，不是插件消息本身；游戏内/网页里是 UTF-8 |

日志：N.E.K.O → 插件管理 → 该插件 → 日志；错误会带真实异常原文。

---

## 重新打包 / 测试

```powershell
# 官方发布检查（会跑插件内测试 + 构建 + 校验 payload hash）
uv run neko-plugin check C:\path\to\titanfall2_player --release

# 只跑插件内测试
python -m pytest .\titanfall2_player\tests -q

# ruff（与 Market CI 同参数）
uvx ruff==0.12.4 check --ignore-noqa --isolated --no-respect-gitignore `
  --target-version py311 --line-length 120 --select E4,E7,E9,F,I `
  --exclude vendor .\titanfall2_player
```

> 测试的 tripwire：`tests/test_service.py` 用假捕获/假注入器；
> 任何测试都不会调用真实 `SendInput` 或启动游戏。

---

## 许可与致谢

插件代码由本项目编写；截图/按键实现参考了 N.E.K.O 官方 SDK 文档和
同一工作区里经过实机验证的 `source_player` 的通用截图/输入基础设施。
《泰坦陨落 2》及相关商标归 Respawn Entertainment / Electronic Arts 所有；
本插件不包含游戏资源。
