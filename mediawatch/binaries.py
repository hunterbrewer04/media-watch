"""Locate external binaries. Services (e.g. a systemd unit) often run with a minimal PATH that lacks
~/.local/bin and Hermes' bundled tools, so fall back to known install locations."""

from __future__ import annotations

import glob
import os
import shutil
from functools import cache
from pathlib import Path


class MissingBinary(RuntimeError):
    pass


def _env_name(name: str) -> str:
    return f"MEDIA_WATCH_{name.upper().replace('-', '_')}"


@cache
def which(name: str) -> str | None:
    """Best path for ``name`` or None. Order: env override → Hermes-bundled → ~/.local → PATH → /usr."""
    env = os.environ.get(_env_name(name))
    if env and os.access(env, os.X_OK):
        return env
    home = Path.home()
    preferred = [
        *sorted(glob.glob(str(home / ".hermes" / "tools" / "ffmpeg-*" / "bin" / name)), reverse=True),
        str(home / ".local" / "bin" / name),
        str(home / ".hermes" / "node" / "bin" / name),
    ]
    for path in preferred:
        if os.access(path, os.X_OK):
            return path
    if found := shutil.which(name):
        return found
    for path in (f"/usr/local/bin/{name}", f"/usr/bin/{name}"):
        if os.access(path, os.X_OK):
            return path
    return None


def find(name: str) -> str:
    path = which(name)
    if path is None:
        raise MissingBinary(f"{name} not found (install it or set {_env_name(name)} to its path)")
    return path
