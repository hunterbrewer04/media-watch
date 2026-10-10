from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediawatch import fetch as fetch_mod
from mediawatch.config import Settings
from mediawatch.fetch import _downloaded, canonical_url, fetch, site_of
from mediawatch.prep import make_proxy, probe


@pytest.mark.parametrize("url,site", [
    ("https://netflix.com/title/1", "netflix.com"), ("https://dropbox.com/s/x/v.mp4", "dropbox.com"),
    ("https://sub.youtube.com/watch?v=1", "youtube"), ("https://m.youtube.com/watch?v=1", "youtube"),
    ("https://youtu.be/x", "youtube"),
])
def test_site_of_matches_domains_not_substrings(url, site):
    assert site_of(url) == site


def test_fetch_cache_hit_ignores_yt_dlp_part_files(settings: Settings):
    url = "https://youtube.com/watch?v=abc"
    d = settings.cache_dir / "items" / fetch_mod._key(canonical_url(url))
    d.mkdir(parents=True)
    (d / "source.f137.mp4").write_bytes(b"video-only")
    (d / "source.mp4").write_bytes(b"merged")
    (d / "source.json").write_text(json.dumps({"site": "youtube", "title": "t"}))
    got = fetch(url, settings)
    assert got.path.name == "source.mp4" and got.cached is True and got.site == "youtube"


def test_downloaded_skips_lone_part_file(settings: Settings, tmp_path: Path):
    d = tmp_path / "item"
    d.mkdir()
    (d / "source.f137.mp4").write_bytes(b"video-only")
    (d / "source.json").write_text("{}")
    assert _downloaded(d) is None


def test_proxy_fps_is_capped(sample_video: Path, settings: Settings, tmp_path: Path):
    proxy = make_proxy(sample_video, tmp_path / "item", settings)
    assert probe(proxy).fps <= settings.proxy_fps


def test_proxy_base64_fits_inline_limit():
    assert Settings(api_key=None).proxy_max_bytes * 4 / 3 < 20 * 1000 * 1000
