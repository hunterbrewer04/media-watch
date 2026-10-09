from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mediawatch.binaries import which
from mediawatch.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(api_key="test-key", cache_dir=tmp_path / "cache", fallback_model="fallback/model")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """A synthetic 4s 1280x720 clip with audio — no real footage checked into the repo."""
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
         "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000", "-t", "4",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )
    return out
