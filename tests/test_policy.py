"""Instruction parsing and reactive policy tests (no screen / no input)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _loader  # noqa: E402

catalog = _loader.catalog
policy_mod = _loader.policy
vision = _loader.vision
hud_mod = _loader.hud


def frame(**kwargs):
    return vision.FrameFeatures(**kwargs)


def hud(**kwargs):
    return hud_mod.HudState(**kwargs)


def new_policy(**kwargs):
    return policy_mod.PlayPolicy(catalog.TITANFALL2, **kwargs)


def op_keys(ops):
    keys = []
    for op in ops:
        if op.kind == "chord":
            keys.extend(op.keys)
        elif op.key:
            keys.append(op.key)
    return keys


def test_stop_and_resume_commands() -> None:
    assert policy_mod.parse_instruction("停手").kind == "stop"
    assert policy_mod.parse_instruction("别玩了").kind == "stop"
    assert policy_mod.parse_instruction("继续").kind == "resume"
    assert policy_mod.parse_instruction("交给你").kind == "resume"


def test_movement_commands_use_chords() -> None:
    parsed = policy_mod.parse_instruction("往前走")
    assert parsed.kind == "move"
    assert parsed.action == "forward"
    ops = new_policy().command_ops(parsed)
    assert ops[0].kind == "chord"
    assert set(ops[0].keys) == {"w", "shift"}


def test_parkour_and_wallrun_commands() -> None:
    wall = policy_mod.parse_instruction("跑墙")
    assert wall.action == "wallrun"
    ops = new_policy().command_ops(wall)
    assert ops[0].kind == "chord"
    assert set(ops[0].keys) == {"w", "shift", "space"}

    jump = policy_mod.parse_instruction("二段跳")
    assert jump.action == "jump"


def test_action_commands() -> None:
    cases = {
        "按 E": "interact",
        "开门": "interact",
        "登机": "interact",
        "开火": "fire",
        "打它": "fire",
        "换弹": "reload",
        "瞄准": "aim",
        "手雷": "ordnance",
        "近战": "melee",
        "召唤泰坦": "titan",
        "上泰坦": "titan",
        "弹射": "eject",
        "重生": "respawn",
    }
    for text, action in cases.items():
        command = policy_mod.parse_instruction(text)
        assert command.recognized, text
        assert command.action == action, text


def test_unknown_instruction_is_not_guessed() -> None:
    command = policy_mod.parse_instruction("帮我看看这个")
    assert not command.recognized


def test_menu_and_blind_never_press_keys() -> None:
    policy = new_policy()
    menu = policy.decide(frame(), hud(), now=time.time(), menu=True)
    assert menu.state is policy_mod.PolicyState.MENU
    assert menu.ops == []

    blind = policy.decide(frame(black=True), hud(), now=time.time(), menu=False)
    assert blind.state is policy_mod.PolicyState.BLIND
    assert blind.ops == []


def test_objective_marker_steers_and_moves() -> None:
    policy = new_policy(auto_call_titan=False)
    features = frame(
        width=1920,
        height=1080,
        objective_visible=True,
        objective_x=0.72,
        objective_y=0.42,
        objective_score=0.8,
        motion=0.004,
        mean_luma=0.4,
    )
    decision = policy.decide(features, hud(present=True), now=time.time(), menu=False)
    assert decision.state is policy_mod.PolicyState.OBJECTIVE
    keys = op_keys(decision.ops)
    assert "w" in keys
    assert any("任务标记" in reason or reason == "steer-to-marker" for reason in decision.reasons)
    assert any(op.kind == "look" for op in decision.ops)


def test_combat_evidence_fires_and_aims() -> None:
    policy = new_policy(auto_call_titan=False)
    features = frame(
        width=1920,
        height=1080,
        motion=0.02,
        center_motion=0.03,
        center_motion_strength=0.11,
        center_motion_x=0.55,
        center_motion_y=0.48,
        center_red=0.04,
        mean_luma=0.4,
    )
    decision = policy.decide(features, hud(present=True), now=time.time(), menu=False)
    assert decision.state is policy_mod.PolicyState.COMBAT
    keys = op_keys(decision.ops)
    assert "mouse1" in keys
    assert "mouse2" in keys
    assert any("视野中央" in reason for reason in decision.reasons)


def test_localised_centre_motion_can_be_ignored_when_world_moves() -> None:
    policy = new_policy(auto_call_titan=False)
    features = frame(
        motion=0.20,
        center_motion=0.05,
        center_motion_strength=0.11,
    )
    assert policy._combat_evidence(features, hud()) == []


def test_titan_call_hint_presses_v() -> None:
    policy = new_policy(auto_call_titan=True)
    now = time.time() + 20.0
    features = frame(
        motion=0.002,
        center_motion=0.0,
        titan_meter=0.22,
        mean_luma=0.4,
    )
    decision = policy.decide(
        features,
        hud(present=True, titan_ready_hint=True),
        now=now,
        menu=False,
    )
    assert "titan-call" in decision.reasons
    assert "v" in op_keys(decision.ops)


def test_titan_mode_fires_and_uses_core() -> None:
    policy = new_policy(auto_call_titan=False)
    now = time.time()
    features = frame(
        motion=0.02,
        center_motion=0.02,
        center_motion_strength=0.08,
        mean_luma=0.4,
    )
    decision = policy.decide(features, hud(present=True), now=now, menu=False, titan_mode=True)
    assert decision.state is policy_mod.PolicyState.TITAN
    keys = op_keys(decision.ops)
    assert "mouse1" in keys
    assert any(reason in {"titan-core", "titan-ordnance"} for reason in decision.reasons)


def test_stuck_ladder_escalates_without_motion() -> None:
    policy = new_policy(auto_call_titan=False)
    now = time.time()
    features = frame(
        scene_hash=0x1234,
        motion=0.0,
        center_motion=0.0,
        mean_luma=0.3,
    )
    policy._last_hash = features.scene_hash
    policy._last_motion_at = now - 6.0
    decision = policy.decide(features, hud(present=True), now=now, menu=False)
    assert decision.state is policy_mod.PolicyState.STUCK
    assert decision.ops
    assert "stuck" in decision.reasons[0]


def test_wait_directive_does_nothing() -> None:
    policy = new_policy(auto_call_titan=False)
    policy.set_directive(policy_mod.Directive(mode="wait", text="先别动", seconds=30.0))
    decision = policy.decide(frame(mean_luma=0.4), hud(present=True), now=time.time())
    assert decision.state is policy_mod.PolicyState.WAITING
    assert decision.ops == []


def test_look_calibration_is_finite_and_directional() -> None:
    look = policy_mod.LookCalibration(units_per_degree=11.0)
    dx, dy = look.counts_for_delta(0.75, 0.5)
    assert dx > 0
    assert dy == 0
    look.invert_y = True
    _, dy_inverted = look.counts_for_delta(0.5, 0.75)
    assert dy_inverted < 0
