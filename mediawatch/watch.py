"""Watch a video: fetch → proxy → one OpenRouter request (base64 video, strict JSON schema) → validated
Report, with every call logged to the usage DB.

Follow-up questions on the same video reuse the cached proxy (no re-download, no re-encode); an
identical question on the same video returns the cached report for free."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import ValidationError

from .config import Settings
from .fetch import FetchError, fetch, is_url, site_of
from .models import REPORT_SCHEMA, Report
from .prep import PrepError, make_proxy, probe
from .usage import CallRecord, log_call

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You watch videos for an AI assistant that cannot see them. Report only what is \
actually in the video — frames, on-screen text, speech, music and sound. Timestamps are seconds from \
the start of THIS file. Never invent details: if something is unclear, too small, or you are guessing, \
put it in `uncertain` instead of stating it as fact. Keep the timeline to the beats that matter \
(roughly one per scene or action, not one per second)."""


class WatchError(RuntimeError):
    pass


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


def call_model(client: httpx.Client, settings: Settings, model: str, data_url: str, prompt: str) -> tuple[Report, dict]:
    """One request (+1 retry on invalid JSON). Returns (report, usage dict). Raises WatchError."""
    body = _request_body(model, data_url, prompt)
    last_err = "unknown error"
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
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
            raise WatchError(f"{model}: HTTP {resp.status_code}: {detail}")
        data = resp.json()
        u = data.get("usage") or {}
        usage_total["prompt_tokens"] += u.get("prompt_tokens") or 0
        usage_total["completion_tokens"] += u.get("completion_tokens") or 0
        usage_total["cost"] += float(u.get("cost") or 0)
        if err := data.get("error"):
            raise WatchError(f"{model}: {err.get('message', err)}")
        try:
            content = data["choices"][0]["message"]["content"] or ""
            return _parse_report(content), usage_total
        except (KeyError, IndexError, json.JSONDecodeError, ValidationError) as e:
            last_err = f"invalid report JSON: {str(e)[:200]}"
    raise WatchError(f"{model}: {last_err}")


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
                report, usage = call_model(client, settings, model, data_url, prompt)
            except WatchError as primary_err:
                if not settings.fallback_model or settings.fallback_model == model:
                    raise
                rec.extra["primary_error"] = str(primary_err)[:300]
                used_model, fallback_used = settings.fallback_model, True
                report, usage = call_model(client, settings, used_model, data_url, prompt)
        finally:
            if own_client:
                client.close()
        latency = round(time.monotonic() - t2, 2)

        rec.model, rec.fallback_used, rec.latency_s = used_model, fallback_used, latency
        rec.prompt_tokens, rec.completion_tokens = usage["prompt_tokens"], usage["completion_tokens"]
        rec.cost_usd = round(usage["cost"], 6)
        rec.ok = True
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"model": used_model, "question": question, "prompt_version": PROMPT_VERSION,
                                          "cost_usd": rec.cost_usd, "report": report.model_dump()}, indent=2))
        return WatchResult(report=report, model=used_model, source=source, site=fetched.site, title=fetched.title,
                           duration_s=info.duration, cost_usd=rec.cost_usd, prompt_tokens=rec.prompt_tokens,
                           completion_tokens=rec.completion_tokens, latency_s=latency, cache_hit=False,
                           fallback_used=fallback_used, item_dir=item_dir)
    except (FetchError, PrepError, WatchError, ValueError) as e:
        rec.error = str(e)[:500]
        raise WatchError(str(e)) from e
    finally:
        try:
            log_call(db, rec)
        except Exception:  # logging must never break a watch
            pass
