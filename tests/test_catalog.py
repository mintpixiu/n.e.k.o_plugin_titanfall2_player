"""Catalogue and alias resolution tests (no game, no SDK)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _loader  # noqa: E402

catalog = _loader.catalog


def test_identity_matches_the_real_game() -> None:
    game = catalog.TITANFALL2
    assert game.id == "titanfall2"
    assert game.app_id == "1237970"
    assert game.exe.lower() == "titanfall2.exe"
    assert game.playable
    assert len(game.chapters) == 9


def test_default_keymap_has_safe_core_and_optional_abilities() -> None:
    keys = catalog.TITANFALL2.keys
    for action in (
        "forward",
        "back",
        "left",
        "right",
        "jump",
        "crouch",
        "sprint",
        "interact",
        "reload",
        "attack",
        "aim",
        "titan",
    ):
        assert keys[action]
    assert keys["attack"] == "mouse1"
    assert keys["aim"] == "mouse2"
    assert keys["titan"] == "v"
    # Optional actions must be overridable, not required for play.
    assert catalog.TITANFALL2.key("tactical", {"tactical": "x"}) == "x"


def test_aliases_resolve_chinese_and_english() -> None:
    for text in (
        "去玩泰坦陨落2",
        "泰坦陨落二",
        "神兵泰坦2",
        "Titanfall 2",
        "titanfall2",
        "铁驭",
    ):
        resolution = catalog.resolve_game(text)
        assert resolution.game is catalog.TITANFALL2, text


def test_bare_titanfall_is_accepted_with_disclosure() -> None:
    resolution = catalog.resolve_game("去玩泰坦陨落")
    assert resolution.game is catalog.TITANFALL2
    assert resolution.assumed


def test_unknown_game_is_not_silently_mapped() -> None:
    resolution = catalog.resolve_game("去玩半条命")
    assert resolution.game is None
    assert resolution.reason == "no-match"


def test_strategy_brief_mentions_parkour_and_titan() -> None:
    brief = catalog.TITANFALL2.strategy_brief(max_chapters=9)
    assert "跑墙" in brief
    assert "泰坦" in brief
    assert "BT-7274" in brief


def test_chapter_dicts_have_bilingual_titles() -> None:
    for chapter in catalog.TITANFALL2.chapters:
        payload = chapter.as_dict()
        assert payload["title"]
        assert payload["title_en"]
        assert payload["hint"]
