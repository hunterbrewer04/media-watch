from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from mediawatch.config import Settings, load_dotenv_key
from mediawatch.fetch import FetchError, canonical_url, fetch, site_of
from mediawatch.models import REPORT_SCHEMA, Report
from mediawatch.prep import make_proxy, probe
from mediawatch.usage import stats
from mediawatch.watch import WatchError, watch

GOOD_REPORT = {
    "summary": "A test pattern.", "answer": "", "content_type": "test", "timeline": [
        {"start_s": 0, "end_s": 4, "description": "colour bars"}],
    "on_screen_text": ["00:00"], "speech": "", "audio": "440 Hz tone",
    "notable_moments": [{"t_s": 1.0, "label": "start", "why": "beginning"}], "uncertain": [],
}


def _ok(content: str, cost: float = 0.001) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 200, "cost": cost}})


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- config -------------------------------------------------------------------------------------

def test_settings_from_env_reads_overrides(tmp_path: Path):
    env = {"OPENROUTER_API_KEY": " k ", "MEDIA_WATCH_MODEL_NORMAL": "x/y", "MEDIA_WATCH_HOME": str(tmp_path),
           "MEDIA_WATCH_FALLBACK_MODEL": "", "MEDIA_WATCH_MAX_SECONDS": "60"}
    s = Settings.from_env(getenv=env.get)
    assert s.api_key == "k" and s.models["normal"] == "x/y" and s.cache_dir == tmp_path
    assert s.fallback_model is None and s.max_seconds == 60
    with pytest.raises(ValueError):
        s.model_for("ultra")


def test_load_dotenv_key_reads_one_key(tmp_path: Path):
    f = tmp_path / ".env"
    f.write_text("OTHER=1\nexport OPENROUTER_API_KEY='abc'\n")
    assert load_dotenv_key("OPENROUTER_API_KEY", f) == "abc"
    assert load_dotenv_key("MISSING", f) is None


# --- schema -------------------------------------------------------------------------------------

def test_schema_is_strict_and_matches_model():
    assert REPORT_SCHEMA["additionalProperties"] is False
    assert set(REPORT_SCHEMA["required"]) == set(Report.model_fields)
    Report.model_validate(GOOD_REPORT)


# --- fetch --------------------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.instagram.com/reel/ABC123/?igsh=xyz&utm_source=ig", "https://instagram.com/reel/ABC123"),
    ("https://www.tiktok.com/@u/video/1?is_from_webapp=1&sender_device=pc", "https://tiktok.com/@u/video/1"),
    ("https://youtube.com/watch?v=abc&si=track", "https://youtube.com/watch?v=abc"),
])
def test_canonical_url_drops_tracking(url, expected):
    assert canonical_url(url) == expected


@pytest.mark.parametrize("url,site", [
    ("https://youtu.be/x", "youtube"), ("https://www.instagram.com/reel/x", "instagram"),
    ("https://fb.watch/x", "facebook"), ("https://x.com/a/status/1", "x"), ("https://example.org/v.mp4", "example.org"),
])
def test_site_of(url, site):
    assert site_of(url) == site


def test_fetch_local_rejects_missing_and_non_video(tmp_path: Path, settings: Settings):
    with pytest.raises(FetchError):
        fetch(str(tmp_path / "nope.mp4"), settings)
    txt = tmp_path / "a.txt"
    txt.write_text("hi")
    with pytest.raises(FetchError):
        fetch(str(txt), settings)


# --- prep ---------------------------------------------------------------------------------------

def test_probe_and_proxy(sample_video: Path, settings: Settings, tmp_path: Path):
    info = probe(sample_video)
    assert info.width == 1280 and info.height == 720 and info.has_audio and 3.5 < info.duration < 4.5
    proxy = make_proxy(sample_video, tmp_path / "item", settings, info)
    p = probe(proxy)
    assert p.height == 480 and p.has_audio and p.size_bytes < info.size_bytes
    assert make_proxy(sample_video, tmp_path / "item", settings) == proxy  # reused


# --- watch (mocked OpenRouter) ------------------------------------------------------------------

def test_watch_happy_path_logs_cost_and_caches(sample_video: Path, settings: Settings):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        assert body["messages"][1]["content"][0]["video_url"]["url"].startswith("data:video/mp4;base64,")
        assert body["response_format"]["json_schema"]["strict"] is True
        return _ok(json.dumps(GOOD_REPORT))

    r = watch(str(sample_video), question="what is it?", settings=settings, caller="test", client=_client(handler))
    assert r.report.summary == "A test pattern." and r.cost_usd == 0.001 and not r.cache_hit
    assert seen[0]["model"] == settings.models["normal"]

    again = watch(str(sample_video), question="What is it?  ", settings=settings, client=_client(handler))
    assert again.cache_hit and again.cost_usd == 0.0 and len(seen) == 1

    s = stats(settings.cache_dir / "usage.db")
    assert s["calls"] == 2 and s["cache_hits"] == 1 and s["cost_usd"] == 0.001


def test_watch_retries_bad_json_then_succeeds(sample_video: Path, settings: Settings):
    replies = iter([_ok("not json"), _ok("```json\n" + json.dumps(GOOD_REPORT) + "\n```")])
    r = watch(str(sample_video), settings=settings, client=_client(lambda req: next(replies)), use_cache=False)
    assert r.report.audio == "440 Hz tone" and r.cost_usd == 0.002  # both attempts billed


def test_watch_falls_back_on_provider_error(sample_video: Path, settings: Settings):
    def handler(req: httpx.Request) -> httpx.Response:
        if json.loads(req.content)["model"] == settings.models["normal"]:
            return httpx.Response(502, json={"error": {"message": "upstream down"}})
        return _ok(json.dumps(GOOD_REPORT))

    r = watch(str(sample_video), settings=settings, client=_client(handler), use_cache=False)
    assert r.fallback_used and r.model == "fallback/model"


def test_watch_errors_are_logged(sample_video: Path, settings: Settings):
    from dataclasses import replace
    s = replace(settings, fallback_model=None)
    with pytest.raises(WatchError, match="HTTP 401"):
        watch(str(sample_video), settings=s, use_cache=False,
              client=_client(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}})))
    st = stats(s.cache_dir / "usage.db")
    assert st["calls"] == 1 and st["ok"] == 0 and "401" in st["recent_errors"][0]["error"]


def test_watch_requires_key_and_enforces_length(sample_video: Path, settings: Settings):
    from dataclasses import replace
    with pytest.raises(WatchError, match="OPENROUTER_API_KEY"):
        watch(str(sample_video), settings=replace(settings, api_key=None))
    with pytest.raises(WatchError, match="limit is 2s"):
        watch(str(sample_video), settings=replace(settings, max_seconds=2))
