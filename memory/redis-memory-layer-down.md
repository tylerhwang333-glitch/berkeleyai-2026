---
name: redis-memory-layer-down
description: The configured cloud Redis is dead, so the coach's memory/recurring-pattern layer silently no-ops
metadata:
  type: project
---

As of 2026-06-21, the cloud Redis endpoint in `.env` (`REDIS_URL=redis://...@head-graphic-spa-61859.db.redis.io:16472`) is non-functional: TCP port accepts connections but the Redis protocol never replies (plaintext AUTH+PING times out; it's not TLS either — `rediss://` gives RECORD_LAYER_FAILURE). The managed instance appears deleted/expired.

**Why:** With `REDIS_REQUIRED=0`, `connect_redis()` returns `None`, so `store_moment`/`search_similar_moments` no-op. Detected moments are never saved and recurring patterns never appear — looks like a bug but is a dead backend.

**How to apply:** To restore the memory layer, point `REDIS_URL` at a working Redis (Redis Stack for the FT.SEARCH vector index; plain Redis still works via brute-force cosine fallback). Verify via `/health` → `redis_connected: true`. Did NOT set `REDIS_REQUIRED=1` because that crash-loops the whole backend into 502s per the `.env` warning. A "memory offline" banner driven by `/health` now surfaces the failure in `frontend/src/AnalyzeApp.tsx`.
