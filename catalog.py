"""Titanfall 2 game catalogue: identity, aliases, keys and mission briefs.

This plugin deliberately supports **one** game.  Splitting the data out of the
policy keeps every Titanfall-specific assumption in one reviewable place:

* Steam AppID ``1237970`` and executable ``Titanfall2.exe`` identify the game;
* the default PC bindings are the public defaults, with the few contested
  optional actions marked as such and overridable from ``plugin.toml``;
* the nine campaign missions carry short tactical hints so the dialogue LLM can
  plan a whole mission instead of only reacting to one frame.

The local policy never reads game memory, save files or the engine console.  A
custom key layout is supported through ``[titanfall2.keys]``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

__all__ = [
    "CHAPTERS",
    "GAMES",
    "GAME_BY_ID",
    "GameProfile",
    "SUPPORTED_IDS",
    "aliases_for",
    "describe_installed",
    "family_games",
    "normalise_text",
    "resolve_game",
]

# ---------------------------------------------------------------------------
# Key map
# ---------------------------------------------------------------------------

#: Canonical action -> default PC key.
#:
#: The mandatory actions (move / jump / crouch / sprint / use / reload / fire /
#: aim / call Titan) are stable across the public PC control references.  The
#: three ability keys are the ones guides disagree about, so they are explicitly
#: labelled "optional" and can be corrected with ``[titanfall2.keys]`` without
#: touching code.  The plugin keeps playing if one of them is wrong.
TITANFALL2_KEYS: dict[str, str] = {
    "forward": "w",
    "back": "s",
    "left": "a",
    "right": "d",
    "jump": "space",
    "crouch": "ctrl",
    "sprint": "shift",
    "interact": "e",
    "reload": "r",
    "attack": "mouse1",
    "aim": "mouse2",
    "titan": "v",
    "eject": "x",
    "weapon_1": "1",
    "weapon_2": "2",
    "weapon_3": "3",
    # Optional abilities (verify once in game settings; safe to leave wrong):
    "tactical": "q",
    "ordnance": "g",
    "melee": "f",
    "boost": "c",
    # A Titan's defensive utility is usually RMB; same button as aim.
    "titan_utility": "mouse2",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chapter:
    """One campaign mission with a short tactical hint."""

    title: str
    hint: str
    title_en: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "title_en": self.title_en, "hint": self.hint}


@dataclass(frozen=True)
class GameProfile:
    id: str
    name_zh: str
    name_en: str
    family: str
    engine: str
    app_id: str
    exe: str
    install_dirs: tuple[str, ...]
    title_patterns: tuple[str, ...]
    aliases: tuple[str, ...]
    keys: Mapping[str, str]
    strategy: str
    chapters: tuple[Chapter, ...] = ()
    supports: tuple[str, ...] = ()
    hud_style: str = "titanfall"
    notes: str = ""
    vr_only: bool = False
    multiplayer_only: bool = False
    coop_only: bool = False

    @property
    def display(self) -> str:
        return self.name_zh

    @property
    def playable(self) -> bool:
        return not (self.vr_only or self.multiplayer_only or self.coop_only)

    def supports_feature(self, feature: str) -> bool:
        return feature in self.supports

    def key(self, action: str, overrides: Mapping[str, str] | None = None) -> str:
        if overrides and action in overrides and overrides[action]:
            return str(overrides[action])
        return str(self.keys.get(action, "") or "")

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name_zh,
            "name_en": self.name_en,
            "family": self.family,
            "engine": self.engine,
            "app_id": self.app_id,
            "exe": self.exe,
            "playable": self.playable,
            "vr_only": self.vr_only,
            "multiplayer_only": self.multiplayer_only,
            "coop_only": self.coop_only,
            "supports": list(self.supports),
            "chapters": len(self.chapters),
            "notes": self.notes,
        }

    def strategy_brief(self, *, max_chapters: int = 6) -> str:
        chapters = list(self.chapters[:max_chapters])
        tail = ""
        if len(self.chapters) > len(chapters):
            tail = f"…共 {len(self.chapters)} 章"
        chapter_text = "；".join(f"{c.title}：{c.hint}" for c in chapters) + tail
        parts = [f"{self.name_zh}（{self.name_en}）", self.strategy]
        if chapter_text:
            parts.append("战役提示：" + chapter_text)
        if self.notes:
            parts.append(self.notes)
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Campaign missions
# ---------------------------------------------------------------------------

#: Short, original tactical summaries written for this plugin.  They are hints,
#: not a script: the LLM still decides from the live screenshot.
TITANFALL2_CHAPTERS = (
    Chapter(
        "铁驭试炼",
        "模拟舱里先学跑墙、二段跳、滑铲和射击；靶场清完再按提示召唤泰坦。",
        "The Pilot's Gauntlet",
    ),
    Chapter(
        "BT-7274",
        "落地后先清步兵和猎犬，沿岩壁跑酷；找两枚电池并按 E 装回 BT，再登机。",
        "BT-7274",
    ),
    Chapter(
        "血与锈",
        "跟友军泰坦沿大坝推进；先清反泰坦步兵，再打敌方泰坦的红色核心；控制室开关按 E。",
        "Blood and Rust",
    ),
    Chapter(
        "堕入深渊",
        "工厂关：步兵和泰坦混编，优先找掩体；需要下泰坦钻管道时不要恋战。",
        "Into the Abyss",
    ),
    Chapter(
        "因果报应",
        "时空切换关：用鼠标中键/对应键切过去与现在，红蓝光提示敌人；有火海时切到过去。",
        "Effect and Cause",
    ),
    Chapter(
        "信标",
        "卫星站：风机与电网是环境伤害，先关风机再走；电弧工具按 E 使用。",
        "The Beacon",
    ),
    Chapter(
        "火之审判",
        "IMC 基地正面战：先打反泰坦步兵，利用掩体和友军泰坦；泰坦核心好了就开 V。",
        "Trial by Fire",
    ),
    Chapter(
        "圣柜",
        "飞艇战：先打步兵，再处理飞行泰坦 Viper；避免站在开阔处。",
        "The Ark",
    ),
    Chapter(
        "折叠时空武器",
        "最后一段：用智慧手枪清场，找到新泰坦后按 V 投放；最终 Boss 战绕掩体打核心。",
        "The Fold Weapon",
    ),
)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

TITANFALL2 = GameProfile(
    id="titanfall2",
    name_zh="泰坦陨落 2",
    name_en="Titanfall 2",
    family="titanfall",
    engine="source_modified",
    app_id="1237970",
    exe="Titanfall2.exe",
    install_dirs=("Titanfall2", "Titanfall 2", "Titanfall"),
    title_patterns=(
        r"Titanfall\s*2",
        r"TITANFALL\s*2",
        r"泰坦陨落",
    ),
    aliases=(
        "泰坦陨落2",
        "泰坦陨落二",
        "泰坦陨落 2",
        "泰坦陨落",
        "神兵泰坦2",
        "神兵泰坦二",
        "神兵泰坦",
        "titanfall2",
        "titanfall 2",
        "titanfall2",
        "titanfall",
        "ttf2",
        "铁驭",
        "bt-7274",
        "bt7274",
    ),
    keys=TITANFALL2_KEYS,
    strategy=(
        "高速 FPS + 跑酷 + 泰坦战。铁驭阶段：Shift 冲刺、Space 跳/二段跳、贴墙按住 Space "
        "跑墙、Ctrl 滑铲；不要在原地对枪，保持横向移动。战斗时先看准星附近，再短点射，"
        "弹匣打空或超过几秒就 R 换弹。目标是屏幕上的橙色任务标记：让它保持在画面中央，"
        "沿它赶路，接近后用 E 开门/互动/登机。泰坦条满时按 V 召唤/登机；泰坦阶段用 LMB "
        "输出、RMB 防御、G 发射副武器，Space 冲刺躲炮弹。死亡后按提示重生；不要跳过剧情时"
        "乱按。"
    ),
    chapters=TITANFALL2_CHAPTERS,
    supports=(
        "combat",
        "parkour",
        "wallrun",
        "slide",
        "titan",
        "objective_marker",
        "respawn",
        "campaign",
    ),
    hud_style="titanfall",
    notes=(
        "键位以 PC 公开默认值为准；F 近战、Q 战术、G 副武器、C 强化在部分攻略里有分歧，"
        "第一次使用请在游戏设置里核对，或用 [titanfall2.keys] 覆盖。"
        "插件不读内存、不改存档、不代发成就。"
    ),
)

GAMES: tuple[GameProfile, ...] = (TITANFALL2,)
GAME_BY_ID: dict[str, GameProfile] = {game.id: game for game in GAMES}
SUPPORTED_IDS: tuple[str, ...] = tuple(game.id for game in GAMES)
CHAPTERS: dict[str, tuple[Chapter, ...]] = {game.id: game.chapters for game in GAMES}

FAMILIES: dict[str, tuple[str, ...]] = {"titanfall": ("titanfall2",)}
FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "titanfall": (
        "泰坦陨落",
        "神兵泰坦",
        "titanfall",
        "ttf2",
        "铁驭",
    ),
}
MAINLINE = {"titanfall": "titanfall2"}


# ---------------------------------------------------------------------------
# Text normalisation / resolution
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"[\s\u3000·•,，。.!！?？:：;；'\"“”‘’、/\\|()（）\[\]【】<>《》_\-—+]+")


def normalise_text(text: Any) -> str:
    """Lowercase, NFKC-normalise and strip punctuation for alias matching."""
    raw = unicodedata.normalize("NFKC", str(text or "")).lower()
    return _PUNCT.sub("", raw)


_ALIAS_INDEX: list[tuple[str, str]] = sorted(
    ((normalise_text(alias), game.id) for game in GAMES for alias in game.aliases if normalise_text(alias)),
    key=lambda item: len(item[0]),
    reverse=True,
)


def aliases_for(game_id: str) -> tuple[str, ...]:
    game = GAME_BY_ID.get(game_id)
    return tuple(game.aliases) if game else ()


def family_games(family: str) -> tuple[GameProfile, ...]:
    return tuple(GAME_BY_ID[gid] for gid in FAMILIES.get(family, ()) if gid in GAME_BY_ID)


@dataclass(frozen=True)
class Resolution:
    """Result of interpreting a "go play Titanfall 2" sentence."""

    game: GameProfile | None
    reason: str = ""
    assumed: bool = False
    candidates: tuple[GameProfile, ...] = ()
    unsupported: GameProfile | None = None

    @property
    def ok(self) -> bool:
        return self.game is not None

    def summary(self) -> dict[str, Any]:
        return {
            "game": self.game.id if self.game else None,
            "name": self.game.name_zh if self.game else None,
            "reason": self.reason,
            "assumed": self.assumed,
            "candidates": [g.id for g in self.candidates],
            "unsupported": self.unsupported.id if self.unsupported else None,
        }


def _pick_from_family(
    family: str,
    *,
    installed: Iterable[str],
    running: Iterable[str],
) -> GameProfile | None:
    installed_set = set(installed)
    running_set = set(running)
    members = family_games(family)
    for game in members:
        if game.id in running_set and game.playable:
            return game
    for game in members:
        if game.id in installed_set and game.playable:
            return game
    for game in members:
        if game.id == MAINLINE.get(family):
            return game
    return members[0] if members else None


def resolve_game(
    text: Any,
    *,
    installed: Iterable[str] = (),
    running: Iterable[str] = (),
) -> Resolution:
    """Resolve a free-form request such as ``去玩泰坦陨落2`` to the profile.

    Longest alias wins, so ``泰坦陨落2`` is preferred over ``泰坦陨落``.  A bare
    ``泰坦陨落`` (without the 2) is accepted but marked as an assumption in the
    reply instead of silently pretending the user named the sequel.
    """
    body = normalise_text(text)
    if not body:
        return Resolution(None, reason="empty")

    for alias, game_id in _ALIAS_INDEX:
        if alias and alias in body:
            game = GAME_BY_ID[game_id]
            if not game.playable:
                return Resolution(None, reason="unsupported", unsupported=game)
            # "泰坦陨落" without a sequel marker is an explicit but ambiguous
            # request; only this one-game plugin exists, so accept and disclose.
            assumed = alias in ("泰坦陨落", "神兵泰坦", "titanfall") and not any(
                marker in body for marker in ("2", "二", "续作", "sequel")
            )
            return Resolution(game, reason=f"alias:{alias}", assumed=assumed)

    for family, aliases in FAMILY_ALIASES.items():
        if any(normalise_text(alias) in body for alias in aliases):
            game = _pick_from_family(family, installed=installed, running=running)
            if game is not None:
                return Resolution(game, reason=f"family:{family}", assumed=True)

    candidates = tuple(GAME_BY_ID[gid] for gid in SUPPORTED_IDS if gid in set(installed) and GAME_BY_ID[gid].playable)
    return Resolution(None, reason="no-match", candidates=candidates)


def describe_installed(installed: Iterable[str]) -> str:
    names = [GAME_BY_ID[gid].name_zh for gid in installed if gid in GAME_BY_ID]
    return "、".join(names) if names else "（未检测到《泰坦陨落 2》）"
