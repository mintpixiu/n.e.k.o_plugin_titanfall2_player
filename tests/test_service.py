"""Service tests with fake game/capture/input — no real screen or SendInput."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _loader  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

catalog = _loader.catalog
discovery = _loader.discovery
service_mod = _loader.service


class FakeFrame:
    def __init__(self, image):
        self.image = image
        self.captured_at = time.time()
        self.backend = "fake"
        self.region = (0, 0, 320, 180)

    @property
    def width(self):
        return self.image.shape[1]

    @property
    def height(self):
        return self.image.shape[0]

    def summary(self):
        return {"backend": "fake", "width": self.width, "height": self.height}


class FakeCapturer:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.last_error = ""
        self.frames = []
        FakeCapturer.instances.append(self)

    def capture(self, region, retries=1):
        return FakeFrame(self.frames.pop(0) if self.frames else self._image())

    @staticmethod
    def _image():
        image = np.full((180, 320, 3), 40, dtype=np.uint8)
        image[60:120, 100:220] = 120
        return image

    def probe(self):
        return {"fake": {"ok": True}}

    def status(self):
        return {"backend": "fake", "active": True, "frames_captured": 1}

    def close(self):
        pass


class FakeInjector:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.held = set()
        self.buttons = set()
        self.events = []
        self.human_keys = []
        self.released = 0
        self.dry_run = bool(kwargs.get("dry_run"))
        self.require_foreground = bool(kwargs.get("require_foreground"))
        FakeInjector.instances.append(self)

    @property
    def available(self):
        return True

    @property
    def held_keys(self):
        return tuple(sorted(self.held))

    @property
    def held_buttons(self):
        return tuple(sorted(self.buttons))

    def layout_is_valid(self):
        return True

    def stats(self):
        return {
            "available": True,
            "held_keys": list(self.held),
            "dry_run": self.dry_run,
            "events_sent": len(self.events),
        }

    def key_down(self, key):
        self.held.add(str(key).upper())
        self.events.append(("down", str(key)))

    def key_up(self, key):
        self.held.discard(str(key).upper())
        self.events.append(("up", str(key)))

    def tap(self, key, hold_seconds=0.05):
        self.events.append(("tap", str(key)))

    def hold_for(self, key, seconds):
        self.events.append(("hold", str(key), seconds))

    def look(self, dx, dy):
        self.events.append(("look", float(dx), float(dy)))

    def click(self, button="left", hold_seconds=0.04):
        self.events.append(("click", button))

    def mouse_down(self, button="left"):
        self.buttons.add(str(button))
        self.events.append(("mouse_down", button))

    def mouse_up(self, button="left"):
        self.buttons.discard(str(button))
        self.events.append(("mouse_up", button))

    def release_all(self):
        self.released += 1
        self.held.clear()
        self.buttons.clear()

    def physical_keys_down(self, include_mouse=True):
        return list(self.human_keys)

    @staticmethod
    def pause_hotkey_down():
        return False


class FakeWindow:
    hwnd = 0x1234
    pid = 999
    title = "Titanfall 2"
    visible = True
    minimized = False
    rect = (0, 0, 320, 180)

    def is_usable(self, min_edge=160):
        return True

    @property
    def width(self):
        return 320

    @property
    def height(self):
        return 180

    def summary(self):
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "rect": list(self.rect),
        }


@pytest.fixture()
def service_env(monkeypatch):
    state = {"running": False, "menu": False, "foreground": True}

    def find_running_games():
        if not state["running"]:
            return []
        return [
            discovery.RunningGame(
                profile=catalog.GAME_BY_ID["titanfall2"],
                pid=999,
                exe_path=r"E:\Games\Titanfall2\Titanfall2.exe",
                window=FakeWindow(),
            )
        ]

    monkeypatch.setattr(service_mod.discovery, "find_running_games", find_running_games)
    monkeypatch.setattr(
        service_mod.discovery,
        "installed_games",
        lambda refresh=False: {"titanfall2": object()},
    )
    monkeypatch.setattr(service_mod.discovery, "is_foreground", lambda hwnd: state["foreground"])
    monkeypatch.setattr(service_mod.discovery, "menu_active", lambda hwnd: state["menu"])
    monkeypatch.setattr(service_mod.discovery, "cursor_visible", lambda: state["menu"])
    monkeypatch.setattr(
        service_mod.discovery,
        "client_rect_on_screen",
        lambda hwnd: (0, 0, 320, 180),
    )
    monkeypatch.setattr(service_mod.capture_mod, "ScreenCapturer", FakeCapturer)
    monkeypatch.setattr(service_mod.capture_mod, "encode_jpeg", lambda *a, **k: b"jpeg")
    monkeypatch.setattr(service_mod.input_mod, "KeyInjector", FakeInjector)
    FakeCapturer.instances.clear()
    FakeInjector.instances.clear()

    service = service_mod.TitanfallService(logger=None, push_message_fn=None)
    service.configure(
        {
            "dry_run": False,
            "autopilot_tick_seconds": 0.2,
            "commentary_interval_seconds": 999,
            "frame_stream_interval_seconds": 999,
        }
    )
    return service, state


@pytest.mark.asyncio
async def test_play_without_game_waits_and_does_not_press(service_env):
    service, _state = service_env
    await service.start()
    try:
        result = service.play(catalog.GAME_BY_ID["titanfall2"])
        assert result["running"] is False
        assert service._mode == "waiting"
        await service._control_tick()
        assert service._mode == "waiting"
        assert FakeInjector.instances[0].events == []
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_menu_does_not_press(service_env):
    service, state = service_env
    state["running"] = True
    state["menu"] = True
    await service.start()
    try:
        service.play(catalog.GAME_BY_ID["titanfall2"])
        for _ in range(4):
            await service._control_tick()
        injector = FakeInjector.instances[-1]
        assert injector.events == []
        assert service._mode == "waiting"
        assert not service._has_entered_gameplay
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_gameplay_starts_and_controls(service_env):
    service, state = service_env
    state["running"] = True
    await service.start()
    try:
        service.play(catalog.GAME_BY_ID["titanfall2"])
        for _ in range(5):
            await service._control_tick()
        injector = FakeInjector.instances[-1]
        assert service._has_entered_gameplay
        assert service._mode == "playing"
        assert injector.events
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_human_takes_over_and_releases_keys(service_env):
    service, state = service_env
    state["running"] = True
    await service.start()
    try:
        service.play(catalog.GAME_BY_ID["titanfall2"])
        for _ in range(3):
            await service._control_tick()
        injector = FakeInjector.instances[-1]
        injector.human_keys = ["A"]
        await service._control_tick()
        assert service._mode == "soft_yield"
        assert injector.held == set()
        assert injector.released >= 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_stop_releases_everything(service_env):
    service, state = service_env
    state["running"] = True
    await service.start()
    try:
        service.play(catalog.GAME_BY_ID["titanfall2"])
        for _ in range(3):
            await service._control_tick()
        injector = FakeInjector.instances[-1]
        service.stop_playing()
        assert not service.cfg.autopilot_enabled
        assert injector.held == set()
        assert service._pause_latched
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_resume_requires_selected_game(service_env):
    service, _state = service_env
    result = service.resume_playing()
    assert not result["ok"]


@pytest.mark.asyncio
async def test_sequence_runs_chord_aim_and_fire(service_env):
    service, state = service_env
    state["running"] = True
    await service.start()
    try:
        service.play(catalog.GAME_BY_ID["titanfall2"])
        for _ in range(3):
            await service._control_tick()
        injector = FakeInjector.instances[-1]
        injector.events.clear()
        result = await service.run_sequence(
            [
                {"action": "forward", "seconds": 0.1},
                {"action": "aim", "x": 0.6, "y": 0.4},
                {"action": "shoot", "button": "left"},
                {"action": "chord", "keys": ["w", "space"], "seconds": 0.1},
            ]
        )
        assert result["ok"]
        kinds = [event[0] for event in injector.events]
        assert "down" in kinds
        assert "look" in kinds
        assert "click" in kinds
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_titan_directive_sets_mode(service_env):
    service, state = service_env
    state["running"] = True
    await service.start()
    try:
        service.play(catalog.GAME_BY_ID["titanfall2"])
        for _ in range(3):
            await service._control_tick()
        result = service.set_directive(mode="titan", goal="打对面泰坦", steps="开火；放副武器", seconds=30)
        assert result["accepted"]
        assert service._titan_mode
        await service._control_tick()
        status = service.get_status()
        assert status["titan_mode"] is True
        assert status["policy"]["state"] == "titan"
    finally:
        await service.stop()


def test_capture_region_never_uses_icon_sized_window(service_env):
    service, _state = service_env
    # The fake window is usable; this checks the guard path does not crash even
    # before a real game exists.
    assert service._capture_region() is None
