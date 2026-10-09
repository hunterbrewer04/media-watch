"""Hermes plugin: registers the ``watch_video`` tool (toolset ``media``).

Thin wrapper — all logic lives in the ``mediawatch`` package one directory up. The plugin dir is
symlinked into ``~/.hermes/plugins/media-watch``, so resolve the real path to find the repo root."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

TOOLSET = "media"

WATCH_VIDEO_SCHEMA = {
    "name": "watch_video",
    "description": (
        "Watch a video and get a structured report of what's in it — visuals, on-screen text, speech, "
        "music/sound, timestamps. Use this whenever the user shares a video link (Instagram reel, TikTok, "
        "YouTube/Shorts, Facebook, X, Reddit, Twitch clip, Vimeo, direct .mp4) or a local video file and "
        "wants to know what's in it, or asks a question about it. Pass their question in `question`. "
        "Follow-up questions about the same video are cheap (the video is cached). Max ~10 min per video. "
        "Instagram/Facebook may fail without a cookies file; if so, ask the user to send the file instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string",
                       "description": "Video URL, or an absolute local file path (e.g. an attachment path)."},
            "question": {"type": "string",
                         "description": "What the user wants to know. Omit for a general summary."},
            "detail": {"type": "string", "enum": ["fast", "normal", "high"],
                       "description": "normal (default) = Gemini Flash. high = Gemini Pro, slower/~5x cost, for "
                                      "when accuracy really matters. fast = cheapest, can hallucinate details."},
        },
        "required": ["source"],
    },
}


def _getenv(name: str) -> str | None:
    try:
        from agent.secret_scope import get_secret  # profile-aware (.env of the active profile)
        val = get_secret(name)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(name)


def _caller() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name
        return get_active_profile_name() or "default"
    except Exception:
        return os.environ.get("HERMES_PROFILE_NAME") or "unknown"


def _check() -> bool:
    return bool(_getenv("OPENROUTER_API_KEY"))


def _handle(args: dict) -> str:
    from mediawatch import Settings, WatchError, watch

    source = str(args.get("source") or "").strip()
    if not source:
        return json.dumps({"error": "source is required (a video URL or local file path)"})
    detail = str(args.get("detail") or "normal")
    try:
        result = watch(source, question=args.get("question") or None, detail=detail,
                       settings=Settings.from_env(getenv=_getenv), caller=_caller())
    except WatchError as e:
        return json.dumps({"error": str(e)})
    out = result.to_dict()
    out["note"] = ("Report from a video model that watched the file. Timestamps are seconds from the start. "
                   "Items under report.uncertain are the model's own guesses — say so if you use them.")
    return json.dumps(out, ensure_ascii=False)


def register(ctx) -> None:
    ctx.register_tool(
        name="watch_video", toolset=TOOLSET, schema=WATCH_VIDEO_SCHEMA, handler=_handle,
        check_fn=_check, requires_env=["OPENROUTER_API_KEY"],
        description="Watch a video (link or file) and report what's in it", emoji="🎞️",
    )
