"""N.E.K.O plugin facade: let the catgirl actually play Titanfall 2.

The facade owns only plumbing: lifecycle wiring, the LLM tool surface and the
Plugin Center entries.  Capture, game discovery, the reactive policy and the
proactive narration all live in
:class:`~plugin.plugins.titanfall2_player.service.TitanfallService`.

Safety defaults (all changeable in ``plugin.toml``):

* the plugin never launches a game -- the user opens it first;
* enabling the plugin does not start playing -- ``titan_play`` does;
* no key is injected unless the game window is in the foreground;
* F8 stops/resumes, and touching WASD/fire makes the catgirl yield immediately.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from . import catalog, prompts
from .service import TitanfallService

# ---------------------------------------------------------------------------
# JSON schemas (module level so tests/metadata see exactly what the LLM sees)
# ---------------------------------------------------------------------------

PLAY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "game": {
            "type": "string",
            "description": (
                "主人说的游戏名，按原话传进来。本插件只支持《泰坦陨落 2》"
                "（Titanfall 2 / 神兵泰坦 2 / 铁驭）。不传也可以：直接接管当前窗口。"
            ),
        },
        "objective": {
            "type": "string",
            "description": "可选：这一局的目标，例如「打到 BT-7274」「清掉这个基地」。",
        },
    },
    "required": [],
}

PLAY_DESCRIPTION = (
    "让猫娘开始玩《泰坦陨落 2》。**游戏必须由主人先手动打开并进入关卡**；"
    "本工具只接管已经运行的窗口，不会自己启动游戏，也不会点「新游戏」。\n\n"
    "调用时机：主人说「去玩泰坦陨落2」「你来打泰坦陨落」「接着打」这类开始/接管要求。\n\n"
    "参数：\n"
    "  game: 主人说的游戏名（原话）；不传就接管当前游戏。\n"
    "  objective: 可选目标；不传就用默认「沿橙色任务标记推进」。\n\n"
    "返回里会带 strategy（怎么打）、running/status 和 9 章战役提示。"
    "如果游戏没开，返回会说明「等待主人手动打开」，不要谎称已经在玩。"
)

COMMAND_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "instruction": {
            "type": "string",
            "description": (
                "主人的原话，例如：往前走 / 跳 / 二段跳 / 滑铲 / 跑墙 / 冲刺 / 按 E / "
                "登机 / 打 / 开火 / 瞄准 / 换弹 / 手雷 / 近战 / 召唤泰坦 / 停手 / 继续。"
                "不要自己补充主人没说的动作。"
            ),
        },
        "seconds": {
            "type": "number",
            "description": "可选：这个动作持续多久；0 表示用默认时长。",
        },
    },
    "required": ["instruction"],
}

COMMAND_DESCRIPTION = (
    "让猫娘在游戏里立刻做一个具体动作（不是改长期目标）。\n\n"
    "调用时机：主人说「往前走一点」「跳过去」「按 E」「开火」「召唤泰坦」"
    "这类**当前画面里的操作**；或者主人中途加入、想和猫娘一起操作时。\n\n"
    "不要用它回答状态问题（用 titan_status）、不要用它开始游戏（用 titan_play）、"
    "不要重复发送同一条已经执行过的指令。\n\n"
    "参数：\n"
    "  instruction: 主人原话；支持前进/后退/左右/跳/二段跳/蹲/滑铲/跑墙/冲刺/"
    "互动/开火/瞄准/换弹/战术/手雷/近战/召唤泰坦/强化/弹射/重生/停手/继续。\n"
    "  seconds: 可选持续秒数。"
)

LOOK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "note": {"type": "string", "description": "可选：为什么要看，只写进日志。"},
    },
    "required": [],
}

LOOK_DESCRIPTION = (
    "立刻截一张当前游戏画面并送进视觉上下文，返回画面特征和 HUD 判断。\n\n"
    "调用时机：主人问「现在什么情况」「打到哪了」「你看到什么」；"
    "或者你自己想确认当前画面/卡住时。不要连续调用很多次。\n\n"
    "注意：插件不会本地 OCR 血量/弹药/泰坦计量数字；请从截图里读，读不到就说读不到。"
    "任务标记、中央运动、屏幕边缘受伤泛红是本地特征，可以引用。"
)

AIM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "x": {
            "type": "number",
            "description": "目标在画面里的横向位置，0=最左，1=最右；也接受像素坐标。",
        },
        "y": {
            "type": "number",
            "description": "目标在画面里的纵向位置，0=最上，1=最下；也接受像素坐标。",
        },
        "fire": {
            "type": "boolean",
            "description": "转过去后是否立刻开火，默认 false。",
        },
        "button": {
            "type": "string",
            "enum": ["left", "right"],
            "description": "开火键；左键开火，右键瞄准/泰坦防御。",
        },
    },
    "required": ["x", "y"],
}

AIM_DESCRIPTION = (
    "把视角转向画面中的某个位置（归一化坐标 0..1），可选马上开火。\n\n"
    "调用时机：你在截图里看到敌人/任务标记/按钮，想精确对准它；"
    "或者 titan_look 之后发现准星不在目标上。\n\n"
    "用法：先 titan_look 看画面 → 估算目标在画面里的 x/y → 调用本工具；"
    "如果没对准，再调用一次微调。\n\n"
    "注意：这是相对转动视角，不是移动鼠标指针；坐标只需大概，二次调用会逐渐收敛。"
)

SHOOT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "button": {
            "type": "string",
            "enum": ["left", "right"],
            "description": "left=开火；right=瞄准/泰坦防御。",
        },
    },
    "required": [],
}

SHOOT_DESCRIPTION = (
    "按一下鼠标键。left=开火，right=瞄准（泰坦里通常是防御/护盾）。\n\n"
    "调用时机：准星已经对准目标，只需要开火；或者你只想点一下右键。"
    "不要在对不准的时候连点，先 titan_aim。"
)

DIRECTIVE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {
            "type": "string",
            "description": "这一阶段的目标，例如「找到 BT」「清掉这个基地」「登上泰坦」。",
        },
        "steps": {
            "type": "string",
            "description": "可选：用中文分号/换行分隔的 1-5 步计划，例如「先清步兵；按 E 开门；沿橙色标记走」。",
        },
        "mode": {
            "type": "string",
            "enum": ["explore", "fight", "titan", "objective", "wait"],
            "description": ("策略模式；赶路用 explore/objective，战斗用 fight，已经上泰坦用 titan，先别动用 wait。"),
        },
        "seconds": {
            "type": "number",
            "description": "这个策略保持多久；0/不传表示默认 30 秒。",
        },
    },
    "required": ["goal"],
}

DIRECTIVE_DESCRIPTION = (
    "设置猫娘这一阶段的**高层目标和打法**（不是单个按键）。\n\n"
    "调用时机：你从截图判断出当前该做什么，例如「先清掉这波步兵」"
    "「沿橙色任务标记去 BT」「现在在泰坦里，先打对面泰坦」；"
    "主人说「你看着办」「去赶路」「上泰坦」也用这个。\n\n"
    "它会更新状态里的 objective/plan，并让本地策略切换模式；"
    "之后你仍可以用 titan_command 做具体动作。\n\n"
    "不要每次解说都调用；只在目标/打法真的变化时调用。"
)

SEQUENCE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "maxItems": 12,
            "description": (
                "按顺序执行的动作。每项 action 可以是：forward/back/left/right/"
                "jump/crouch/slide/sprint/wallrun/interact/reload/fire/aim/tactical/"
                "ordnance/melee/titan/boost/eject/respawn，或 aim（配 x/y）、"
                "shoot（配 button）、chord（配 keys 数组）、wait（配 seconds）。"
                "整个序列最多约 8 秒。"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "seconds": {"type": "number"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "button": {"type": "string", "enum": ["left", "right"]},
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": 'chord 动作要同时按住的键，例如 ["w","space"]。',
                    },
                },
                "required": ["action"],
            },
        }
    },
    "required": ["actions"],
}

SEQUENCE_DESCRIPTION = (
    "执行一小段连续动作（最多 12 步、约 8 秒），适合「跑墙→跳→开火→换弹」"
    "或「瞄准→点射→滑铲」这种组合操作。\n\n"
    "调用时机：你已经从截图想清楚一个短序列，想一次性做完；"
    "或者主人说「你按这个顺序试一下」。\n\n"
    "每一步可以是：\n"
    '  {"action": "forward", "seconds": 1.2}\n'
    '  {"action": "wallrun", "seconds": 0.5}\n'
    '  {"action": "aim", "x": 0.62, "y": 0.40}\n'
    '  {"action": "shoot", "button": "left"}\n'
    '  {"action": "chord", "keys": ["w", "space"], "seconds": 0.3}\n'
    '  {"action": "use"} / jump / reload / wait / titan\n\n'
    "玩家一碰键鼠，序列会很快（通常 0.1–0.4 秒内）停下并把控制权交还。"
)

STATUS_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

STATUS_DESCRIPTION = (
    "查询插件和游戏状态：是否在运行、窗口是否在前台、截图后端、"
    "当前策略状态、泰坦模式、最近动作、任务标记/画面特征、是否被玩家接管、最近错误。\n\n"
    "调用时机：主人问「连上了吗」「现在在干嘛」「怎么没反应」「打到哪了」；"
    "或者你自己不确定当前状态时。"
)

STOP_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

STOP_DESCRIPTION = (
    "让猫娘立刻停手：松开所有按键、停止自动操作，把键盘还给主人。\n\n"
    "调用时机：主人说「别玩了」「停手」「我来」「暂停」「先别打」。"
    "不会关闭游戏，也不会丢存档；之后可以说「继续」或再调 titan_resume。"
)

RESUME_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

RESUME_DESCRIPTION = (
    "让猫娘重新接管已经选定的《泰坦陨落 2》（在 titan_stop 或玩家接手之后）。\n\n"
    "调用时机：主人说「继续」「你接着玩」「交给你」。如果还没开始，先用 titan_play。"
)

MISSIONS_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

MISSIONS_DESCRIPTION = (
    "列出《泰坦陨落 2》的 9 章战役和每章的一句话打法提示。\n\n"
    "调用时机：主人问「这游戏有几关」「下一关是什么」「现在大概到哪了」；"
    "或者你想给主人一个不涉及当前画面的总览。不要假装知道当前存档进度。"
)

SELFCHECK_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

SELFCHECK_DESCRIPTION = (
    "做一次只读环境自检：Steam 库、游戏窗口、前台状态、截图后端逐个探测、"
    "输入结构体、键位、配置。不会按键、不会启动游戏。\n\n"
    "调用时机：主人说「检查一下」「怎么截不到画面」「按键没反应」，"
    "或者第一次使用前确认环境。"
)


# ---------------------------------------------------------------------------
# Tool result helpers
# ---------------------------------------------------------------------------


def _tool_error(code: str, message: str) -> Dict[str, Any]:
    """Return the documented LLM-tool error shape."""
    return {"output": {"reason": message}, "is_error": True, "error": code}


def _ok_or_err(result: Any) -> Any:
    """Map a tool result to ``Ok``/``Err`` for ``@plugin_entry`` handlers."""
    if isinstance(result, dict) and result.get("is_error"):
        output = result.get("output")
        reason = output.get("reason") if isinstance(output, dict) else None
        return Err(SdkError(str(reason or result.get("error") or "tool failed")))
    return Ok(result)


@neko_plugin
class Titanfall2PlayerPlugin(NekoPluginBase):
    """Plugin facade: lifecycle + tool entries only."""

    def __init__(self, ctx):
        super().__init__(ctx)
        log_level = (os.environ.get("NEKO_LOG_LEVEL") or "INFO").strip().upper()
        try:
            self.file_logger = self.enable_file_logging(log_level=log_level)
        except ValueError:
            self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self._cfg: Dict[str, Any] = {}
        self._lang: str = prompts.DEFAULT_LANG
        self._service = TitanfallService(
            logger=self.logger,
            push_message_fn=self.push_message,
            data_dir=self.data_path(),
        )
        self._service_started = False
        self._service_start_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lazy start (host's long-lived loop; never from the startup hook)
    # ------------------------------------------------------------------

    async def _ensure_service_started(self) -> bool:
        async with self._service_start_lock:
            if self._service_started:
                return True
            try:
                await self._service.start()
                self._service_started = True
                return True
            except Exception as exc:
                self.logger.warning(
                    "[startup] service.start failed; will retry — {}: {}",
                    type(exc).__name__,
                    exc,
                )
                return False

    async def _on_command_loop_start(self) -> None:
        try:
            await self._ensure_service_started()
        except Exception as exc:
            self.logger.warning(
                "[eager-start] failed; lazy start remains — {}: {}",
                type(exc).__name__,
                exc,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        raw = cfg.get("titanfall2", {}) if isinstance(cfg.get("titanfall2"), dict) else {}
        self._cfg = raw
        self._lang = prompts.user_lang()
        self._service.set_lang(self._lang)
        self._service.configure(raw)
        installed = self._service.installed_ids()
        running = self._service.running_ids()
        return Ok(
            {
                "status": "ready",
                "lang": self._lang,
                "installed": installed,
                "running": running,
                "autopilot_enabled": self._service.cfg.autopilot_enabled,
                "dry_run": self._service.cfg.dry_run,
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self._service_started:
            try:
                await self._service.stop()
            except Exception as exc:
                self.logger.warning(
                    "[shutdown] service.stop raised — {}: {}",
                    type(exc).__name__,
                    exc,
                )
            self._service_started = False
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def config_change(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        raw = cfg.get("titanfall2", {}) if isinstance(cfg.get("titanfall2"), dict) else {}
        self._cfg = raw
        self._service.configure(raw)
        return Ok(
            {
                "status": "reconfigured",
                "dry_run": self._service.cfg.dry_run,
            }
        )

    # ------------------------------------------------------------------
    # LLM tools
    # ------------------------------------------------------------------

    @llm_tool(
        name="titan_play",
        description=PLAY_DESCRIPTION,
        parameters=PLAY_SCHEMA,
        timeout=30.0,
    )
    async def titan_play_tool(self, *, game: Any = None, objective: Any = "", **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来，看插件日志。")

        installed = self._service.installed_ids()
        # Force a fresh running-game scan before the "no game name" fallback;
        # otherwise a Plugin Center button would see a stale empty list.
        self._service.get_status()
        running = self._service.running_ids()

        profile = catalog.TITANFALL2
        assumed = False
        if isinstance(game, str) and game.strip():
            resolution = catalog.resolve_game(game, installed=installed, running=running)
            if resolution.game is None:
                return {
                    "summary": (
                        f"我只支持《泰坦陨落 2》（用户说：{str(game)[:40]!r}）。"
                        "请主人先手动打开游戏并进入关卡，再说「去玩泰坦陨落2」。"
                    ),
                    "supported": False,
                    "installed": [catalog.GAME_BY_ID[gid].summary() for gid in installed if gid in catalog.GAME_BY_ID],
                }
            profile = resolution.game
            assumed = resolution.assumed

        if not profile.playable:
            return {
                "summary": f"《{profile.name_zh}》不支持自动游玩。",
                "supported": False,
            }

        result = self._service.play(profile, objective=str(objective or "").strip())
        status = result["status"]
        running_now = bool(result.get("running"))
        menu_now = bool(status.get("menu_active"))
        assumption = ""
        if assumed:
            assumption = (
                f"（我把「{str(game)[:30]}」理解成《{profile.name_zh}》；如果主人指的是别的作品，告诉我具体名字。）"
            )
        if running_now and menu_now:
            summary = (
                f"已经找到《{profile.name_zh}》的窗口，但画面停在主菜单/暂停菜单。"
                "请主人先点进关卡或读档；进入游戏后我就接手。"
                "**我不会替你点「新游戏」覆盖进度。**"
                f"{assumption}"
            )
        elif running_now:
            summary = (
                f"已接管《{profile.name_zh}》的窗口，目标：{result['objective']}。"
                f"{assumption} 我开始看画面、按自己的判断推进；主人随时可以接手。"
            )
        else:
            summary = (
                f"已选定《{profile.name_zh}》，但还没检测到它的窗口。"
                f"请主人**手动打开《{profile.name_zh}》并进入关卡/读档**；"
                "窗口出现后我会自动开始，不会再催指令。"
                f"{assumption}（这不是故障；EA App 版只要窗口出现也能接管。）"
            )
        if self._service.cfg.dry_run:
            summary += " 现在是 dry_run 演练模式：只观察、不按键。"
        return {
            "summary": summary,
            "game": profile.summary(),
            "strategy": profile.strategy_brief(max_chapters=9),
            "chapters": [chapter.as_dict() for chapter in profile.chapters],
            "waiting_for_game": not running_now,
            "assumed": assumed,
            "running": status.get("running"),
            "status": status,
        }

    @llm_tool(
        name="titan_command",
        description=COMMAND_DESCRIPTION,
        parameters=COMMAND_SCHEMA,
        timeout=20.0,
    )
    async def titan_command_tool(self, *, instruction: Any = None, seconds: Any = 0, **_):
        if not isinstance(instruction, str) or not instruction.strip():
            return _tool_error(
                "INVALID_ARGUMENT",
                "titan_command 需要一句具体操作，比如「往前走」「跳」「按 E」。",
            )
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来，看插件日志。")
        try:
            seconds_value = float(seconds) if seconds not in (None, "") else 0.0
        except (TypeError, ValueError):
            seconds_value = 0.0
        return self._service.apply_command(instruction.strip(), seconds=seconds_value)

    @llm_tool(
        name="titan_look",
        description=LOOK_DESCRIPTION,
        parameters=LOOK_SCHEMA,
        timeout=25.0,
    )
    async def titan_look_tool(self, *, note: Any = "", **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来，看插件日志。")
        return self._service.look(note=str(note or ""))

    @llm_tool(
        name="titan_aim",
        description=AIM_DESCRIPTION,
        parameters=AIM_SCHEMA,
        timeout=20.0,
    )
    async def titan_aim_tool(
        self,
        *,
        x: Any = 0.5,
        y: Any = 0.5,
        fire: Any = False,
        button: Any = "left",
        **_,
    ):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        try:
            xf, yf = float(x), float(y)
        except (TypeError, ValueError):
            return _tool_error("INVALID_ARGUMENT", "x/y 需要是数字（0..1 或像素坐标）。")
        if not self._service.get_status().get("running"):
            return {"summary": "还没有正在运行的游戏窗口；先让主人打开游戏并 titan_play。"}
        return self._service.aim(xf, yf, fire=bool(fire), button=str(button or "left"))

    @llm_tool(
        name="titan_shoot",
        description=SHOOT_DESCRIPTION,
        parameters=SHOOT_SCHEMA,
        timeout=15.0,
    )
    async def titan_shoot_tool(self, *, button: Any = "left", **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        if not self._service.get_status().get("running"):
            return {"summary": "还没有正在运行的游戏窗口；先让主人打开游戏并 titan_play。"}
        return self._service.shoot(str(button or "left"))

    @llm_tool(
        name="titan_directive",
        description=DIRECTIVE_DESCRIPTION,
        parameters=DIRECTIVE_SCHEMA,
        timeout=20.0,
    )
    async def titan_directive_tool(
        self,
        *,
        goal: Any = None,
        steps: Any = "",
        mode: Any = "explore",
        seconds: Any = 0,
        **_,
    ):
        if not isinstance(goal, str) or not goal.strip():
            return _tool_error(
                "INVALID_ARGUMENT",
                "titan_directive 需要一句目标，比如「沿标记去 BT」。",
            )
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        try:
            seconds_value = float(seconds) if seconds not in (None, "") else 0.0
        except (TypeError, ValueError):
            seconds_value = 0.0
        return self._service.set_directive(
            mode=str(mode or "explore"),
            goal=goal.strip(),
            steps=str(steps or ""),
            seconds=seconds_value,
        )

    @llm_tool(
        name="titan_sequence",
        description=SEQUENCE_DESCRIPTION,
        parameters=SEQUENCE_SCHEMA,
        timeout=25.0,
    )
    async def titan_sequence_tool(self, *, actions: Any = None, **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        return await self._service.run_sequence(actions)

    @llm_tool(
        name="titan_stop",
        description=STOP_DESCRIPTION,
        parameters=STOP_SCHEMA,
        timeout=15.0,
    )
    async def titan_stop_tool(self, **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        if self._service._game is None:  # noqa: SLF001 - cheap state check
            return {"summary": "现在还没有在玩；不用停。"}
        status = self._service.stop_playing(reason="tool")
        return {
            "summary": "停手了，所有按键都松开了。想继续就说「继续」。",
            "status": status,
        }

    @llm_tool(
        name="titan_resume",
        description=RESUME_DESCRIPTION,
        parameters=RESUME_SCHEMA,
        timeout=15.0,
    )
    async def titan_resume_tool(self, **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        return self._service.resume_playing(reason="tool")

    @llm_tool(
        name="titan_status",
        description=STATUS_DESCRIPTION,
        parameters=STATUS_SCHEMA,
        timeout=15.0,
    )
    async def titan_status_tool(self, **_):
        try:
            return self._service.get_status()
        except Exception as exc:
            self.logger.exception("[status] failed")
            return _tool_error("INTERNAL", type(exc).__name__)

    @llm_tool(
        name="titan_missions",
        description=MISSIONS_DESCRIPTION,
        parameters=MISSIONS_SCHEMA,
        timeout=10.0,
    )
    async def titan_missions_tool(self, **_):
        profile = catalog.TITANFALL2
        return {
            "summary": (
                f"《{profile.name_zh}》有 {len(profile.chapters)} 章战役："
                + "、".join(chapter.title for chapter in profile.chapters)
                + "。当前存档进度无法从画面外得知；我只能根据你看到的画面判断。"
            ),
            "missions": [chapter.as_dict() for chapter in profile.chapters],
            "strategy": profile.strategy,
        }

    @llm_tool(
        name="titan_selfcheck",
        description=SELFCHECK_DESCRIPTION,
        parameters=SELFCHECK_SCHEMA,
        timeout=45.0,
    )
    async def titan_selfcheck_tool(self, **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        checks = await self._service.selfcheck()
        problems = checks.get("problems") or []
        summary = "自检通过。" if not problems else ("自检发现问题：" + "；".join(problems[:4]))
        return {"summary": summary, "checks": checks}

    # ------------------------------------------------------------------
    # Plugin Center entries (GUI actions / intent dispatch)
    # ------------------------------------------------------------------

    @ui.action(
        id="titan_play",
        label="让猫娘玩泰坦陨落2",
        icon="play",
        tone="primary",
        order=10,
    )
    @plugin_entry(
        id="titan_play",
        name="开始玩泰坦陨落2",
        description=(
            "让猫娘接管已经打开的《泰坦陨落 2》。**不会自己启动游戏**："
            "主人先手动打开并进入关卡，然后说「去玩泰坦陨落2」。"
            "调用后如果游戏还没开，插件会等待窗口出现。"
        ),
        input_schema=PLAY_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def titan_play_entry(self, *, game: Any = None, objective: Any = "", **_):
        result = await self.titan_play_tool(game=game, objective=objective)
        return _ok_or_err(result)

    @ui.action(
        id="titan_stop",
        label="让猫娘停手",
        icon="stop",
        tone="danger",
        order=20,
    )
    @plugin_entry(
        id="titan_stop",
        name="让猫娘停手",
        description=(
            "立刻停止自动操作并松开所有按键，把键盘还给主人。适用于「别玩了」「停手」「我来」「暂停」。不会关闭游戏。"
        ),
        input_schema=STOP_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def titan_stop_entry(self, **_):
        result = await self.titan_stop_tool()
        return _ok_or_err(result)

    @plugin_entry(
        id="titan_resume",
        name="让猫娘继续玩",
        description=(
            "在停手/玩家接手之后，让猫娘重新接管已经选定的《泰坦陨落 2》。适用于「继续」「你接着玩」「交给你」。"
        ),
        input_schema=RESUME_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def titan_resume_entry(self, **_):
        result = await self.titan_resume_tool()
        return _ok_or_err(result)

    @ui.action(
        id="titan_status",
        label="刷新状态",
        icon="refresh",
        order=30,
    )
    @plugin_entry(
        id="titan_status",
        name="查询泰坦陨落2插件状态",
        description=(
            "查询游戏是否运行、窗口是否在前台、截图后端、策略状态、泰坦模式、最近动作、玩家是否接手、最近错误。"
        ),
        input_schema=STATUS_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def titan_status_entry(self, **_):
        status = await self.titan_status_tool()
        return _ok_or_err(status)

    @plugin_entry(
        id="titan_look",
        name="看一眼当前游戏画面",
        description=("截一张当前游戏画面并送进视觉上下文，返回画面特征。适用于「现在什么情况」「你看到什么」。"),
        input_schema=LOOK_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def titan_look_entry(self, *, note: Any = "", **_):
        result = await self.titan_look_tool(note=note)
        return _ok_or_err(result)

    @plugin_entry(
        id="titan_selfcheck",
        name="泰坦陨落2环境自检",
        description=("只读自检：Steam 库、游戏窗口、前台、截图后端、输入结构体、键位、配置。不会按键、不会启动游戏。"),
        input_schema=SELFCHECK_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def titan_selfcheck_entry(self, **_):
        result = await self.titan_selfcheck_tool()
        return _ok_or_err(result)

    @plugin_entry(
        id="titan_missions",
        name="查看战役章节",
        description=("列出 9 章战役和每章一句话打法提示。适用于「这游戏有几关」「下一关是什么」。"),
        input_schema=MISSIONS_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def titan_missions_entry(self, **_):
        result = await self.titan_missions_tool()
        return _ok_or_err(result)
