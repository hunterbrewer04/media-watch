"""ffprobe + a small proxy the model can watch cheaply (480p short side, ≤15 fps by default, mono 48 kbps audio)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .binaries import find
from .config import Settings


class PrepError(RuntimeError):
    pass


@dataclass(frozen=True)
class Probe:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    size_bytes: int


def probe(path: Path) -> Probe:
    out = subprocess.run(
        [find("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise PrepError(f"ffprobe failed: {out.stderr.strip()[-300:]}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise PrepError("no video stream")
    num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    return Probe(
        duration=duration, width=int(v.get("width") or 0), height=int(v.get("height") or 0), fps=round(fps, 3),
        has_audio=any(s.get("codec_type") == "audio" for s in streams), size_bytes=path.stat().st_size,
    )


def make_proxy(src: Path, dest_dir: Path, settings: Settings, info: Probe | None = None) -> Path:
    """Write ``proxy.mp4`` into ``dest_dir`` (reused if already there)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "proxy.mp4"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    info = info or probe(src)
    side = settings.proxy_short_side
    # Scale the short side to `side` (never upscale), keep even dimensions.
    if info.width >= info.height:
        vf = f"scale=-2:'min({side},ih)'"
    else:
        vf = f"scale='min({side},iw)':-2"
    crf = 30
    tmp = dest.with_suffix(".part.mp4")
    for _ in range(3):
        cmd = [find("ffmpeg"), "-y", "-v", "error", "-i", str(src),
               "-vf", f"{vf},fps='min({settings.proxy_fps},source_fps)'",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", "-threads", "2"]
        cmd += ["-c:a", "aac", "-ac", "1", "-b:a", "48k"] if info.has_audio else ["-an"]
        cmd.append(str(tmp))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise PrepError(f"ffmpeg proxy failed: {proc.stderr.strip()[-400:]}")
        if tmp.stat().st_size <= settings.proxy_max_bytes:
            tmp.replace(dest)
            return dest
        crf += 5  # too big for an inline request: squeeze harder
    tmp.unlink(missing_ok=True)
    raise PrepError(f"proxy still over {settings.proxy_max_bytes // 2**20} MB — video too long for one request")
