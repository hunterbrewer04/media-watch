"""Usage stats: cache hits must not drag down the per-model latency average."""

from __future__ import annotations

from mediawatch.usage import CallRecord, log_call, stats


def test_by_model_latency_excludes_cache_hits(tmp_path):
    db = tmp_path / "usage.db"
    log_call(db, CallRecord(model="m", site="s", ok=True, cache_hit=True, latency_s=0.0))
    log_call(db, CallRecord(model="m", site="s", ok=True, cache_hit=False, latency_s=10.0))
    assert stats(db)["by_model"][0]["lat"] == 10.0
