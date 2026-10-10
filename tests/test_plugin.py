"""The Hermes plugin wrapper, exercised with a fake plugin context (no Hermes install needed)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("mw_plugin_under_test", PLUGIN_DIR / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mw_plugin_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw


def test_registers_watch_video():
    ctx = FakeCtx()
    _load_plugin().register(ctx)
    t = ctx.tools["watch_video"]
    assert t["toolset"] == "media" and t["requires_env"] == ["OPENROUTER_API_KEY"]
    assert t["schema"]["parameters"]["required"] == ["source"]


def test_handler_reports_errors_as_json(monkeypatch, tmp_path):
    mod = _load_plugin()
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("MEDIA_WATCH_HOME", str(tmp_path))
    out = json.loads(mod._handle({"source": ""}))
    assert "error" in out
    out = json.loads(mod._handle({"source": str(tmp_path / "missing.mp4")}))
    assert "no such file" in out["error"]


def test_bad_config_returns_json_error(monkeypatch, tmp_path):
    mod = _load_plugin()
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("MEDIA_WATCH_HOME", str(tmp_path))
    monkeypatch.setenv("MEDIA_WATCH_MAX_SECONDS", "abc")
    out = json.loads(mod._handle({"source": "https://youtube.com/watch?v=x"}))
    assert "bad media-watch config" in out["error"]


class _FakeResult:
    def to_dict(self):
        return {"report": {}}


def test_note_warns_about_untrusted_report(monkeypatch, tmp_path):
    import mediawatch

    mod = _load_plugin()
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("MEDIA_WATCH_HOME", str(tmp_path))
    monkeypatch.setattr(mediawatch, "watch", lambda *a, **k: _FakeResult())
    out = json.loads(mod._handle({"source": "https://youtube.com/watch?v=x"}))
    assert "untrusted" in out["note"]


def test_unexpected_error_returns_json_error(monkeypatch, tmp_path):
    import mediawatch

    mod = _load_plugin()
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("MEDIA_WATCH_HOME", str(tmp_path))

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(mediawatch, "watch", boom)
    out = json.loads(mod._handle({"source": "https://youtube.com/watch?v=x"}))
    assert "RuntimeError" in out["error"] and "boom" in out["error"]
