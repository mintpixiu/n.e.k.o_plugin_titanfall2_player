"""Titanfall 2 combat/parkour policy: frame features -> input primitives.

This is a *reactive* policy, not a speedrun script.  It keeps the catgirl moving,
looking, firing, reloading and interacting between LLM turns, while the dialogue
LLM (which sees the pushed screenshots) supplies the mission-level plan and can
override everything with a :class:`Directive`.

Design rules:

* never press keys on a black frame -- "blind" is its own state;
* "cannot see health" is not "health is zero";
* movement keys and jump/sprint are sent as a **chord**, because pressing W and
  releasing it before Space is not parkour;
* combat evidence is conservative (centre-localised motion, centre red, damage
  flash), so a camera pan is not mistaken for an enemy;
* the orange campaign waypoint is used to steer, but a weak detection falls back
  to safe forward exploration rather than spinning at a false positive;
* every action carries a short Chinese ``intent`` so the catgirl can narrate
  what she is doing instead of inventing a reason afterwards.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from . import catalog
from .hud import HudState
from .vision import FrameFeatures

__all__ = [
    "Command",
    "Decision",
    "Directive",
    "LookCalibration",
    "Op",
    "PlayPolicy",
    "PolicyState",
    "parse_instruction",
]


class PolicyState(str, Enum):
    WAITING = "waiting"
    MENU = "menu"
    BLIND = "blind"
    EXPLORE = "explore"
    OBJECTIVE = "objective"
    COMBAT = "combat"
    TITAN = "titan"
    STUCK = "stuck"
    RESPAWN = "respawn"
    PAUSED = "paused"


@dataclass
class Op:
    """One input primitive; the service executes these sequentially."""

    kind: str  # tap | hold | chord | wait | look | click | mouse_down | mouse_up
    key: str = ""
    seconds: float = 0.05
    dx: float = 0.0
    dy: float = 0.0
    button: str = "left"
    keys: tuple[str, ...] = ()

    def summary(self) -> str:
        if self.kind == "wait":
            return f"等 {self.seconds:.2f}s"
        if self.kind == "look":
            return f"转视角({self.dx:.0f},{self.dy:.0f})"
        if self.kind in ("click", "mouse_down", "mouse_up"):
            return f"{self.kind} {self.button}"
        if self.kind == "chord":
            return f"同时按 {'+'.join(self.keys)} {self.seconds:.2f}s"
        if self.kind == "hold":
            return f"按住 {self.key} {self.seconds:.2f}s"
        if self.kind == "tap":
            return f"点 {self.key}"
        return f"{self.kind} {self.key}"


@dataclass
class LookCalibration:
    """Mouse counts needed for one degree of view rotation.

    Titanfall 2 uses a modified Source engine, so the same closed-loop method
    applies: the default is a calibration starting point (roughly the common
    800-DPI / in-game-sensitivity range), not a claim about the user's exact
    settings.  Aiming is relative and repeated nudges converge; the user can
    tune ``look_units_per_degree`` in the config.
    """

    units_per_degree: float = 11.0
    horizontal_fov: float = 90.0
    vertical_fov: float = 59.0
    invert_y: bool = False

    def counts_for_delta(self, ndc_x: float, ndc_y: float) -> tuple[float, float]:
        """Convert a normalised screen offset from centre into mouse counts."""
        dx_deg = (float(ndc_x) - 0.5) * self.horizontal_fov
        dy_deg = (float(ndc_y) - 0.5) * self.vertical_fov
        dy = dy_deg * self.units_per_degree
        if self.invert_y:
            dy = -dy
        return dx_deg * self.units_per_degree, dy

    def turn_counts(self, degrees: float) -> float:
        return float(degrees) * self.units_per_degree


@dataclass
class Directive:
    """A time-boxed override from the LLM/user."""

    mode: str
    text: str = ""
    seconds: float = 0.0
    command: "Command | None" = None
    issued_at: float = field(default_factory=time.time)
    source: str = "user"

    def expired(self, now: float | None = None) -> bool:
        if self.seconds <= 0:
            return False
        return (now if now is not None else time.time()) - self.issued_at > self.seconds

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "text": self.text,
            "seconds": self.seconds,
            "source": self.source,
            "command": self.command.action if self.command else None,
        }


@dataclass
class Command:
    kind: str  # action | mode | stop | resume | wait | look_at | move | respawn
    action: str = ""
    seconds: float = 0.6
    text: str = ""
    recognized: bool = True

    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action,
            "seconds": self.seconds,
            "text": self.text,
            "recognized": self.recognized,
        }


@dataclass
class Decision:
    state: PolicyState
    intent: str
    ops: list[Op] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.5

    def summary(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "intent": self.intent,
            "ops": [op.summary() for op in self.ops],
            "reasons": list(self.reasons),
            "confidence": round(self.confidence, 2),
        }


# ---------------------------------------------------------------------------
# Natural-language instruction parsing
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec|seconds?)", re.I)

_STOP_WORDS = (
    "别玩",
    "不玩",
    "停手",
    "停下",
    "停止",
    "暂停",
    "我来",
    "我接手",
    "让我来",
    "关掉",
    "别动",
    "takeover",
    "take over",
    "stop",
)
_RESUME_WORDS = (
    "继续",
    "接着玩",
    "你继续",
    "你来",
    "交给你",
    "接着打",
    "恢复",
    "resume",
    "continue",
)
_WAIT_WORDS = ("等一下", "等等", "停一下", "先别动", "站住", "待机", "不要动", "wait")
_TITAN_WORDS = (
    "泰坦",
    "召唤",
    "叫泰坦",
    "上泰坦",
    "核心",
    "titan",
    "call titan",
    "titanfall",
    "core",
)
_FIGHT_WORDS = ("战斗", "清怪", "打架", "开打", "打怪", "干掉", "敌人", "fight")
_EXPLORE_WORDS = ("探索", "找路", "赶路", "沿路", "前进", "explore", "traverse")
_OBJECTIVE_WORDS = ("目标", "任务", "标记", "objective", "waypoint")
_MOVE_WORDS: dict[str, tuple[str, ...]] = {
    "forward": (
        "前进",
        "往前走",
        "向前",
        "往前",
        "走",
        "冲",
        "跑",
        "follow",
        "跟着",
        "forward",
    ),
    "back": ("后退", "往后", "退后", "撤", "躲", "back"),
    "left": ("往左", "向左", "左移", "左闪", "left"),
    "right": ("往右", "向右", "右移", "右闪", "right"),
    "jump": ("二段跳", "跳跃", "跳", "jump", "space"),
    "crouch": ("滑铲", "蹲下", "蹲", "crouch", "slide", "ctrl"),
    "sprint": ("冲刺", "加速", "奔跑", "sprint", "shift"),
    "wallrun": ("跑墙", "壁走", "飞檐走壁", "wallrun", "wall run"),
    "interact": (
        "按e",
        "使用",
        "开门",
        "按按钮",
        "按钮",
        "捡",
        "拿",
        "拾取",
        "互动",
        "登机",
        "进入泰坦",
        "use",
        "interact",
        "embark",
    ),
    "reload": ("换弹", "装弹", "reload"),
    "fire": ("开火", "射击", "攻击", "打", "射", "fire", "shoot", "attack"),
    "aim": ("瞄准", "右键", "开镜", "aim", "ads"),
    "tactical": ("战术", "技能", "隐身", "钩爪", "脉冲刀", "tactical"),
    "ordnance": ("手雷", "副武器", "火箭", "榴弹", "ordnance", "grenade"),
    "melee": ("近战", "拳", "处决", "melee"),
    "boost": ("强化", "boost"),
    "eject": ("弹射", "脱离", "eject"),
    "respawn": ("重生", "复活", "respawn"),
}


def _duration(text: str, default: float) -> float:
    match = _DURATION_RE.search(text)
    if not match:
        return default
    try:
        return max(0.08, min(8.0, float(match.group(1))))
    except ValueError:
        return default


def parse_instruction(text: str, *, seconds: float = 0.0) -> Command:
    """Map a free-form Chinese/English instruction onto one command.

    The dialogue LLM is told to pass the user's own wording through.  Missing a
    phrase is worse than accepting a broad alias; unrecognised text comes back
    with ``recognized=False`` so the caller can ask for a rephrase.
    """
    raw = str(text or "").strip()
    body = re.sub(r"\s+", "", raw.lower())
    if not body:
        return Command("action", "", 0.0, raw, recognized=False)

    duration = _duration(body, 0.0) or float(seconds or 0.0) or 0.6

    exact = {
        "forward": ("move", "forward"),
        "back": ("move", "back"),
        "left": ("move", "left"),
        "right": ("move", "right"),
        "jump": ("action", "jump"),
        "crouch": ("action", "crouch"),
        "slide": ("action", "crouch"),
        "sprint": ("action", "sprint"),
        "wallrun": ("action", "wallrun"),
        "interact": ("action", "interact"),
        "use": ("action", "interact"),
        "reload": ("action", "reload"),
        "fire": ("action", "fire"),
        "shoot": ("action", "fire"),
        "aim": ("action", "aim"),
        "tactical": ("action", "tactical"),
        "ordnance": ("action", "ordnance"),
        "melee": ("action", "melee"),
        "titan": ("action", "titan"),
        "boost": ("action", "boost"),
        "eject": ("action", "eject"),
        "respawn": ("respawn", "respawn"),
        "wait": ("wait", "wait"),
    }
    if body in exact:
        kind, action = exact[body]
        return Command(kind, action, max(duration, 0.08), raw)

    if any(word in body for word in _STOP_WORDS):
        return Command("stop", "stop", 0.0, raw)
    if any(word in body for word in _RESUME_WORDS):
        return Command("resume", "resume", 0.0, raw)
    if any(word in body for word in _WAIT_WORDS):
        return Command("wait", "wait", max(duration, 1.2), raw)

    if any(word in body for word in _FIGHT_WORDS) and not any(word in body for word in ("前面", "这个", "那只")):
        return Command("mode", "fight", max(duration, 20.0), raw)
    if "titanmode" in body or "泰坦模式" in body or "泰坦形态" in body:
        return Command("mode", "titan", max(duration, 30.0), raw)
    if any(word in body for word in _OBJECTIVE_WORDS):
        return Command("mode", "objective", max(duration, 30.0), raw)
    if any(word in body for word in _EXPLORE_WORDS) and not any(word in body for word in ("走", "跑", "冲")):
        return Command("mode", "explore", max(duration, 20.0), raw)

    # Look directions first: "往右看" is a turn, not a strafe.
    if "看" in body or "转" in body or "视角" in body:
        if "左" in body:
            return Command("look_at", "turn_left", max(duration, 0.35), raw)
        if "右" in body:
            return Command("look_at", "turn_right", max(duration, 0.35), raw)
        if "上" in body or "头顶" in body:
            return Command("look_at", "look_up", max(duration, 0.3), raw)
        if "下" in body:
            return Command("look_at", "look_down", max(duration, 0.3), raw)

    # Specific verbs before generic "打" (so "打泰坦" calls the Titan).
    if any(word in body for word in _TITAN_WORDS) and any(
        word in body for word in ("召唤", "叫", "上", "核心", "call", "core")
    ):
        return Command("action", "titan", 0.12, raw)
    for action in (
        "respawn",
        "wallrun",
        "tactical",
        "ordnance",
        "melee",
        "boost",
        "eject",
    ):
        if any(word in body for word in _MOVE_WORDS[action]):
            return Command("action", action, max(0.12, duration), raw)

    # Movement / generic actions.  Longer phrases first so "往前走" wins over
    # the bare "走" and "蹲下" over "下".
    for action in (
        "jump",
        "crouch",
        "sprint",
        "interact",
        "reload",
        "aim",
        "fire",
        "left",
        "right",
        "back",
        "forward",
    ):
        if any(word in body for word in _MOVE_WORDS[action]):
            hold = 0.12 if action in ("fire", "jump", "interact", "reload", "aim") else max(0.15, duration)
            return Command(
                "move" if action in ("forward", "back", "left", "right") else "action",
                action,
                hold,
                raw,
            )

    return Command("action", "", 0.0, raw, recognized=False)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class PlayPolicy:
    """Reactive Titanfall 2 state machine shared by autopilot and commands."""

    def __init__(
        self,
        game: catalog.GameProfile,
        *,
        look: LookCalibration | None = None,
        key_overrides: Mapping[str, str] | None = None,
        seed: int = 7,
        auto_call_titan: bool = True,
    ) -> None:
        self.game = game
        self.look = look or LookCalibration()
        self.keys = self._build_keys(key_overrides)
        self.auto_call_titan = bool(auto_call_titan)
        self._random = random.Random(seed)
        self.state = PolicyState.WAITING
        self.directive: Directive | None = None
        self._forced_until = 0.0
        self._forced_mode = ""
        self._last_hash = 0
        self._same_scene_since = time.time()
        self._last_motion_at = time.time()
        self._last_use_at = 0.0
        self._last_fire_at = 0.0
        self._last_reload_at = 0.0
        self._last_tactical_at = 0.0
        self._last_ordnance_at = 0.0
        self._last_jump_at = 0.0
        self._last_slide_at = 0.0
        self._last_titan_call_at = 0.0
        self._last_core_at = 0.0
        self._stuck_level = 0
        self._stuck_since = 0.0
        self._explore_phase = 0
        self._explore_turn = 1
        self._last_intent = ""
        self._shots = 0
        self.ticks = 0

    # -- configuration ---------------------------------------------------

    def _build_keys(self, overrides: Mapping[str, str] | None) -> dict[str, str]:
        keys = {action: self.game.key(action, overrides) for action in self.game.keys}
        if overrides:
            keys.update({k: v for k, v in overrides.items() if v})
        return keys

    def set_game(
        self,
        game: catalog.GameProfile,
        *,
        key_overrides: Mapping[str, str] | None = None,
    ) -> None:
        self.game = game
        self.keys = self._build_keys(key_overrides)
        self.reset()

    def set_auto_call_titan(self, enabled: bool) -> None:
        self.auto_call_titan = bool(enabled)

    def reset(self) -> None:
        self.state = PolicyState.WAITING
        self.directive = None
        self._forced_until = 0.0
        self._forced_mode = ""
        self._last_hash = 0
        self._same_scene_since = time.time()
        self._last_motion_at = time.time()
        self._last_use_at = 0.0
        self._last_fire_at = 0.0
        self._last_reload_at = 0.0
        self._last_tactical_at = 0.0
        self._last_ordnance_at = 0.0
        self._last_jump_at = 0.0
        self._last_slide_at = 0.0
        self._last_titan_call_at = 0.0
        self._last_core_at = 0.0
        self._stuck_level = 0
        self._stuck_since = 0.0
        self._explore_phase = 0
        self._explore_turn = 1
        self._last_intent = ""
        self._shots = 0
        self.ticks = 0

    def set_directive(self, directive: Directive | None) -> None:
        self.directive = directive
        if directive is not None and directive.mode in (
            "explore",
            "fight",
            "titan",
            "objective",
            "wait",
        ):
            self._forced_mode = directive.mode
            self._forced_until = time.time() + (directive.seconds or 30.0)

    def clear_directive(self) -> None:
        self.directive = None
        self._forced_mode = ""
        self._forced_until = 0.0

    # -- helpers ---------------------------------------------------------

    def _key(self, action: str) -> str:
        return self.keys.get(action, "") or ""

    def _look_ops(
        self,
        ndc_x: float,
        ndc_y: float,
        *,
        scale: float = 1.0,
    ) -> list[Op]:
        dx, dy = self.look.counts_for_delta(ndc_x, ndc_y)
        return [Op("look", dx=dx * scale, dy=dy * scale)]

    def _turn_ops(self, degrees: float) -> list[Op]:
        return [Op("look", dx=self.look.turn_counts(degrees), dy=0.0)]

    def _tap(self, action: str, *, hold: float = 0.08) -> list[Op]:
        key = self._key(action)
        if not key:
            return []
        return [Op("tap", key=key, seconds=hold)]

    def _hold(self, action: str, seconds: float) -> list[Op]:
        key = self._key(action)
        if not key:
            return []
        return [Op("hold", key=key, seconds=seconds)]

    def _chord(self, actions: tuple[str, ...], seconds: float) -> list[Op]:
        keys = tuple(self._key(action) for action in actions if self._key(action))
        if not keys:
            return []
        return [Op("chord", keys=keys, seconds=seconds)]

    def _fire(self, *, hold: float = 0.14) -> list[Op]:
        return self._tap("attack", hold=hold)

    def _aim(self, *, hold: float = 0.16) -> list[Op]:
        return self._hold("aim", hold)

    def _aim_fire(self, *, hold: float = 0.28) -> list[Op]:
        """Hold ADS and fire together instead of releasing the sight first."""
        return self._chord(("aim", "attack"), hold)

    # -- command execution ------------------------------------------------

    def command_ops(self, command: Command) -> list[Op]:
        """Turn a parsed command into primitives (one tick's worth)."""
        action = command.action
        seconds = command.seconds
        if command.kind == "stop":
            return []
        if command.kind == "respawn":
            # On the death screen Space is the common respawn prompt; in normal
            # play it is just a jump, which is harmless.
            return self._tap("jump", hold=0.16)
        if command.kind == "wait":
            return [Op("wait", seconds=max(0.5, seconds))]
        if command.kind == "look_at":
            if action == "turn_left":
                return self._turn_ops(-max(15.0, min(160.0, seconds * 90.0)))
            if action == "turn_right":
                return self._turn_ops(max(15.0, min(160.0, seconds * 90.0)))
            if action == "look_up":
                return [Op("look", dx=0.0, dy=-self.look.turn_counts(25.0))]
            if action == "look_down":
                return [Op("look", dx=0.0, dy=self.look.turn_counts(25.0))]
            return []
        if command.kind == "move":
            if action in ("forward", "back", "left", "right"):
                if action == "forward":
                    return self._chord(("forward", "sprint"), max(0.15, seconds))
                return self._hold(action, max(0.12, seconds))
        if command.kind == "action":
            if action == "fire":
                return self._fire(hold=0.18)
            if action == "aim":
                return self._aim(hold=max(0.15, seconds))
            if action == "interact":
                return self._tap("interact", hold=0.18)
            if action == "jump":
                return self._tap("jump", hold=0.12)
            if action == "wallrun":
                return self._chord(("forward", "sprint", "jump"), max(0.30, seconds))
            if action == "crouch":
                return [Op("hold", key=self._key("crouch"), seconds=max(0.4, seconds))]
            if action == "sprint":
                return self._hold("sprint", max(0.3, seconds))
            if action == "reload":
                return self._tap("reload", hold=0.1)
            if action == "respawn":
                return self._tap("jump", hold=0.16)
            if action == "tactical":
                return self._tap("tactical", hold=0.08)
            if action == "ordnance":
                return self._tap("ordnance", hold=0.08)
            if action == "melee":
                return self._tap("melee", hold=0.1)
            if action == "titan":
                return self._tap("titan", hold=0.12)
            if action == "boost":
                return self._tap("boost", hold=0.1)
            if action == "eject":
                return self._tap("eject", hold=0.12)
        return []

    # -- main decision ----------------------------------------------------

    def decide(
        self,
        features: FrameFeatures | None,
        hud: HudState | None,
        *,
        now: float | None = None,
        menu: bool = False,
        playing: bool = True,
        titan_mode: bool = False,
    ) -> Decision:
        now = time.time() if now is None else float(now)
        self.ticks += 1
        hud = hud or HudState()

        if not playing:
            self.state = PolicyState.WAITING
            return Decision(self.state, "还没看到游戏画面，我先等着。", [], ["no-game"], 0.2)

        if menu:
            self.state = PolicyState.MENU
            return Decision(
                self.state,
                "游戏停在菜单/暂停画面，我先不按键。",
                [],
                ["menu"],
                0.8,
            )

        if features is None or features.black:
            self.state = PolicyState.BLIND
            return Decision(
                self.state,
                "截到的画面是全黑的，我不乱按键，先确认游戏还在前台。",
                [],
                ["blind"],
                0.9,
            )

        self._track_scene(features, now)

        directive = self.directive
        if directive is not None and directive.expired(now):
            self.directive = None
            directive = None
        if directive is not None and directive.command is not None:
            command = directive.command
            self.directive = None
            if command.kind == "stop":
                self.state = PolicyState.PAUSED
                return Decision(
                    self.state,
                    "好，我停手，把操作还给你。",
                    [],
                    ["stop-command"],
                    1.0,
                )
            ops = self.command_ops(command)
            if ops:
                state = PolicyState.RESPAWN if command.kind == "respawn" else self.state
                self.state = state
                return Decision(
                    state,
                    f"照你说的做：{command.text or command.action}。",
                    ops,
                    [f"command:{command.action}"],
                    0.9,
                )

        mode = ""
        if self._forced_mode and now < self._forced_until:
            mode = self._forced_mode
        if directive is not None and directive.mode in (
            "explore",
            "fight",
            "titan",
            "objective",
            "wait",
        ):
            mode = directive.mode

        if mode == "wait":
            self.state = PolicyState.WAITING
            return Decision(self.state, "按你的要求先待机，不按键。", [], ["directive:wait"], 0.8)

        if mode == "titan" or titan_mode:
            return self._titan(features, hud, now, [f"mode:{mode or 'titan'}"])

        if self.auto_call_titan and self._titan_call_due(features, hud, now):
            ops = self._tap("titan", hold=0.12)
            if ops:
                self._last_titan_call_at = now
                self.state = PolicyState.OBJECTIVE
                return Decision(
                    self.state,
                    "泰坦计量看起来快满了，我按 V 试试召唤泰坦；如果没反应就继续打。",
                    ops,
                    ["titan-call"],
                    0.55,
                )

        combat_evidence = self._combat_evidence(features, hud)
        if mode != "objective" and combat_evidence:
            return self._combat(features, hud, now, combat_evidence)

        if mode == "fight":
            return self._combat(features, hud, now, ["forced-fight"])

        if mode == "objective" or (features.objective_visible and features.objective_score >= 0.28):
            return self._objective(features, hud, now)

        return self._explore(features, hud, now)

    # -- scene tracking ---------------------------------------------------

    def _track_scene(self, features: FrameFeatures, now: float) -> None:
        if features.scene_hash != self._last_hash:
            self._last_hash = features.scene_hash
            self._same_scene_since = now
            if self._stuck_level:
                self._stuck_level = 0
                self._stuck_since = 0.0
        if features.motion > 0.0025 or features.motion_strength > 0.01 or features.center_motion > 0.004:
            self._last_motion_at = now
            if self._stuck_level and now - self._stuck_since > 4.0:
                self._stuck_level = 0

    def _combat_evidence(self, features: FrameFeatures, hud: HudState) -> list[str]:
        evidence: list[str] = []
        if hud.damage_flash:
            evidence.append("正在掉血")
        if features.crosshair and features.crosshair_red > 0.20:
            evidence.append("准星发红")
        # A camera pan moves the whole grid; an enemy in front changes the
        # centre while the rest is comparatively still.
        if features.center_motion > 0.010 and features.center_motion_strength > 0.060 and features.motion < 0.055:
            evidence.append("视野中央有局部移动")
        if features.center_red > 0.018:
            evidence.append("准星附近有红色目标/命中")
        if features.hit_flash > 0.90 and features.center_motion > 0.004:
            evidence.append("中央有亮闪")
        if hud.low_health_guess:
            evidence.append("血量区域偏红")
        return evidence

    def _titan_call_due(
        self,
        features: FrameFeatures,
        hud: HudState,
        now: float,
    ) -> bool:
        if now - self._last_titan_call_at < 18.0:
            return False
        if not hud.present:
            return False
        return bool(hud.titan_ready_hint and features.titan_meter >= 0.16 and features.center_motion < 0.02)

    # -- combat -----------------------------------------------------------

    def _combat(
        self,
        features: FrameFeatures,
        hud: HudState,
        now: float,
        evidence: list[str],
    ) -> Decision:
        self.state = PolicyState.COMBAT
        ops: list[Op] = []
        intent = "看到目标了，先对准再点射。"
        confidence = 0.55
        reasons = list(evidence)

        target_motion = features.center_motion_strength > 0.055 or features.center_red > 0.018
        if target_motion:
            # Aim at the centre-localised motion/red blob, then fire.  Small
            # offsets are ignored so the catgirl does not jitter on noise.
            if abs(features.center_motion_x - 0.5) > 0.035:
                ops.extend(
                    self._look_ops(
                        features.center_motion_x,
                        features.center_motion_y,
                        scale=0.55,
                    )
                )
            ops.extend(self._aim_fire(hold=0.28))
            self._shots += 1
            intent = "准星附近有目标，我压一下枪口再短点射，不扫射。"
            confidence = 0.78
        elif hud.damage_flash:
            ops.extend(self._hold("back", 0.42))
            ops.extend(self._tap("tactical", hold=0.08))
            ops.extend(self._fire(hold=0.12))
            self._shots += 1
            intent = "正在挨打，先退一步、放战术技能，再还击。"
            confidence = 0.55
        elif features.motion_strength > 0.02:
            ops.extend(self._look_ops(features.motion_x, features.motion_y, scale=0.45))
            ops.extend(self._fire(hold=0.10))
            self._shots += 1
            intent = "画面里有东西在动，我先把视角转过去再压火力。"
            confidence = 0.5
        else:
            ops.extend(self._fire(hold=0.10))
            self._shots += 1
            intent = "有战斗迹象，先压一轮火力保持压制。"
            confidence = 0.4

        # Reload is proactive rather than ammo-OCR driven: after enough bursts
        # or a few seconds of firing, top up during a natural gap.
        if self._shots >= 4 or (now - self._last_reload_at > 5.0 and now - self._last_fire_at > 1.2):
            ops.extend(self._tap("reload", hold=0.1))
            self._last_reload_at = now
            self._shots = 0
            reasons.append("reload")

        if hud.low_health_guess:
            ops.extend(self._hold("back", 0.32))
            intent += " 血量看着偏低，先拉开距离。"
            confidence = max(0.45, confidence - 0.1)

        if now - self._last_tactical_at > 9.0:
            ops.extend(self._tap("tactical", hold=0.08))
            self._last_tactical_at = now
            reasons.append("tactical")
        if now - self._last_ordnance_at > 7.0:
            ops.extend(self._tap("ordnance", hold=0.08))
            self._last_ordnance_at = now
            reasons.append("ordnance")
        if features.center_motion_strength > 0.16 and now - self._last_fire_at > 0.8:
            ops.extend(self._tap("melee", hold=0.12))
            reasons.append("melee-close")

        # Strafing keeps the hitbox moving; a stationary duel is death in
        # Titanfall.  Alternate sides deterministically so it is testable.
        if self.ticks % 3 == 0:
            ops.extend(self._chord(("right",), 0.22))
        elif self.ticks % 3 == 1:
            ops.extend(self._chord(("left",), 0.22))

        if now - self._last_fire_at < 0.18:
            ops = [op for op in ops if op.kind != "tap" or op.key != self._key("attack")]
        self._last_fire_at = now
        return Decision(self.state, intent, ops, reasons, confidence)

    # -- objective / exploration -----------------------------------------

    def _objective(
        self,
        features: FrameFeatures,
        hud: HudState,
        now: float,
    ) -> Decision:
        self.state = PolicyState.OBJECTIVE
        ops: list[Op] = []
        reasons: list[str] = []
        marker = features.objective_visible and features.objective_score >= 0.28
        if marker:
            dx = features.objective_x - 0.5
            dy = features.objective_y - 0.5
            if abs(dx) > 0.055:
                ops.extend(
                    self._look_ops(
                        features.objective_x,
                        min(0.60, max(0.40, features.objective_y)),
                        scale=0.8,
                    )
                )
                reasons.append("steer-to-marker")
            if abs(dy) > 0.20:
                ops.append(
                    Op(
                        "look",
                        dx=0.0,
                        dy=self.look.turn_counts((features.objective_y - 0.5) * 20.0),
                    )
                )
                reasons.append("marker-vertical")
            intent = f"橙色任务标记在画面({features.objective_x:.2f},{features.objective_y:.2f})，我朝它过去。"
            confidence = min(0.85, 0.45 + features.objective_score * 0.5)
        else:
            intent = "暂时没看到任务标记，我沿当前通道推进并留意门和敌人。"
            confidence = 0.35

        ops.extend(self._chord(("forward", "sprint"), 0.70 + self._random.random() * 0.35))
        if now - self._last_jump_at > 1.1:
            ops.extend(self._chord(("forward", "sprint", "jump"), 0.18))
            self._last_jump_at = now
            reasons.append("parkour-jump")
        if now - self._last_slide_at > 4.5:
            ops.extend(self._chord(("forward", "sprint", "crouch"), 0.25))
            self._last_slide_at = now
            reasons.append("slide")
        if now - self._last_use_at > 3.8:
            ops.extend(self._tap("interact", hold=0.15))
            self._last_use_at = now
            reasons.append("interact")
        if hud.damage_flash:
            ops = [Op("hold", key=self._key("back"), seconds=0.4)] + ops[:3]
            intent += " 正在挨打，先退一下再走。"
            reasons.append("under-fire")
        return Decision(self.state, intent, ops, reasons, confidence)

    def _explore(
        self,
        features: FrameFeatures,
        hud: HudState,
        now: float,
    ) -> Decision:
        still_for = now - self._last_motion_at
        if still_for > 2.6 and features.scene_hash == self._last_hash:
            return self._stuck(features, hud, now, still_for)

        self._explore_phase += 1
        ops: list[Op] = []
        intent = "沿通道向前推进，保持移动，边走边找门和敌人。"
        reasons = ["explore"]

        if features.objective_visible and features.objective_score >= 0.33:
            ops.extend(
                self._look_ops(
                    features.objective_x,
                    min(0.62, max(0.38, features.objective_y)),
                    scale=0.7,
                )
            )
            reasons.append("marker-glance")
            intent = "看到橙色任务标记，先朝它的方向带一下视角。"

        if self._explore_phase % 7 == 0:
            turn = 18.0 * self._explore_turn
            self._explore_turn *= -1
            ops.extend(self._turn_ops(turn))
            reasons.append("look-sweep")
        else:
            ops.extend(
                self._chord(
                    ("forward", "sprint"),
                    0.70 + self._random.random() * 0.35,
                )
            )
            if now - self._last_jump_at > 1.35:
                ops.extend(self._chord(("forward", "sprint", "jump"), 0.18))
                self._last_jump_at = now
                reasons.append("parkour-jump")
            if now - self._last_slide_at > 5.0:
                ops.extend(self._chord(("forward", "sprint", "crouch"), 0.22))
                self._last_slide_at = now
                reasons.append("slide")

        if still_for > 0.7 and now - self._last_use_at > 2.8:
            ops.extend(self._tap("interact", hold=0.15))
            self._last_use_at = now
            reasons.append("interact")
            intent = "前面可能有门或控制台，我按 E 互动一下。"

        if hud.subtitle:
            reasons.append("subtitle-present")
        return Decision(self.state, intent, ops, reasons, 0.52)

    def _stuck(
        self,
        features: FrameFeatures,
        hud: HudState,
        now: float,
        still_for: float,
    ) -> Decision:
        self.state = PolicyState.STUCK
        if not self._stuck_since:
            self._stuck_since = now
        if now - self._stuck_since > 4.5:
            self._stuck_level = min(4, self._stuck_level + 1)
            self._stuck_since = now
        level = self._stuck_level
        ops: list[Op] = []
        reasons: list[str] = []
        if level == 0:
            ops.extend(self._turn_ops(55.0 * self._explore_turn))
            self._explore_turn *= -1
            intent = "画面一直没变化，我先转个方向看看是不是走错了。"
            reasons = ["stuck:look"]
        elif level == 1:
            ops.extend(self._hold("back", 0.5))
            ops.extend(self._chord(("forward", "jump"), 0.20))
            intent = "像被地形卡住了，我退半步再跳一下试试。"
            reasons = ["stuck:back-jump"]
        elif level == 2:
            ops.extend(self._chord(("forward", "sprint", "jump"), 0.55))
            ops.extend(self._chord(("right", "jump"), 0.35))
            intent = "换个方向跑墙/侧跳，绕开这堵墙。"
            reasons = ["stuck:parkour"]
        elif level == 3:
            ops.extend(self._tap("interact", hold=0.18))
            ops.extend(self._chord(("forward", "sprint", "crouch"), 0.40))
            ops.extend(self._chord(("forward", "jump"), 0.22))
            intent = "试试按 E、滑铲再跳，可能是门或矮通道。"
            reasons = ["stuck:interact"]
        else:
            ops.extend(self._tap("titan", hold=0.12))
            ops.extend(self._tap("jump", hold=0.15))
            intent = "再试一次召唤泰坦/跳跃；如果画面还是不动，我需要主人给个提示。"
            reasons = ["stuck:titan-jump"]
        if hud.damage_flash:
            ops.append(Op("hold", key=self._key("back"), seconds=0.35))
            intent += " 还在挨打，先退。"
        return Decision(self.state, intent, ops, reasons, 0.4)

    # -- Titan ------------------------------------------------------------

    def _titan(
        self,
        features: FrameFeatures,
        hud: HudState,
        now: float,
        reasons: list[str],
    ) -> Decision:
        self.state = PolicyState.TITAN
        ops: list[Op] = []
        intent = "在泰坦里：主炮持续输出，留神火箭和冲刺。"
        reasons = list(reasons)

        if hud.damage_flash or hud.low_health_guess:
            ops.extend(self._hold("aim", 0.30))
            ops.extend(self._hold("back", 0.35))
            intent = "泰坦在挨打，先举盾/后退，别站着吃伤害。"
            reasons.append("titan-guard")
        else:
            ops.extend(self._aim_fire(hold=0.32))
            self._shots += 1
            if features.center_motion_strength > 0.10:
                ops.extend(self._tap("melee", hold=0.1))
                reasons.append("titan-melee")
            if now - self._last_ordnance_at > 6.0:
                ops.extend(self._tap("ordnance", hold=0.08))
                self._last_ordnance_at = now
                reasons.append("titan-ordnance")
            if now - self._last_jump_at > 2.0:
                ops.extend(self._tap("jump", hold=0.10))
                self._last_jump_at = now
                reasons.append("titan-dash")

        if now - self._last_core_at > 14.0:
            ops.extend(self._tap("titan", hold=0.12))
            self._last_core_at = now
            reasons.append("titan-core")

        if self._shots >= 3:
            ops.extend(self._tap("reload", hold=0.1))
            self._shots = 0
            reasons.append("titan-reload")
        if hud.subtitle:
            reasons.append("subtitle-present")
        return Decision(self.state, intent, ops, reasons, 0.6)
