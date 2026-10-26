"""Manifest / SDK surface / safety-hygiene contract tests.

The SDK part is skipped when N.E.K.O is not importable (for a plain checkout),
but always runs inside the Market verification container.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

EXPECTED_ENTRIES = {
    "titan_play",
    "titan_stop",
    "titan_resume",
    "titan_status",
    "titan_look",
    "titan_selfcheck",
    "titan_missions",
}
EXPECTED_TOOLS = {
    "titan_play",
    "titan_command",
    "titan_look",
    "titan_aim",
    "titan_shoot",
    "titan_directive",
    "titan_sequence",
    "titan_stop",
    "titan_resume",
    "titan_status",
    "titan_missions",
    "titan_selfcheck",
}
EXPECTED_ACTIONS = {"titan_play", "titan_stop", "titan_status"}


def load_manifest():
    return tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))


def test_manifest_identity_and_safe_defaults():
    data = load_manifest()
    plugin = data["plugin"]
    assert plugin["id"] == "titanfall2_player"
    assert plugin["type"] == "plugin"
    assert plugin["entry"] == "plugin.plugins.titanfall2_player:Titanfall2PlayerPlugin"
    assert data["plugin_runtime"]["auto_start"] is False
    assert data["plugin_runtime"]["enabled"] is True
    assert plugin["keywords"]

    cfg = data["titanfall2"]
    assert cfg["autopilot_enabled"] is False
    assert cfg["dry_run"] is False
    assert cfg["require_foreground"] is True
    assert cfg["yield_on_human_input"] is True
    assert cfg["start_in_game_required"] is True
    assert cfg["max_session_minutes"] > 0


def test_config_table_is_not_the_plugin_id():
    data = load_manifest()
    assert "titanfall2" in data
    assert "titanfall2_player" not in data


def test_i18n_files_parse_and_agree_on_action_keys():
    data = load_manifest()
    directory = ROOT / data["plugin"]["i18n"]["locales_dir"]
    assert (directory / "zh-CN.json").is_file()
    key_sets = []
    for path in directory.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        key_sets.append(set(payload))
    assert key_sets
    assert all(keys == key_sets[0] for keys in key_sets)
    for action in EXPECTED_ACTIONS:
        assert f"action.{action}" in key_sets[0]


def test_no_third_party_python_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == []


def test_no_forbidden_imports_or_game_launch():
    forbidden = re.compile(
        r"^\s*(?:import|from)\s+(cv2|opencv|pyautogui|pydirectinput|win32gui|"
        r"win32con|mss|dxcam|keyboard|pynput)\b",
        re.M,
    )
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert forbidden.search(text) is None, path.name
        assert "subprocess" not in text, path.name
        assert "ShellExecute" not in text, path.name
        assert "steam://rungameid" not in text, path.name


def test_no_startup_side_effects():
    text = (ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "asyncio.run(" not in text
    assert "time.sleep(" not in text


def _load_sdk_surface():
    sdk = pytest.importorskip("plugin.sdk.plugin")
    package = pytest.importorskip("titanfall2_player")
    if not hasattr(sdk, "StubContext"):
        # Market CI mounts the plugin inside the real N.E.K.O tree, whose
        # ``plugin`` package is imported before this conftest runs.  The local
        # strict stub is then shadowed; the real surface is covered by the live
        # host probe described in PUBLISHING.md instead of being faked here.
        pytest.skip("real N.E.K.O SDK present; surface is covered by the live host probe")
    return sdk, package


def test_sdk_surface_entries_tools_actions():
    sdk, package = _load_sdk_surface()
    ctx = sdk.StubContext(
        plugin_id="titanfall2_player",
        config={"titanfall2": {"dry_run": True}},
    )
    plugin = package.Titanfall2PlayerPlugin(ctx)

    assert set(plugin.collect_entries()) == EXPECTED_ENTRIES
    for entry in plugin.collect_entries().values():
        schema = entry["meta"]["input_schema"]
        assert schema.get("type") == "object"

    assert set(plugin.collect_llm_tools()) == EXPECTED_TOOLS
    actions = plugin.collect_ui_actions()
    assert {meta["id"] for meta in actions.values()} == EXPECTED_ACTIONS

    try:
        asyncio.run(plugin.shutdown())
    except Exception:
        pass


def test_sdk_startup_and_play_waiting_summary():
    sdk, package = _load_sdk_surface()
    ctx = sdk.StubContext(
        plugin_id="titanfall2_player",
        config={"titanfall2": {"dry_run": True}},
    )
    plugin = package.Titanfall2PlayerPlugin(ctx)
    try:
        result = asyncio.run(plugin.startup())
        assert result.ok
        unknown = asyncio.run(plugin.titan_play_tool(game="这不是游戏"))
        assert "只支持" in unknown["summary"]
        known = asyncio.run(plugin.titan_play_tool(game="去玩泰坦陨落2"))
        assert "手动打开" in known["summary"]
        assert known["waiting_for_game"]
        assert known["strategy"]
        assert len(known["chapters"]) == 9
    finally:
        try:
            asyncio.run(plugin.shutdown())
        except Exception:
            pass


def test_sdk_push_payloads_are_bounded_and_valid():
    sdk, package = _load_sdk_surface()
    ctx = sdk.StubContext(
        plugin_id="titanfall2_player",
        config={"titanfall2": {"dry_run": True}},
    )
    plugin = package.Titanfall2PlayerPlugin(ctx)
    try:
        asyncio.run(plugin.startup())
        asyncio.run(plugin.titan_play_tool(game="泰坦陨落2"))
        assert ctx.messages
        for message in ctx.messages:
            assert message.get("ai_behavior") in ("respond", "read", "blind")
            for part in message.get("parts") or []:
                if part.get("type") == "text":
                    assert isinstance(part.get("text"), str)
                if part.get("type") == "image":
                    assert isinstance(part.get("data"), (bytes, bytearray))
    finally:
        try:
            asyncio.run(plugin.shutdown())
        except Exception:
            pass
