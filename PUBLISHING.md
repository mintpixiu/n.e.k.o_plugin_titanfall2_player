# 发布到 N.E.K.O 插件市场 —— titanfall2_player

这份文档只记录**可复现的事实**和**还差谁来做**。不会把“本地打包成功”说成
“已经上架”。

---

## 1. 当前已验证到什么程度

在本机 `N.E.K.O/` 源码目录下用官方 CLI 执行：

```text
check --release --market-release
  passed
  version=1.0.0
  package=dist/titanfall2_player.neko-plugin
  package_sha256=<每次构建输出，见 CLI>
  payload_hash=<每次构建输出，见 CLI>
  payload_hash_verified=True
  tests=passed
```

> Hash 每次改代码重新构建都会变。市场以 GitHub Release 里由标准 workflow
> 生成的 `market-evidence.json` 为准，不要手工抄写 hash。

本机真实宿主（Steam 版 N.E.K.O，插件服务器 48916）：

| 项目 | 结果 |
|------|------|
| 导入 | `POST /plugin-cli/upload-and-install` 成功，payload hash 校验通过 |
| 启动 | `running`，进程存活 |
| runtime entries | 7 个：play/stop/resume/status/look/selfcheck/missions |
| LLM 工具 | 12 个 `titan_*` 注册到 3 个角色（YUI / Little Wish / 小天） |
| 只读/等待入口 | `titan_status`、`titan_missions`、`titan_play`(等待)、`titan_selfcheck`、`titan_look`、`titan_stop` 均成功 |
| LLM 工具调用 | `titan_command("往前走")` 返回 `accepted=true` |
| 截图 | DXGI / GDI / PIL 三后端均出图 2560×1600 |
| 输入 ABI | `INPUT` 40 字节，`SendInput` 可用 |

**没有验证**：真实《泰坦陨落 2》画面与按键。原因是本机 Steam 库里没有安装
该游戏（只有 Half-Life / Portal 2 等）。这一点在 README「已知限制」里也写了。
因此发布文案不能写“已通关”“实机验证全流程”。

### GitHub 当前状态（已实际完成）

```text
仓库：https://github.com/mintpixiu/n.e.k.o_plugin_titanfall2_player
可见性：public
默认分支：main
首次推送：546b682
最新提交：f5d019a（test: skip stub-only surface checks when real SDK is preloaded）
Verify N.E.K.O Plugin：success（run 40330010626）
Release N.E.K.O Plugin：等待 v1.0.0 tag
```

### 市场当前状态（尚未完成）

**尚未提交首次审核，也尚未生成 Market stable 版本。** 市场登录使用 NEKO 账号中心
（Ory OAuth），需要在网页里用作者账号授权；当前会话没有该浏览器登录态，不能代替
作者点“提交审核”。这是唯一没有自动完成的外部账号步骤。

---

## 2. 市场要求与对应关系

| 市场/审核关注点 | 本插件的处理 |
|----------------|--------------|
| 插件能否加载、entry 是否 async | 7 个 `@plugin_entry` 全部 `async def`；真实宿主启动成功 |
| manifest / entry / ID / 版本 | `id=titanfall2_player`，`entry=plugin.plugins.titanfall2_player:Titanfall2PlayerPlugin`，`version=1.0.0` |
| 是否启用就抢键盘 | `auto_start=false`、`autopilot_enabled=false`；必须 `titan_play` |
| 是否只在前台注入 | `require_foreground=true`；失焦即松手 |
| 是否会在主菜单点新游戏 | 没进过游戏只等待；进过后只 Esc 关暂停；**绝不按 Enter** |
| 是否读内存/改存档/作弊 | 只截屏 + `SendInput`；不读内存、不写存档、不执行控制台 |
| 玩家能否随时接手 | 软接管（100ms 内松手）、F8、`titan_stop` |
| 会话是否失控 | `max_session_minutes=45` 硬停 |
| 依赖是否可复现 | 无第三方 Python 依赖；仅用宿主自带 numpy/Pillow |
| i18n | `i18n/zh-CN.json` + `i18n/en.json`，键集一致 |
| 密钥/隐私 | 不记录对话、不记录截图、不联网上传 |
| 测试 | 48 项离线测试，Linux CI 可跑；测试不会调用真实 `SendInput` |

---

## 3. 还差哪两步（需要作者账号）

### 3.1 GitHub 仓库

市场要求仓库名：

```text
n.e.k.o_plugin_titanfall2_player
```

公开仓库，作者 `mintpixiu`。插件目录本身已经是独立 Git 仓库（`main` 分支）。

```powershell
cd C:\Users\inksa\Documents\deepseek-harness\default-workspace\titanfall2_player
git add .
git commit -m "feat: Titanfall 2 player plugin v1.0.0"
git remote add origin https://github.com/mintpixiu/n.e.k.o_plugin_titanfall2_player.git
git push -u origin main
```

