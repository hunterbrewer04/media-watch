# media-watch

**Let AI agents actually watch videos.**

Give it a link to an Instagram reel, TikTok, YouTube Short, tweet, or a video file on disk, plus an optional
question. media-watch downloads the video, shrinks it, has a video-native model (Gemini via
[OpenRouter](https://openrouter.ai)) watch it, and hands back a structured report your agent can reason over:
what happens, when, what's written on screen, what's said, and what it sounds like.

It ships two ways:

- **A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin**: adds a `watch_video` tool, so you can
  paste a link into a chat with your agent and ask about it.
- **A standalone CLI (`mw`)**: for scripts, debugging, or any other agent framework (the core library has no Hermes
  dependency).

```text
$ mw watch "https://www.instagram.com/reel/…" -q "what's the recipe?"
[instagram] Crispy chili oil noodles  ·  42.3s  ·  google/gemini-3.8-flash  ·  $0.0061

A creator makes chili oil noodles in one pan, narrating each step over upbeat music…

Answer: Boil noodles, then pour hot oil over garlic, chili flakes, sesame and soy sauce…

Timeline:
    0.0–  4.5s  Finished bowl shown, caption "15 min dinner"
    4.5– 12.0s  Garlic and scallions chopped on a wooden board
  …
```

## How it works

```text
source (URL or file)
  │
  ├─ fetch   yt-dlp downloads it (≤720p); local files are used in place
  ├─ prep    ffmpeg makes a small proxy: 480p, a few fps, mono audio, under ~14 MB
  ├─ watch   one OpenRouter request: video as base64 + your question, strict JSON schema
  │          → validated, retried once on bad JSON, falls back to a second model on provider errors
  ├─ cache   proxies and reports are cached, so follow-up questions are cheap and repeats are free
  └─ log     every call (cost, tokens, latency, errors) goes into a local SQLite usage log
```

The report looks like this:

```json
{
  "summary": "2-4 sentences: what the video is and what happens.",
  "answer": "Direct answer to your question (empty if you didn't ask one).",
  "content_type": "reel",
  "timeline": [{ "start_s": 0, "end_s": 4.5, "description": "…" }],
  "on_screen_text": ["verbatim captions, scoreboards, handles…"],
  "speech": "transcript of what's said or sung",
  "audio": "music, sound effects, crowd, tone",
  "notable_moments": [{ "t_s": 12.1, "label": "…", "why": "…" }],
  "uncertain": ["anything the model couldn't see/hear clearly or is guessing about"]
}
```

## Requirements

- Python 3.11+
- [`ffmpeg`/`ffprobe`](https://ffmpeg.org) and [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) on the machine
  (`node` or `deno` too, if you want yt-dlp to handle YouTube's JS challenges)
- An [OpenRouter API key](https://openrouter.ai/keys)

## Quick start: CLI

```bash
pip install "git+https://github.com/hunterbrewer04/media-watch"   # or: uv tool install git+https://…
export OPENROUTER_API_KEY=sk-or-...

mw watch "https://www.tiktok.com/@user/video/123"                  # general summary
mw watch "https://youtu.be/abc" -q "what song is playing?"         # ask a question
mw watch ~/clips/goal.mp4 --detail high --json                     # better model, raw JSON out
mw stats --days 30                                                 # what you've spent
```

`--detail` picks the model tier: `fast` (cheapest, can hallucinate), `normal` (default), `high` (slower and
pricier, for when accuracy really matters). `--no-cache` forces a fresh model call.

## Quick start: Hermes plugin

```bash
git clone https://github.com/hunterbrewer04/media-watch.git ~/Code/media-watch
ln -s ~/Code/media-watch/plugin ~/.hermes/plugins/media-watch
hermes plugins enable media-watch
```

Put `OPENROUTER_API_KEY` in `~/.hermes/.env` (the plugin uses Hermes' profile-aware secret lookup), then restart
the gateway/dashboard. The `media` toolset is on by default. Hermes' Python already includes `httpx` and
`pydantic`, so there's nothing else to install. Keep the clone where it is: the plugin loads the library from the
repo next to it.

Then just talk to your agent:

> **You:** https://www.instagram.com/reel/… what's the recipe in this?
>
> **Agent:** *(calls `watch_video`)* It's chili oil noodles. Boil the noodles, then…

The tool returns the report plus a note telling the agent that everything in it came from the video and is
untrusted content, so it reports on it instead of following instructions found in it.

## Supported sources

Anything yt-dlp can download should work. These were verified without cookies (October 2026):

| Source | Status |
|---|---|
| YouTube / Shorts | ✅ |
| TikTok | ✅ |
| Instagram reels (public) | ✅ |
| Facebook videos (`/watch/?v=`) | ✅ |
| Facebook **Reels** | ❌ yt-dlp's parser is broken upstream. Send the file instead. |
| Private or login-walled posts | Need a `cookies.txt` (see Configuration) |
| Local files (mp4, mov, webm, mkv, …) | ✅ |

Videos longer than 10 minutes are refused by default (a cost guard against random links). Raise it with
`MEDIA_WATCH_MAX_SECONDS`.

## Configuration

Everything is read from environment variables. Only the API key is required. See [`.env.example`](.env.example).

| Variable | Default | What it does |
|---|---|---|
| `OPENROUTER_API_KEY` | (required) | Your OpenRouter key |
| `MEDIA_WATCH_MODEL_NORMAL` | `google/gemini-3.8-flash` | Default model |
| `MEDIA_WATCH_MODEL_HIGH` | `google/gemini-3.1-pro-preview` | `--detail high` model |
| `MEDIA_WATCH_MODEL_FAST` | `google/gemini-3.1-flash-lite` | `--detail fast` model |
| `MEDIA_WATCH_FALLBACK_MODEL` | `qwen/qwen3.8-omni-flash` | Used once if the main model errors. Empty string disables it. |
| `MEDIA_WATCH_MAX_SECONDS` | `600` | Longest video it will watch |
| `MEDIA_WATCH_HOME` | `~/.hermes/cache/media-watch` | Where downloads, proxies, reports and `usage.db` live |
| `MEDIA_WATCH_COOKIES` | `~/.config/media-watch/cookies.txt` if it exists | Netscape-format cookies for login-walled sites |
| `MEDIA_WATCH_FFMPEG`, `MEDIA_WATCH_FFPROBE`, `MEDIA_WATCH_YT_DLP` | auto-detected | Explicit binary paths (handy under systemd's minimal `PATH`) |

The CLI also falls back to the `OPENROUTER_API_KEY` line in `~/.hermes/.env` if it isn't in your environment.

## Models and cost

Tested on 2026-10-09 with a 5.6-second clip:

| Model | Latency | Cost | Notes |
|---|---|---|---|
| `google/gemini-3.8-flash` | 5s | $0.0025 | Accurate, good timestamps, catches SFX, lyrics and on-screen text. **Default.** |
| `qwen/qwen3.8-omni-flash` | 20s | $0.0007 | Most detailed but slow. **Fallback.** |
| `google/gemini-3.1-flash-lite` | 3s | $0.0005 | Hallucinated an event in testing. `fast` tier only. |

In real use, a typical short video (under about 75 seconds) costs **$0.004–0.011**. The fallback model only kicks in
for errors it can actually fix (outages, rate limits, bad output), not for a bad key or an empty balance.

## Cost tracking

Every call, including failures and cache hits, is written to `usage.db`: timestamp, caller (Hermes profile),
source, site, video length, model, prompt version, question, tokens, **exact cost from OpenRouter** (including
requests that failed), latency, and errors.

```text
$ mw stats
Last 7 days: 11 calls (6 ok, 1 cached, 0 fallback) · $0.0297 · 2.3 min of video · avg 9.1s
  google/gemini-3.8-flash                 6 calls  $0.0297  avg 9.1s
  sites: instagram 3/4, tiktok 2/2, youtube 1/3
```

Requests are tagged `X-Title: media-watch`, so they also show up separately on your OpenRouter activity page.

## Caching

- Downloads and proxies are cached per video. For URLs, tracking parameters like `igsh`, `si` and `utm_*` are
  stripped first, so the same reel shared twice is only downloaded once. Local files are keyed by path, size, modification time and their first and last bytes.
- Reports are cached per video, model, question and prompt version. Asking the same thing twice is free; a new
  question reuses the proxy and only pays for the model call.
- There's no automatic cleanup yet. Delete `MEDIA_WATCH_HOME/items/` whenever you like.

## Security and privacy

- **Where your video goes:** the proxy is sent only to OpenRouter and the model provider it routes to. Nothing is
  uploaded anywhere else.
- **Untrusted content:** on-screen text and speech come from arbitrary videos and could contain prompt injection.
  The Hermes tool labels the report as untrusted. If you wire the library into another agent, do the same.
- **Secrets:** no keys live in the repo. `.gitignore` blocks `.env*`, cookie files, databases and media, and CI runs
  [gitleaks](https://github.com/gitleaks/gitleaks) on every push.
- **Cookies:** a `cookies.txt` is a logged-in session for that site. Keep it `chmod 600` and never commit it.
- Only download and analyze content you have the right to, and respect each site's terms of service.

## Development

```bash
git clone https://github.com/hunterbrewer04/media-watch.git && cd media-watch
uv run --with pytest --with-editable . pytest   # OpenRouter is mocked; the test clip is generated by ffmpeg
uvx ruff check .
```

Layout:

```text
mediawatch/        core library (no Hermes imports)
  config.py        settings from env
  fetch.py         URL → yt-dlp → cache | local path → used in place
  prep.py          ffprobe + small proxy
  watch.py         OpenRouter call, JSON schema, validation, retry + fallback
  usage.py         SQLite usage log + stats
  cli.py           `mw watch`, `mw stats`
plugin/            Hermes plugin: registers `watch_video` (toolset `media`)
tests/             pytest suite
```

## Roadmap

This is phase 1. Next is a gaming-montage editor built on the same core: find highlights from audio peaks, have
the model rank them, write an edit list, then render with ffmpeg, music and sound effects.

## License

[MIT](LICENSE)
