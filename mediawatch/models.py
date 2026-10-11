"""The watch report: a pydantic model for validation plus the strict JSON schema sent to the model.

The model is asked for timestamps as "M:SS" strings, not numbers: asked for seconds, Gemini writes
3:10 as 310 on anything over a minute. They are parsed back into seconds (``start_s`` / ``end_s`` /
``t_s``), which is what the Report exposes and what cached reports store."""

from __future__ import annotations

from typing import Annotated

from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field


def to_seconds(v: object) -> object:
    """'3:10' / '0:04.5' / '1:02:03' / 12.5 -> seconds. Anything else is left for pydantic to reject."""
    if isinstance(v, str) and ":" in v:
        try:
            total = 0.0
            for part in v.strip().split(":"):
                total = total * 60 + float(part)
            return total
        except ValueError:
            return v
    return v


Seconds = Annotated[float, BeforeValidator(to_seconds), Field(ge=0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Beat(_Strict):
    start_s: Seconds = Field(validation_alias=AliasChoices("start_s", "start"))
    end_s: Seconds = Field(validation_alias=AliasChoices("end_s", "end"))
    description: str


class Moment(_Strict):
    t_s: Seconds = Field(validation_alias=AliasChoices("t_s", "at"))
    label: str
    why: str


class Report(_Strict):
    summary: str
    answer: str
    content_type: str
    timeline: list[Beat]
    on_screen_text: list[str]
    speech: str
    audio: str
    notable_moments: list[Moment]
    uncertain: list[str]


def _obj(props: dict, desc: str | None = None) -> dict:
    out = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    if desc:
        out["description"] = desc
    return out


_STR = {"type": "string"}
_TS = {"type": "string", "description": "M:SS from the start of the file, e.g. 0:04.5 or 3:10 (never 310)."}

REPORT_SCHEMA: dict = _obj({
    "summary": {"type": "string", "description": "2-4 sentences: what the video is and what happens."},
    "answer": {"type": "string", "description": "Direct answer to the user's question; empty string if none was asked."},
    "content_type": {"type": "string", "description": "e.g. reel, tutorial, gameplay clip, meme, news, vlog, ad."},
    "timeline": {"type": "array", "items": _obj({"start": _TS, "end": _TS, "description": _STR}),
                 "description": "Chronological beats."},
    "on_screen_text": {"type": "array", "items": _STR,
                       "description": "Verbatim overlays, captions, scoreboards, watermarks, handles."},
    "speech": {"type": "string", "description": "Transcript of spoken words / lyrics where audible; empty if none."},
    "audio": {"type": "string", "description": "Music, sound effects, crowd, commentary tone."},
    "notable_moments": {"type": "array", "items": _obj({"at": _TS, "label": _STR, "why": _STR}),
                        "description": "Highlights worth clipping, with the reason."},
    "uncertain": {"type": "array", "items": _STR,
                  "description": "Anything you could not see/hear clearly or are guessing about."},
})
