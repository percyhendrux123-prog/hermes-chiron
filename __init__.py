"""Chiron: an advisor who is also a friend.

Reads how much the person knows about the current topic (from their own words, no model call) and adds
one short note per turn telling the model how to talk to them. Off per session until `/chiron on`
(or always_on: true in config). Floor at every level: says what a risky step will do and waits for yes.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .core import Store, build_note, command, read_turn

logger = logging.getLogger(__name__)
PLUGIN_ID = "chiron"


def _data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore
        return Path(plugin_data_dir(PLUGIN_ID))
    except Exception:
        home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        return Path(home) / "plugin-data" / PLUGIN_ID


def register(ctx):
    store = Store(_data_dir() / "sessions.json")

    def always_on() -> bool:
        try:
            return bool(ctx.get_config("always_on", False))
        except Exception:
            return False

    def pre_llm_call(session_id: str = "", user_message: str = "", **_):
        try:
            if not session_id:
                return None
            msg = user_message if isinstance(user_message, str) else str(user_message or "")
            if msg.strip().startswith("/"):
                return None
            with store.locked():  # one cross-process transaction: read, apply the turn, write
                st = store._get(session_id)
                if always_on():
                    st.on = True
                if not st.on:
                    return None
                st = read_turn(st, msg)
                store._put(session_id, st)
            return {"context": build_note(st)}
        except Exception:
            logger.debug("chiron pre_llm_call failed", exc_info=True)
            return None

    def chiron_cmd(raw_args: str = "", session_id: str = "", **_):
        return command(store, session_id or "default", raw_args, always_on())

    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_command("chiron", chiron_cmd, description="Chiron advisor: on/off/status/level/reset",
                         args_hint="[on|off|status|reset|level <topic> <level>]")
