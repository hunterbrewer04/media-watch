"""Turn a source (URL or local path) into a local media file inside the cache.

URLs go through yt-dlp (which knows YouTube, TikTok, Instagram, Facebook, X, Reddit, ...). Local files
are used in place — never copied, never deleted."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .binaries import find, which
from .config import Settings

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpeg", ".mpg", ".3gp"}
# Query params that change per share but not per video (IG igsh, TikTok is_from_webapp, utm_*, ...).
_TRACKING = re.compile(r"^(utm_.*|igsh|igshid|si|feature|is_from_webapp|sender_device|share_.*|_r|_t|t)$")


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fetched:
    path: Path
    key: str            # cache key (sha256 of the canonical source)
    site: str           # "youtube", "instagram", "local", ...
    title: str | None
    url: str | None
    cached: bool


def is_url(source: str) -> bool:
    return urlparse(source.strip()).scheme in ("http", "https")


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    query = "&".join(
        kv for kv in p.query.split("&") if kv and not _TRACKING.match(kv.split("=", 1)[0])
    )
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    return f"{p.scheme}://{host}{p.path.rstrip('/')}" + (f"?{query}" if query else "")


def site_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for name, needles in {
        "youtube": ("youtube.com", "youtu.be"), "instagram": ("instagram.com",), "tiktok": ("tiktok.com",),
        "facebook": ("facebook.com", "fb.watch"), "x": ("twitter.com", "x.com"), "reddit": ("reddit.com", "redd.it"),
        "twitch": ("twitch.tv",), "vimeo": ("vimeo.com",), "snapchat": ("snapchat.com",),
    }.items():
        if any(n in host for n in needles):
            return name
    return host.removeprefix("www.") or "web"


def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def file_key(path: Path) -> str:
    """Content-ish key for local files: size + mtime + head/tail bytes (fast, no full read)."""
    st = path.stat()
    h = hashlib.sha256(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode())
    with path.open("rb") as f:
        h.update(f.read(65536))
        if st.st_size > 65536:
            f.seek(-65536, 2)
            h.update(f.read(65536))
    return h.hexdigest()[:24]


def _hint(site: str, stderr: str) -> str:
    low = stderr.lower()
    if "cannot parse data" in low or "unsupported url" in low:
        return (" — yt-dlp can't read this page format yet (known for Facebook Reels). "
                "Ask the user to save the video and send the file instead.")
    if site in ("instagram", "facebook") and any(
        s in low for s in ("login", "cookies", "rate-limit", "not available", "private", "401", "403")
    ):
        return (f" — {site} usually needs a logged-in cookies.txt. Export one from a browser and save it to "
                f"~/.config/media-watch/cookies.txt (chmod 600), or send the video file instead.")
    if "private" in low:
        return " — the post looks private."
    return ""


def fetch(source: str, settings: Settings) -> Fetched:
    source = source.strip()
    if not source:
        raise FetchError("empty source")

    if not is_url(source):
        path = Path(source).expanduser()
        if not path.is_file():
            raise FetchError(f"no such file: {path}")
        if path.suffix.lower() not in VIDEO_EXTS:
            raise FetchError(f"unsupported file type {path.suffix!r} (want one of {sorted(VIDEO_EXTS)})")
        return Fetched(path=path, key=file_key(path), site="local", title=path.name, url=None, cached=True)

    url = canonical_url(source)
    key = _key(url)
    d = settings.cache_dir / "items" / key
    d.mkdir(parents=True, exist_ok=True)
    meta_path = d / "source.json"
    existing = sorted(p for p in d.glob("source.*") if p.suffix.lower() in VIDEO_EXTS)
    if existing and meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        return Fetched(path=existing[0], key=key, site=meta["site"], title=meta.get("title"), url=url, cached=True)

    site = site_of(url)
    cmd = [
        find("yt-dlp"), "--no-playlist", "--no-progress", "--quiet", "--no-warnings",
        "--max-filesize", "500M",
        # Smallest decent stream is plenty: we downscale to 480p anyway.
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(d / "source.%(ext)s"),
        "--print-json",
        "--match-filter", f"!is_live & duration<=?{settings.max_seconds}",
    ]
    if node := which("node"):
        cmd += ["--js-runtimes", f"node:{node}"]
    if settings.cookies_file:
        cmd += ["--cookies", str(settings.cookies_file)]
    cmd.append(url)

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    files = sorted(p for p in d.glob("source.*") if p.suffix.lower() in VIDEO_EXTS)
    if proc.returncode != 0 or not files:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = err[-1] if err else f"yt-dlp exited {proc.returncode}"
        if not files and proc.returncode == 0:
            msg = f"skipped: longer than {settings.max_seconds}s, a livestream, or not a video"
        raise FetchError(f"download failed ({site}): {msg}{_hint(site, proc.stderr or '')}")

    info: dict = {}
    for line in proc.stdout.splitlines()[::-1]:
        try:
            info = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    meta = {"url": url, "site": site, "title": info.get("title"), "uploader": info.get("uploader"),
            "duration": info.get("duration"), "extractor": info.get("extractor")}
    meta_path.write_text(json.dumps(meta, indent=2))
    return Fetched(path=files[0], key=key, site=site, title=meta["title"], url=url, cached=False)
