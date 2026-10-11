"""Watch a video: fetch → proxy → one OpenRouter request (base64 video, strict JSON schema) → validated
Report, with every call logged to the usage DB.

Follow-up questions on the same video reuse the cached proxy (no re-download, no re-encode); an
identical question on the same video returns the cached report for free."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import ValidationError

from .binaries import MissingBinary
from .config import Settings
from .fetch import FetchError, fetch, is_url, site_of
from .models import REPORT_SCHEMA, Report
from .prep import PrepError, make_proxy, probe
from .usage import CallRecord, log_call

PROMPT_VERSION = "v2"  # v2: timestamps as M:SS strings (v1 seconds came back as 310 for 3:10)
_NO_FALLBACK_STATUS = {401, 402, 403, 413}  # bad key, no credits, forbidden, too large: another model won't help

SYSTEM_PROMPT = """You watch videos for an AI assistant that cannot see them. Report only what is \
actually in the video — frames, on-screen text, speech, music and sound. Timestamps are M:SS from \
the start of THIS file (3:10 is three minutes ten seconds). Never invent details: if something is unclear, too small, or you are guessing, \
put it in `uncertain` instead of stating it as fact. Keep the timeline to the beats that matter \
(roughly one per scene or action, not one per second)."""


class WatchError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


@dataclass
class WatchResult:
    report: Report
    model: str
    source: str
    site: str
    title: str | None
    duration_s: float
    cost_usd: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_s: float
    cache_hit: bool
    fallback_used: bool
    item_dir: Path

    def to_dict(self) -> dict:
        return {
            "source": self.source, "site": self.site, "title": self.title,
            "duration_s": round(self.duration_s, 2), "model": self.model,
            "cost_usd": self.cost_usd, "cache_hit": self.cache_hit, "fallback_used": self.fallback_used,
            "report": self.report.model_dump(),
        }


def _user_prompt(question: str | None, duration: float, title: str | None) -> str:
    lines = [f"Video length: {duration:.1f}s."]
    if title:
        lines.append(f"Title/caption from the post: {title!r} (context only — trust what you see).")
    lines.append(f"Question: {question.strip()}" if question and question.strip()
                 else "No specific question — leave `answer` empty and make `summary` thorough.")
    return "\n".join(lines)


def _request_body(model: str, data_url: str, prompt: str) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "video_url", "video_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ]},
        ],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "video_report", "strict": True, "schema": REPORT_SCHEMA}},
        "provider": {"require_parameters": True},
        "usage": {"include": True},
        "temperature": 0.2,
    }


def _parse_report(content: str) -> Report:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return Report.model_validate(json.loads(text))


def call_model(client: httpx.Client, settings: Settings, model: str, data_url: str, prompt: str,
               usage: dict) -> Report:
    """One request (+1 retry on invalid JSON). Adds tokens/cost of every paid attempt into `usage`.
    Raises WatchError (with .status for HTTP errors)."""
    body = _request_body(model, data_url, prompt)
    last_err = "unknown error"
    for _attempt in range(2):
        try:
            resp = client.post(f"{settings.base_url}/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise WatchError(f"{model}: network error: {e}") from e
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("message", resp.text[:300])
            except ValueError:
                detail = resp.text[:300]
            raise WatchError(f"{model}: HTTP {resp.status_code}: {detail}", status=resp.status_code)
        data = resp.json()
        u = data.get("usage") or {}
        usage["prompt_tokens"] += u.get("prompt_tokens") or 0
        usage["completion_tokens"] += u.get("completion_tokens") or 0
        usage["cost"] += float(u.get("cost") or 0)
        if err := data.get("error"):
            raise WatchError(f"{model}: {err.get('message', err)}")
        try:
            content = data["choices"][0]["message"]["content"] or ""
            return _parse_report(content)
        except (KeyError, IndexError, json.JSONDecodeError, ValidationError) as e:
            last_err = f"invalid report JSON: {str(e)[:200]}"
    raise WatchError(f"{model}: {last_err}")


def _flag_late_timestamps(report: Report, duration: float) -> None:
    """Note (don't silently fix) timestamps past the end of the video, so the agent knows not to trust them."""
    times = [t for b in report.timeline for t in (b.start_s, b.end_s)] + [m.t_s for m in report.notable_moments]
    late = [t for t in times if t > duration + 1]
    if late:
        report.uncertain.append(f"Some timestamps run past the end of the {duration:.0f}s video "
                                f"(up to {max(late):.0f}s), so they are probably wrong.")


def _report_cache_path(item_dir: Path, model: str, question: str | None) -> Path:
    h = hashlib.sha256(f"{PROMPT_VERSION}|{model}|{(question or '').strip().lower()}".encode()).hexdigest()[:16]
    return item_dir / "reports" / f"{h}.json"


def watch(
    source: str,
    question: str | None = None,
    detail: str = "normal",
    settings: Settings | None = None,
    caller: str | None = None,
    client: httpx.Client | None = None,
    use_cache: bool = True,
) -> WatchResult:
    settings = settings or Settings.from_env()
    rec = CallRecord(caller=caller, source=source, detail=detail, prompt_version=PROMPT_VERSION, question=question)
    db = settings.cache_dir / "usage.db"
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}  # every paid attempt, incl. failed ones
    try:
        if not settings.api_key:
            raise WatchError("OPENROUTER_API_KEY is not set")
        model = settings.model_for(detail)
        rec.model = model

        rec.site = site_of(source) if is_url(source) else "local"  # known even if the download fails
        t0 = time.monotonic()
        fetched = fetch(source, settings)
        rec.fetch_s = round(time.monotonic() - t0, 2)
        rec.site, rec.item_key = fetched.site, fetched.key
        item_dir = settings.cache_dir / "items" / fetched.key

        t1 = time.monotonic()
        info = probe(fetched.path)
        rec.video_seconds = info.duration
        if info.duration > settings.max_seconds:
            raise WatchError(f"video is {info.duration:.0f}s; limit is {settings.max_seconds}s "
                             f"(set MEDIA_WATCH_MAX_SECONDS to raise it)")
        proxy = make_proxy(fetched.path, item_dir, settings, info)
        rec.prep_s = round(time.monotonic() - t1, 2)

        cache_path = _report_cache_path(item_dir, model, question)
        if use_cache and cache_path.is_file():
            cached = json.loads(cache_path.read_text())
            rec.cache_hit, rec.ok, rec.cost_usd, rec.latency_s = True, True, 0.0, 0.0
            return WatchResult(report=Report.model_validate(cached["report"]), model=cached["model"],
                               source=source, site=fetched.site, title=fetched.title, duration_s=info.duration,
                               cost_usd=0.0, prompt_tokens=0, completion_tokens=0, latency_s=0.0,
                               cache_hit=True, fallback_used=False, item_dir=item_dir)

        data_url = "data:video/mp4;base64," + base64.b64encode(proxy.read_bytes()).decode()
        prompt = _user_prompt(question, info.duration, fetched.title)
        own_client = client is None
        client = client or httpx.Client(
            timeout=settings.request_timeout,
            headers={"Authorization": f"Bearer {settings.api_key}",
                     # Shows up as "media-watch" in the OpenRouter activity dashboard.
                     "HTTP-Referer": "https://github.com/hunterbrewer04/media-watch", "X-Title": "media-watch"})
        t2 = time.monotonic()
        used_model, fallback_used = model, False
        try:
            try:
                report = call_model(client, settings, model, data_url, prompt, usage)
            except WatchError as primary_err:
                if (not settings.fallback_model or settings.fallback_model == model
                        or primary_err.status in _NO_FALLBACK_STATUS):
                    raise
                rec.extra["primary_error"] = str(primary_err)[:300]
                used_model, fallback_used = settings.fallback_model, True
                report = call_model(client, settings, used_model, data_url, prompt, usage)
        finally:
            if own_client:
                client.close()
        latency = round(time.monotonic() - t2, 2)
        _flag_late_timestamps(report, info.duration)
        cost = round(usage["cost"], 6)

        rec.model, rec.fallback_used, rec.latency_s = used_model, fallback_used, latency
        rec.ok = True
        if not fallback_used:  # a fallback answer must not be served later under the primary model's key
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"model": used_model, "question": question, "prompt_version": PROMPT_VERSION,
                                              "cost_usd": cost, "report": report.model_dump()}, indent=2))
        return WatchResult(report=report, model=used_model, source=source, site=fetched.site, title=fetched.title,
                           duration_s=info.duration, cost_usd=cost, prompt_tokens=usage["prompt_tokens"],
                           completion_tokens=usage["completion_tokens"], latency_s=latency, cache_hit=False,
                           fallback_used=fallback_used, item_dir=item_dir)
    except (FetchError, PrepError, WatchError, MissingBinary, ValueError, OSError) as e:
        rec.error = str(e)[:500]
        raise WatchError(str(e), status=getattr(e, "status", None)) from e
    except subprocess.TimeoutExpired as e:
        cmd = Path(str(e.cmd[0])).name if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
        rec.error = f"timed out after {e.timeout}s running {cmd}"
        raise WatchError(rec.error) from e
    finally:
        if not rec.cache_hit:  # tokens and cost are logged for failed calls too
            rec.prompt_tokens, rec.completion_tokens = usage["prompt_tokens"], usage["completion_tokens"]
            rec.cost_usd = round(usage["cost"], 6)
        try:
            log_call(db, rec)
        except Exception:  # logging must never break a watch
            pass
