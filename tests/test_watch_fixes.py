from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from mediawatch.binaries import MissingBinary
from mediawatch.config import Settings
from mediawatch.usage import stats
from mediawatch.watch import WatchError, watch

watch_module = importlib.import_module("mediawatch.watch")  # the package re-exports the watch() function

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


def _model(req: httpx.Request) -> str:
    return json.loads(req.content)["model"]


def test_failed_calls_are_still_billed(sample_video: Path, settings: Settings):
    # primary: 2 non-JSON attempts, fallback: 2 non-JSON attempts -> 4 paid requests at 0.01 each
    with pytest.raises(WatchError):
        watch(str(sample_video), settings=settings, use_cache=False,
              client=_client(lambda req: _ok("not json", cost=0.01)))
    s = stats(settings.cache_dir / "usage.db")
    assert s["cost_usd"] == pytest.approx(0.04) and s["ok"] == 0


def test_fallback_cost_includes_failed_primary(sample_video: Path, settings: Settings):
    def handler(req: httpx.Request) -> httpx.Response:
        if _model(req) == settings.models["normal"]:
            return _ok("not json", cost=0.01)
        return _ok(json.dumps(GOOD_REPORT), cost=0.001)

    r = watch(str(sample_video), settings=settings, use_cache=False, client=_client(handler))
    assert r.fallback_used and r.cost_usd == pytest.approx(0.021)


def test_fallback_report_is_not_cached_under_primary(sample_video: Path, settings: Settings):
    def outage(req: httpx.Request) -> httpx.Response:
        if _model(req) == settings.models["normal"]:
            return httpx.Response(502, json={"error": {"message": "upstream down"}})
        return _ok(json.dumps(GOOD_REPORT))

    first = watch(str(sample_video), question="what?", settings=settings, client=_client(outage))
    assert first.fallback_used and not first.cache_hit

    seen = []

    def recovered(req: httpx.Request) -> httpx.Response:
        seen.append(_model(req))
        return _ok(json.dumps(GOOD_REPORT))

    second = watch(str(sample_video), question="what?", settings=settings, client=_client(recovered))
    assert not second.cache_hit and not second.fallback_used
    assert second.model == settings.models["normal"] and seen == [settings.models["normal"]]


def test_no_fallback_on_401_and_status_is_kept(sample_video: Path, settings: Settings):
    count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(WatchError) as exc:
        watch(str(sample_video), settings=settings, use_cache=False, client=_client(handler))
    assert exc.value.status == 401 and count == 1


def test_missing_binary_becomes_watch_error(sample_video: Path, settings: Settings, monkeypatch):
    def boom(path):
        raise MissingBinary("ffprobe not found")

    monkeypatch.setattr(watch_module, "probe", boom)
    with pytest.raises(WatchError) as exc:
        watch(str(sample_video), settings=settings, use_cache=False)
    assert not isinstance(exc.value, MissingBinary)
    assert "ffprobe" in stats(settings.cache_dir / "usage.db")["recent_errors"][0]["error"]


def test_timeout_becomes_readable_watch_error(settings: Settings, monkeypatch):
    def boom(source, settings):
        raise subprocess.TimeoutExpired(cmd=["/usr/bin/yt-dlp", "x"], timeout=300)

    monkeypatch.setattr(watch_module, "fetch", boom)
    with pytest.raises(WatchError) as exc:
        watch("https://youtube.com/watch?v=abc", settings=settings, use_cache=False)
    assert "timed out" in str(exc.value) and "yt-dlp" in str(exc.value)
