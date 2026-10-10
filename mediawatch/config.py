"""Runtime settings. Everything is read from the environment (or an injected getter) so no secret
or machine-specific path ever lives in the repo."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULT_MODELS: dict[str, str] = {
    # Picked from a real bake-off on 2026-10-09 (see README "Model choice").
    "fast": "google/gemini-3.1-flash-lite",    # cheapest; hallucinated details in testing
    "normal": "google/gemini-3.8-flash",       # default: accurate + fast
    "high": "google/gemini-3.1-pro-preview",   # slow/expensive, for when it matters
}
DEFAULT_FALLBACK = "qwen/qwen3.8-omni-flash"   # used once when the primary provider errors

Getter = Callable[[str], "str | None"]


def _default_cache_dir() -> Path:
    # One shared cache across every Hermes profile; the usage log's `caller` column tells them apart.
    return Path.home() / ".hermes" / "cache" / "media-watch"


def _default_cookies() -> Path:
    return Path.home() / ".config" / "media-watch" / "cookies.txt"


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    cache_dir: Path = field(default_factory=_default_cache_dir)
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    fallback_model: str | None = DEFAULT_FALLBACK
    max_seconds: int = 600            # refuse anything longer (cost guard for random links)
    proxy_short_side: int = 480       # px; keeps scoreboards / captions readable
    proxy_fps: int = 4                # Gemini samples ~1 fps; a few fps keeps motion legible and files small
    # base64 inflates the file ~33%: 14 MiB -> ~19 MB request, under Gemini's ~20 MB inline limit
    proxy_max_bytes: int = 14 * 1024 * 1024
    request_timeout: float = 180.0
    cookies_file: Path | None = None
    base_url: str = "https://openrouter.ai/api/v1"

    @classmethod
    def from_env(cls, getenv: Getter | None = None, **overrides: object) -> Settings:
        get: Getter = getenv or os.environ.get

        def val(name: str) -> str | None:
            v = get(name)
            return v.strip() if isinstance(v, str) and v.strip() else None

        models = dict(DEFAULT_MODELS)
        for tier in models:
            if m := val(f"MEDIA_WATCH_MODEL_{tier.upper()}"):
                models[tier] = m
        fallback_raw = get("MEDIA_WATCH_FALLBACK_MODEL")
        fallback = DEFAULT_FALLBACK if fallback_raw is None else (fallback_raw.strip() or None)

        cookies_env = val("MEDIA_WATCH_COOKIES")
        cookies = Path(cookies_env).expanduser() if cookies_env else _default_cookies()
        home_env = val("MEDIA_WATCH_HOME")
        settings = cls(
            api_key=val("OPENROUTER_API_KEY"),
            cache_dir=Path(home_env).expanduser() if home_env else _default_cache_dir(),
            models=models,
            fallback_model=fallback,
            max_seconds=int(val("MEDIA_WATCH_MAX_SECONDS") or 600),
            cookies_file=cookies if cookies.is_file() else None,
        )
        return replace(settings, **overrides) if overrides else settings  # type: ignore[arg-type]

    def model_for(self, detail: str) -> str:
        if detail not in self.models:
            raise ValueError(f"detail must be one of {sorted(self.models)}, got {detail!r}")
        return self.models[detail]


def load_dotenv_key(name: str, path: Path | None = None) -> str | None:
    """Read one key from Hermes' .env without exporting the rest (CLI convenience only)."""
    path = path or Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / ".env"
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            k, sep, v = line.partition("=")
            if sep and k.strip() == name:
                return v.strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None
