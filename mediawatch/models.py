"""The watch report: a pydantic model for validation plus the strict JSON schema sent to the model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Beat(_Strict):
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    description: str


class Moment(_Strict):
    t_s: float = Field(ge=0)
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
_NUM = {"type": "number"}

REPORT_SCHEMA: dict = _obj({
    "summary": {"type": "string", "description": "2-4 sentences: what the video is and what happens."},
    "answer": {"type": "string", "description": "Direct answer to the user's question; empty string if none was asked."},
    "content_type": {"type": "string", "description": "e.g. reel, tutorial, gameplay clip, meme, news, vlog, ad."},
    "timeline": {"type": "array", "items": _obj({"start_s": _NUM, "end_s": _NUM, "description": _STR}),
                 "description": "Chronological beats, seconds from the start."},
    "on_screen_text": {"type": "array", "items": _STR,
                       "description": "Verbatim overlays, captions, scoreboards, watermarks, handles."},
    "speech": {"type": "string", "description": "Transcript of spoken words / lyrics where audible; empty if none."},
    "audio": {"type": "string", "description": "Music, sound effects, crowd, commentary tone."},
    "notable_moments": {"type": "array", "items": _obj({"t_s": _NUM, "label": _STR, "why": _STR}),
                        "description": "Highlights worth clipping, with the reason."},
    "uncertain": {"type": "array", "items": _STR,
                  "description": "Anything you could not see/hear clearly or are guessing about."},
})