推送后打开仓库 **Actions**，等 `Verify N.E.K.O Plugin` 变绿。标准
`verify.yml` / `release.yml` 已经由 `neko-plugin init` 生成。

### 3.2 市场网页首次审核

1. 打开 <https://market.project-neko.cn/#/upload> 并用作者账号登录。
2. 填仓库地址 `https://github.com/mintpixiu/n.e.k.o_plugin_titanfall2_player`。
3. 点「读取仓库信息」，确认名称、分类、1–5 个标签。
4. 提交首次审核。
5. 在「我的插件」里看检查结果与审核意见。

审核通过后，回到 N.E.K.O 源码根目录：

```powershell
uv run neko-plugin publish C:\Users\inksa\Documents\deepseek-harness\default-workspace\titanfall2_player
```

它会再次跑 release check，确认 HEAD 已推送，创建并推送 `v1.0.0` tag，
等 GitHub Actions 产出：

```text
titanfall2_player.neko-plugin
titanfall2_player.market-release-check.txt
market-evidence.json
```

然后通知 Market 发布 `stable` 版本。

> `publish` **不能代替首次审核**。没有审核通过 + 仓库匹配，市场不会接受。

---

## 4. 建议的市场文案

**名称**：泰坦陨落2 陪玩

**短描述**：
> 猫娘亲自玩《泰坦陨落 2》：看画面、定策略、跑墙射击、召唤泰坦，还会主动解说；你随时可以接手。

**分类建议**：游戏 / 游戏陪玩 / 自动化（按市场实际选项选）

**标签建议**：`泰坦陨落2`、`游戏陪玩`、`跑酷`、`FPS`、`N.E.K.O`

**详细描述**：
> 你手动打开《泰坦陨落 2》并进入关卡后，说「去玩泰坦陨落2」。猫娘会截屏观察
> 橙色任务标记、HUD、中央运动与受伤迹象，主动说明「我看到了什么 / 我判断该做什么 /
> 下一步怎么做」，然后接管键鼠：冲刺、跑墙、二段跳、滑铲、短点射、换弹、按 E、
> 召唤泰坦。她不会启动游戏、不会点新游戏、不会读内存或改存档；你碰一下 WASD
> 就能接手，F8 随时暂停，说「停手」也会立刻松开所有键。
>
> 注意：这是视觉 + 反应式策略 + 对话 LLM 的陪玩，不保证 100% 通关；
> 复杂关卡仍可能需要你或 LLM 介入。首次使用建议先开 `dry_run` 自检。

---

## 5. 审核时可能被追问的点

| 追问 | 回答 |
|------|------|
| 为什么没有真实游戏截图？ | 本机未安装该游戏；视觉阈值是对合成帧 + 公开截图调的。宿主/打包/工具链已实测。 |
| 会不会误按菜单？ | 没进游戏只等待；进过后只 Esc；从不按 Enter；`start_in_game_required=true`。 |
| 玩家怎么抢回控制？ | 物理键轮询（排除自己按住的键），100ms 切片内松手；F8；`titan_stop`。 |
| 会不会读内存？ | 不会。只有 DXGI/GDI/PIL 截图 + SendInput。 |
| 会不会自己启动游戏？ | 不会。`titan_play` 只接管已运行窗口；没有 subprocess/ShellExecute/steam://。 |
| 为什么有 F/Q/G/C 键位说明？ | 公开攻略对可选动作键位有分歧；核心操作稳定，可选键可配置。 |
| 依赖是什么？ | 空 `dependencies`；只用宿主自带 numpy/Pillow，不装 cv2/dxcam。 |
| 能不能多人？ | 不承诺；策略为单人战役写。 |

---

## 6. 版本纪律

* 插件 ID `titanfall2_player` 发布后不变。
* 已发布版本号不能复用；改代码就升版本（1.0.1、1.0.2…）。
* 撤回是单向的；撤回/发布过的版本号都不能再用。
* 发布前固定顺序：改代码 → `check --release` → commit/push → `publish`。
* 本机最终包 hash 以重新构建后的输出为准；市场以 GitHub Release 的
  `market-evidence.json` 为准。

---

## 7. 本机打包命令

```powershell
cd C:\Users\inksa\Documents\deepseek-harness\default-workspace
& .\N.E.K.O\.venv\Scripts\neko-plugin.exe check .\titanfall2_player --release --target-dir .\dist
```

产出：`dist/titanfall2_player.neko-plugin`。

导入验证：

```powershell
curl.exe -X POST "http://127.0.0.1:48916/plugin-cli/upload-and-install?on_conflict=replace" `
  -F "file=@dist/titanfall2_player.neko-plugin;type=application/octet-stream"
```

> `on_conflict=replace` 会替换已安装的同 ID 插件；正常用户用 Plugin Center
> 的「导入 / 升级」，不要直接调 API。
