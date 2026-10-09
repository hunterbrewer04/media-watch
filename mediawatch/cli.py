"""`mw` — run media-watch by hand (debugging, evals, stats) without any agent in the loop."""

from __future__ import annotations

import argparse
import json
import sys

from .config import Settings, load_dotenv_key
from .usage import stats
from .watch import WatchError, watch


def _settings() -> Settings:
    s = Settings.from_env()
    if not s.api_key:  # convenience: fall back to Hermes' .env (read one key, export nothing)
        if key := load_dotenv_key("OPENROUTER_API_KEY"):
            from dataclasses import replace
            s = replace(s, api_key=key)
    return s


def _cmd_watch(a: argparse.Namespace) -> int:
    try:
        r = watch(a.source, question=a.question, detail=a.detail, settings=_settings(), caller="cli",
                  use_cache=not a.no_cache)
    except WatchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(r.to_dict(), indent=2))
        return 0
    rep = r.report
    cost = "cached" if r.cache_hit else f"${r.cost_usd:.4f}"
    print(f"[{r.site}] {r.title or r.source}  ·  {r.duration_s:.1f}s  ·  {r.model}  ·  {cost}"
          + ("  ·  fallback" if r.fallback_used else ""))
    print(f"\n{rep.summary}")
    if rep.answer:
        print(f"\nAnswer: {rep.answer}")
    if rep.timeline:
        print("\nTimeline:")
        for b in rep.timeline:
            print(f"  {b.start_s:5.1f}–{b.end_s:5.1f}s  {b.description}")
    if rep.on_screen_text:
        print("\nOn screen: " + " | ".join(rep.on_screen_text))
    if rep.speech:
        print(f"\nSpeech: {rep.speech}")
    if rep.audio:
        print(f"Audio: {rep.audio}")
    if rep.notable_moments:
        print("\nMoments:")
        for m in rep.notable_moments:
            print(f"  {m.t_s:5.1f}s  {m.label} — {m.why}")
    if rep.uncertain:
        print("\nUnsure: " + "; ".join(rep.uncertain))
    return 0


def _cmd_stats(a: argparse.Namespace) -> int:
    s = stats(_settings().cache_dir / "usage.db", days=a.days)
    if a.json:
        print(json.dumps(s, indent=2))
        return 0
    lat = f"{s['avg_latency_s']}s" if s["avg_latency_s"] is not None else "n/a"
    print(f"Last {s['days']:g} days: {s['calls']} calls ({s['ok']} ok, {s['cache_hits']} cached, "
          f"{s['fallbacks']} fallback) · ${s['cost_usd']:.4f} · {s['video_minutes']} min of video · avg {lat}")
    for m in s["by_model"]:
        print(f"  {m['model']:<36} {m['n']:>4} calls  ${m['cost']:.4f}  avg {m['lat'] or 0:.1f}s")
    if s["by_site"]:
        print("  sites: " + ", ".join(f"{r['site']} {r['ok'] or 0}/{r['n']}" for r in s["by_site"]))
    for e in s["recent_errors"]:
        print(f"  ! {e['at']} [{e['site']}] {e['error'][:140]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mw", description="media-watch: have a multimodal model watch a video.")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="watch a URL or local file")
    w.add_argument("source")
    w.add_argument("-q", "--question")
    w.add_argument("-d", "--detail", choices=["fast", "normal", "high"], default="normal")
    w.add_argument("--json", action="store_true")
    w.add_argument("--no-cache", action="store_true", help="re-ask the model even if this was answered before")
    w.set_defaults(fn=_cmd_watch)
    st = sub.add_parser("stats", help="cost / usage summary from the usage log")
    st.add_argument("--days", type=float, default=7)
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=_cmd_stats)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
