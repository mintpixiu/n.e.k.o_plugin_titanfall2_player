"""A strict stand-in for ``plugin.sdk.plugin`` used by the offline test suite.

This is **not** a mock of convenience: its argument lists mirror the real SDK
signatures (read from ``plugin/sdk/plugin/{decorators,llm_tool,ui,base}.py``)
and it raises ``TypeError`` on any unknown keyword.  A typo such as
``llm_result_field=`` instead of ``llm_result_fields=`` therefore fails the test
suite rather than failing at plugin load time in the user's N.E.K.O.

``push_message`` re-implements the documented ``push_message.v2`` validation
(``visibility`` in {chat,hud}, ``ai_behavior`` in {respond,read,blind}, parts
discriminated by ``type``) so the test suite proves the plugin only ever emits
messages the host will accept.
"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


class SdkError(Exception):
    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Result:
    def __init__(self, *, ok: bool, value: Any = None, error: Any = None) -> None:
        self.ok = ok
        self.value = value
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Result(ok={self.ok}, value={self.value!r}, error={self.error!r})"


def Ok(value: Any = None) -> Result:  # noqa: N802 - mirrors the SDK name
    return Result(ok=True, value=value)


def Err(error: Any = None) -> Result:  # noqa: N802 - mirrors the SDK name
    return Result(ok=False, error=error)


# --------------------------------------------------------------------------
# push_message.v2 schema (mirrors plugin/sdk/shared/core/push_message_schema.py)
# --------------------------------------------------------------------------

VISIBILITY_VALUES = ("chat", "hud")
AI_BEHAVIOR_VALUES = ("respond", "read", "blind")
PART_TYPES = ("html_card", "text", "image", "audio", "video", "ui_action")


def validate_push_message(**kwargs: Any) -> None:
    visibility = kwargs.get("visibility")
    if visibility is not None:
        if not isinstance(visibility, (list, tuple)):
            raise TypeError("visibility must be a list of strings")
        for entry in visibility:
            if not isinstance(entry, str):
                raise TypeError("visibility entries must be str")
            if entry not in VISIBILITY_VALUES:
                raise ValueError(f"visibility entry {entry!r} not in {VISIBILITY_VALUES}")
    ai_behavior = kwargs.get("ai_behavior")
    if ai_behavior is not None and ai_behavior not in AI_BEHAVIOR_VALUES:
        raise ValueError(f"ai_behavior must be one of {AI_BEHAVIOR_VALUES}, got {ai_behavior!r}")
    priority = kwargs.get("priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise TypeError("priority must be an int")
    coalesce = kwargs.get("coalesce_key")
    if coalesce is not None and not isinstance(coalesce, str):
        raise TypeError("coalesce_key must be a str")
    source = kwargs.get("source")
    if source is not None and not isinstance(source, str):
        raise TypeError("source must be a str")
    target = kwargs.get("target_lanlan")
    if target is not None and not isinstance(target, str):
        raise TypeError("target_lanlan must be a str or None")

    parts = kwargs.get("parts")
    if parts is None:
        if kwargs.get("content") is None and not kwargs.get("binary_data"):
            raise ValueError("push_message needs at least one of parts/content/binary_data")
        return
    if not isinstance(parts, (list, tuple)):
        raise TypeError("parts must be a list")
    for part in parts:
        if not isinstance(part, dict):
            raise TypeError(f"part must be a dict, got {type(part).__name__}")
        kind = part.get("type")
        if kind not in PART_TYPES:
            raise ValueError(f"part type {kind!r} not in {PART_TYPES}")
        if kind == "text" and not isinstance(part.get("text"), str):
            raise TypeError("text part needs a str 'text'")
        if kind in ("image", "audio", "video"):
            has_inline = isinstance(part.get("data"), (bytes, bytearray))
            has_url = isinstance(part.get("url"), str)
            if not (has_inline or has_url):
                raise TypeError(f"{kind} part needs 'data' bytes or a 'url' str")
            mime = part.get("mime")
            if not isinstance(mime, str) or "/" not in mime:
                raise TypeError(f"{kind} part needs a 'mime' string")


# --------------------------------------------------------------------------
# Decorators (strict signatures)
# --------------------------------------------------------------------------

ENTRY_META_ATTR = "__stub_entry_meta__"
LLM_TOOL_META_ATTR = "__stub_llm_tool_meta__"
LIFECYCLE_META_ATTR = "__stub_lifecycle_meta__"
UI_ACTION_META_ATTR = "__stub_ui_action_meta__"
UI_CONTEXT_META_ATTR = "__stub_ui_context_meta__"
TIMER_META_ATTR = "__stub_timer_meta__"

_LIFECYCLE_IDS = {"startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"}
_ENTRY_KINDS = {"action", "tool", "event", "custom"}


def _reject_unknown(kwargs: dict[str, Any], allowed: set[str], decorator: str) -> None:
    unknown = set(kwargs) - allowed
    if unknown:
        raise TypeError(
            f"{decorator}() got unexpected keyword argument(s): {sorted(unknown)}; allowed: {sorted(allowed)}"
        )


def _require_async(fn, decorator: str) -> None:
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(
            f"{decorator} handler {fn.__qualname__!r} must be 'async def': the host "
            "invokes runtime-triggered entries on the asyncio command loop"
        )


def neko_plugin(cls):
    """Mark a class as a plugin. Real SDK: ``_neko_plugin``.

    A class-level ``name`` is deliberately *not* required: plugin identity comes
    from ``plugin.toml``'s ``[plugin].id``, and shipped plugins such as
    ``game_agent_minecraft`` declare no class attribute at all.
    """
    if not isinstance(cls, type):
        raise TypeError("neko_plugin must decorate a class")
    cls.__stub_neko_plugin__ = True
    return cls


def plugin_entry(
    *,
    id: str | None = None,
    name: object | None = None,
    description: object = "",
    input_schema: dict[str, object] | None = None,
    params: type | None = None,
    kind: str = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    model_validate: bool = True,
    timeout: float | None = None,
    llm_result_fields: list[str] | None = None,
    llm_result_model: type | None = None,
    fields: type | None = None,
    metadata: dict[str, object] | None = None,
    quick_action: bool = False,
    _localns: dict[str, object] | None = None,
):
    _reject_unknown(
        locals(),
        {
            "id",
            "name",
            "description",
            "input_schema",
            "params",
            "kind",
            "auto_start",
            "persist",
            "model_validate",
            "timeout",
            "llm_result_fields",
            "llm_result_model",
            "fields",
            "metadata",
            "quick_action",
            "_localns",
        },
        "plugin_entry",
    )
    if not id:
        raise ValueError("plugin_entry requires an explicit 'id'")
    if kind not in _ENTRY_KINDS:
        raise ValueError(f"plugin_entry kind must be one of {sorted(_ENTRY_KINDS)}")
    if input_schema is not None:
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            raise ValueError("plugin_entry input_schema must be a JSON-Schema object with type='object'")
    if llm_result_fields is not None and not isinstance(llm_result_fields, list):
        raise ValueError("llm_result_fields must be a list of field names")

    def decorator(fn):
        _require_async(fn, "plugin_entry")
        setattr(
            fn,
            ENTRY_META_ATTR,
            {
                "id": id,
                "name": name or id,
                "description": description,
                "input_schema": input_schema or {"type": "object", "properties": {}},
                "kind": kind,
                "auto_start": auto_start,
                "timeout": timeout,
                "llm_result_fields": llm_result_fields,
                "quick_action": quick_action,
                "metadata": metadata or {},
            },
        )
        return fn

    return decorator


def lifecycle(
    *,
    id: str,
    name: str | None = None,
    description: str = "",
    metadata: dict[str, object] | None = None,
):
    _reject_unknown(locals(), {"id", "name", "description", "metadata"}, "lifecycle")
    if id not in _LIFECYCLE_IDS:
        raise ValueError(f"lifecycle id must be one of {sorted(_LIFECYCLE_IDS)}, got {id!r}")

    def decorator(fn):
        _require_async(fn, "lifecycle")
        setattr(
            fn,
            LIFECYCLE_META_ATTR,
            {"id": id, "name": name or id, "description": description, "metadata": metadata or {}},
        )
        return fn

    return decorator


def llm_tool(
    *,
    name: str | None = None,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    timeout: float = 30.0,
    role: str | None = None,
):
    _reject_unknown(locals(), {"name", "description", "parameters", "timeout", "role"}, "llm_tool")
    if parameters is not None and (not isinstance(parameters, dict) or parameters.get("type") != "object"):
        raise ValueError("llm_tool parameters must be a JSON-Schema object with type='object'")
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("llm_tool timeout must be a positive number")

    def decorator(fn):
        _require_async(fn, "llm_tool")
        setattr(
            fn,
            LLM_TOOL_META_ATTR,
            {
                "name": name or fn.__name__,
                "description": description,
                "parameters": parameters or {"type": "object", "properties": {}},
                "timeout": float(timeout),
                "role": role,
            },
        )
        return fn

    return decorator


def timer_interval(
    *,
    id: str,
    seconds: int,
    name: str | None = None,
    description: str = "",
    auto_start: bool = True,
    metadata: dict[str, object] | None = None,
):
    """Mirror of the real ``timer_interval`` (``plugin/sdk/plugin/decorators.py``).

    The real decorator is metadata-only on the plugin side: the *host* schedules
    the timer from the registered meta.  It is recorded here anyway so the test
    suite can assert the declared interval rather than trusting the source.
    """
    _reject_unknown(locals(), {"id", "seconds", "name", "description", "auto_start", "metadata"}, "timer_interval")
    if not isinstance(id, str) or not id:
        raise ValueError("timer_interval requires a non-empty id")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        raise ValueError(f"timer_interval seconds must be a positive int, got {seconds!r}")

    def decorator(fn):
        _require_async(fn, "timer_interval")
        setattr(
            fn,
            TIMER_META_ATTR,
            {
                "id": id,
                "seconds": int(seconds),
                "name": name or id,
                "description": description,
                "auto_start": bool(auto_start),
                "metadata": metadata or {},
            },
        )
        return fn

    return decorator


class _UiNamespace:
    """Mirrors ``plugin.sdk.plugin.ui`` (a module with ``context``/``action``)."""

    @staticmethod
    def context(*, id: str = "main", title: str | None = None):
        _reject_unknown(locals(), {"id", "title"}, "ui.context")

        def decorator(fn):
            setattr(fn, UI_CONTEXT_META_ATTR, {"id": id, "title": title})
            return fn

        return decorator

    @staticmethod
    def action(
        *,
        id: str | None = None,
        label: object | None = None,
        icon: str | None = None,
        tone: str = "default",
        group: str | None = None,
        order: int = 0,
        confirm: object = False,
        refresh_context: bool = True,
    ):
        _reject_unknown(
            locals(), {"id", "label", "icon", "tone", "group", "order", "confirm", "refresh_context"}, "ui.action"
        )

        def decorator(fn):
            setattr(
                fn,
                UI_ACTION_META_ATTR,
                {
                    "id": id,
                    "label": label,
                    "icon": icon,
                    "tone": tone,
                    "group": group,
                    "order": int(order),
                    "confirm": confirm,
                    "refresh_context": bool(refresh_context),
                },
            )
            return fn

        return decorator


ui = _UiNamespace()


# --------------------------------------------------------------------------
# Context + base class
# --------------------------------------------------------------------------


class StubConfig:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.dump_calls = 0

    async def dump(self, *, timeout: float = 5.0) -> dict[str, Any]:
        if not isinstance(timeout, (int, float)):
            raise TypeError("config.dump(timeout=...) must be a number")
        self.dump_calls += 1
        return dict(self._payload)


class StubLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str, message: str, *args: Any) -> None:
        try:
            text = message.format(*args) if args else message
        except Exception:
            text = f"{message} {args!r}"
        self.records.append((level, text))

    def info(self, message: str, *args: Any) -> None:
        self._record("info", message, *args)

    def warning(self, message: str, *args: Any) -> None:
        self._record("warning", message, *args)

    def debug(self, message: str, *args: Any) -> None:
        self._record("debug", message, *args)

    def exception(self, message: str, *args: Any) -> None:
        self._record("exception", message, *args)

    def error(self, message: str, *args: Any) -> None:
        self._record("error", message, *args)


class StubContext:
    """Implements just enough of ``PluginContextProtocol`` for the plugin."""

    def __init__(self, *, plugin_id: str = "icey_player", config: dict[str, Any] | None = None) -> None:
        self.plugin_id = plugin_id
        self.config_path = f"/stub/{plugin_id}/plugin.toml"
        self.metadata: dict[str, Any] = {}
        self.logger = StubLogger()
        self.config = StubConfig(config or {})
        self.messages: list[dict[str, Any]] = []
        self.lifecycle_calls: list[str] = []

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        validate_push_message(**kwargs)
        self.messages.append(kwargs)
        return {"submitted": True}


class NekoPluginBase:
    """Mirrors ``plugin.sdk.plugin.base.NekoPluginBase``.

    The path helpers are part of the real public surface
    (``plugin/sdk/plugin/base.py``): ``plugin_dir`` / ``config_dir`` /
    ``storage_dir`` / ``runtime_config_path`` properties plus ``data_path()``
    and ``cache_path()``.  They are stubbed onto a temp directory so plugins
    that write diagnostics can be exercised in tests.
    """

    name = ""

    def __init__(self, ctx: StubContext) -> None:
        self.ctx = ctx
        self.config = ctx.config
        self.logger = ctx.logger
        self.enable_file_logging_calls: list[dict[str, Any]] = []
        self._stub_root = Path(tempfile.gettempdir()) / "neko-stub" / str(ctx.plugin_id)

    # -- path helpers (mirror the real SDK) -------------------------------

    @property
    def plugin_id(self) -> str:
        return str(getattr(self.ctx, "plugin_id", "plugin"))

    @property
    def plugin_dir(self) -> Path:
        return self._stub_root / "plugin"

    @property
    def config_dir(self) -> Path:
        return self.plugin_dir

    @property
    def storage_dir(self) -> Path:
        return self._stub_root / "storage"

    @property
    def runtime_config_path(self) -> Path:
        return self.storage_dir / "config.toml"

    def data_path(self, *parts: str) -> Path:
        base = self._stub_root / "data"
        base.mkdir(parents=True, exist_ok=True)
        return base.joinpath(*parts) if parts else base

    def cache_path(self, *parts: str) -> Path:
        base = self._stub_root / "cache"
        base.mkdir(parents=True, exist_ok=True)
        return base.joinpath(*parts) if parts else base

    @property
    def metadata(self) -> dict[str, Any]:
        value = getattr(self.ctx, "metadata", {})
        return dict(value) if isinstance(value, Mapping) else {}

    def enable_file_logging(self, *, log_level: str = "INFO", **_ignored: Any):
        if not isinstance(log_level, str) or not log_level:
            raise ValueError("log_level must be a non-empty string")
        if log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(f"unknown log level: {log_level!r}")
        self.enable_file_logging_calls.append({"log_level": log_level})
        return self.logger

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        return self.ctx.push_message(**kwargs)

    # -- introspection used by the tests ---------------------------------

    def collect_entries(self) -> dict[str, Any]:
        entries: dict[str, Any] = {}
        for _name, member in inspect.getmembers(type(self)):
            meta = getattr(member, ENTRY_META_ATTR, None)
            if meta:
                entries[meta["id"]] = {"meta": meta, "handler": getattr(self, _name)}
        return entries

    def collect_lifecycles(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for _name, member in inspect.getmembers(type(self)):
            meta = getattr(member, LIFECYCLE_META_ATTR, None)
            if meta:
                found[meta["id"]] = getattr(self, _name)
        return found

    def collect_llm_tools(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for _name, member in inspect.getmembers(type(self)):
            meta = getattr(member, LLM_TOOL_META_ATTR, None)
            if meta:
                found[meta["name"]] = {"meta": meta, "handler": getattr(self, _name)}
        return found

    def collect_ui_actions(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for _name, member in inspect.getmembers(type(self)):
            meta = getattr(member, UI_ACTION_META_ATTR, None)
            if meta:
                found[_name] = meta
        return found

    def collect_timers(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for _name, member in inspect.getmembers(type(self)):
            meta = getattr(member, TIMER_META_ATTR, None)
            if meta:
                found[meta["id"]] = {"meta": meta, "handler": getattr(self, _name)}
        return found


async def run(coro):
    """Small helper so tests can drive an entry handler from sync code."""
    return await coro


def run_sync(coro):
    return asyncio.run(coro)


__all__ = [
    "AI_BEHAVIOR_VALUES",
    "ENTRY_META_ATTR",
    "Err",
    "LIFECYCLE_META_ATTR",
    "LLM_TOOL_META_ATTR",
    "NekoPluginBase",
    "Ok",
    "PART_TYPES",
    "Result",
    "SdkError",
    "StubContext",
    "StubLogger",
    "TIMER_META_ATTR",
    "UI_ACTION_META_ATTR",
    "VISIBILITY_VALUES",
    "lifecycle",
    "llm_tool",
    "neko_plugin",
    "plugin_entry",
    "run_sync",
    "timer_interval",
    "ui",
    "validate_push_message",
]
