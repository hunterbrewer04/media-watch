"""media-watch: let AI agents actually watch videos (reels, TikToks, YouTube, game clips).

Core library — deliberately has no Hermes imports. The Hermes plugin (``plugin/``) and the ``mw`` CLI
are thin wrappers around :func:`watch`."""

from .config import Settings
from .models import Report
from .watch import PROMPT_VERSION, WatchError, WatchResult, watch

__all__ = ["PROMPT_VERSION", "Report", "Settings", "WatchError", "WatchResult", "watch"]
__version__ = "0.1.0"
