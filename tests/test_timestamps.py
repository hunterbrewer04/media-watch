"""Timestamps come back from the model as "M:SS" strings and are exposed as seconds.

Regression: asked for numeric seconds, Gemini wrote 3:10 as 310 on a 262s film (timeline ran to 418)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mediawatch.config import Settings
from mediawatch.models import REPORT_SCHEMA, Report, to_seconds
from mediawatch.watch import watch

WIRE_REPORT = {  # the shape the model returns under REPORT_SCHEMA
    "summary": "A test pattern.", "answer": "", "content_type": "test",
    "timeline": [{"start": "0:00", "end": "0:02.5", "description": "colour bars"},
                 {"start": "0:02.5", "end": "0:04", "description": "tone"}],
    "on_screen_text": [], "speech": "", "audio": "440 Hz tone",
    "notable_moments": [{"at": "0:01", "label": "start", "why": "beginning"}], "uncertain": [],
}


def _client(report: dict) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(report)}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.001}})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("raw, seconds", [
    ("3:10", 190.0), ("0:04.5", 4.5), ("4:18", 258.0), ("1:02:03", 3723.0), (" 0:59 ", 59.0), (12.5, 12.5),
])
def test_to_seconds(raw, seconds):
    assert to_seconds(raw) == seconds


def test_schema_asks_for_mss_strings():
    beat = REPORT_SCHEMA["properties"]["timeline"]["items"]["properties"]
    moment = REPORT_SCHEMA["properties"]["notable_moments"]["items"]["properties"]
    assert beat["start"]["type"] == beat["end"]["type"] == moment["at"]["type"] == "string"


def test_wire_report_parses_to_seconds_and_round_trips():
    r = Report.model_validate(WIRE_REPORT)
    assert [(b.start_s, b.end_s) for b in r.timeline] == [(0.0, 2.5), (2.5, 4.0)]
    assert r.notable_moments[0].t_s == 1.0
    dumped = r.model_dump()  # what the cache stores
    assert dumped["timeline"][0] == {"start_s": 0.0, "end_s": 2.5, "description": "colour bars"}
    assert Report.model_validate(dumped) == r


def test_bad_timestamp_is_rejected():
    bad = {**WIRE_REPORT, "notable_moments": [{"at": "soon", "label": "x", "why": "y"}]}
    with pytest.raises(ValidationError):
        Report.model_validate(bad)


def test_timestamps_past_the_end_are_flagged(sample_video: Path, settings: Settings):
    late = {**WIRE_REPORT, "timeline": [{"start": "0:00", "end": "4:18", "description": "whole thing"}]}
    r = watch(str(sample_video), settings=settings, client=_client(late), use_cache=False)
    assert r.report.timeline[0].end_s == 258.0
    assert any("past the end" in u for u in r.report.uncertain)


def test_in_range_timestamps_are_not_flagged(sample_video: Path, settings: Settings):
    r = watch(str(sample_video), settings=settings, client=_client(WIRE_REPORT), use_cache=False)
    assert r.report.uncertain == []
