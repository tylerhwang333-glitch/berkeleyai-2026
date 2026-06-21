"""Redis as the core memory + retrieval layer.

Redis is NOT just a cache here. It is the persistent memory of the coach:

  * Every detected DecisionMoment is stored as a hash (moment:{id}).
  * Each player accumulates a memory list (player:{id}:moments) and report
    history (player:{id}:reports).
  * Similar past mistakes are retrieved via vector similarity over a simple
    deterministic local embedding (no external embedding API needed).

We try to build a RediSearch vector index (Redis Stack). If FT.CREATE /
FT.SEARCH is unavailable we fall back to brute-force cosine similarity over the
stored moments. Index setup never crashes the app.
"""
from __future__ import annotations

import json
import math
import os
import struct
import time
from typing import List, Optional

import redis

from .models import CoachReport, DecisionMoment, SimilarMemoryItem

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
# When truthy, the app refuses to start if Redis is unreachable instead of
# silently degrading the memory layer to a no-op.
REDIS_REQUIRED = os.environ.get("REDIS_REQUIRED", "").lower() in ("1", "true", "yes")
EMBED_DIM = 64
INDEX_NAME = "moments_idx"
MOMENT_PREFIX = "moment:"

_vector_index_ready = False


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def connect_redis(retries: int = 5, backoff: float = 0.5) -> Optional["redis.Redis"]:
    """Return a connected Redis client, retrying briefly first.

    Real demos hit Redis on every analysis (it is the persistent memory layer),
    so a flaky/slow-to-boot Redis shouldn't silently disable memory on the first
    request. We retry with linear backoff. If REDIS_REQUIRED is set we raise;
    otherwise we return None and the pipeline runs without memory.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            client = redis.Redis.from_url(
                REDIS_URL,
                decode_responses=False,
                socket_connect_timeout=3,
                # Read timeout too: this endpoint can accept the TCP connection
                # but then hang on the AUTH/PING reply. Without this the ping()
                # blocks for ~6s/attempt and startup drags for 30s+.
                socket_timeout=3,
            )
            client.ping()
            if attempt > 1:
                print(f"[redis_store] Connected to Redis on attempt {attempt}.")
            return client
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * attempt)

    msg = f"Redis unavailable after {retries} attempts at {REDIS_URL}: {last_exc}"
    if REDIS_REQUIRED:
        raise RuntimeError(msg)
    print(f"[redis_store] {msg} — memory features disabled for now.")
    return None


# ---------------------------------------------------------------------------
# Deterministic local embedding (64-dim hashed bag-of-words)
# ---------------------------------------------------------------------------
def embed_text(text: str) -> List[float]:
    """Hash each lowercase token into one of EMBED_DIM buckets, then L2-normalize."""
    vec = [0.0] * EMBED_DIM
    for token in text.lower().split():
        token = token.strip(".,:;!?()[]\"'")
        if not token:
            continue
        bucket = hash_token(token) % EMBED_DIM
        vec[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def hash_token(token: str) -> int:
    """Stable hash independent of PYTHONHASHSEED."""
    h = 2166136261
    for ch in token:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _to_float32_bytes(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _cosine(a: List[float], b: List[float]) -> float:
    # vectors are already normalized, so dot product == cosine similarity
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Index setup (graceful fallback)
# ---------------------------------------------------------------------------
def ensure_indexes(client: Optional["redis.Redis"]) -> bool:
    """Create the RediSearch vector index if possible. Never raises."""
    global _vector_index_ready
    if client is None:
        return False
    try:
        from redis.commands.search.field import TagField, TextField, VectorField

        try:
            # redis-py >= 5/8 uses snake_case module name.
            from redis.commands.search.index_definition import IndexDefinition, IndexType
        except ImportError:  # older redis-py
            from redis.commands.search.indexDefinition import IndexDefinition, IndexType

        try:
            client.ft(INDEX_NAME).info()
            _vector_index_ready = True
            return True  # already exists
        except Exception:
            pass

        schema = (
            TagField("player_id"),
            TagField("mistake_type"),
            TagField("map"),
            TextField("summary_text"),
            VectorField(
                "embedding",
                "FLAT",
                {"TYPE": "FLOAT32", "DIM": EMBED_DIM, "DISTANCE_METRIC": "COSINE"},
            ),
        )
        definition = IndexDefinition(prefix=[MOMENT_PREFIX], index_type=IndexType.HASH)
        client.ft(INDEX_NAME).create_index(fields=schema, definition=definition)
        _vector_index_ready = True
        print("[redis_store] RediSearch vector index created.")
        return True
    except Exception as exc:  # noqa: BLE001
        _vector_index_ready = False
        print(f"[redis_store] Vector index unavailable, using fallback search: {exc}")
        return False


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def store_moment(client: Optional["redis.Redis"], moment: DecisionMoment) -> None:
    if client is None:
        return
    try:
        vec = embed_text(moment.summary_text)
        key = f"{MOMENT_PREFIX}{moment.moment_id}"
        mapping = {
            "moment_id": moment.moment_id,
            "player_id": moment.player_id,
            "mistake_type": moment.mistake_type,
            "map": moment.map,
            "summary_text": moment.summary_text,
            "json": json.dumps(moment.model_dump()),
            "embedding": _to_float32_bytes(vec),
        }
        client.hset(key, mapping=mapping)
        client.sadd(f"player:{moment.player_id}:moments", moment.moment_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[redis_store] store_moment failed: {exc}")


def store_report(client: Optional["redis.Redis"], report: CoachReport) -> None:
    if client is None:
        return
    try:
        client.set(f"report:{report.report_id}", json.dumps(report.model_dump()))
        client.lpush(f"player:{report.player_id}:reports", report.report_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[redis_store] store_report failed: {exc}")


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def _load_moment_json(client: "redis.Redis", moment_id: str) -> Optional[dict]:
    raw = client.hget(f"{MOMENT_PREFIX}{moment_id}", "json")
    if not raw:
        return None
    return json.loads(raw)


def get_player_memory(client: Optional["redis.Redis"], player_id: str) -> List[DecisionMoment]:
    """Return all stored decision moments for a player."""
    if client is None:
        return []
    try:
        ids = client.smembers(f"player:{player_id}:moments")
        out: List[DecisionMoment] = []
        for mid in ids:
            mid_s = mid.decode() if isinstance(mid, bytes) else mid
            data = _load_moment_json(client, mid_s)
            if data:
                out.append(DecisionMoment(**data))
        out.sort(key=lambda m: m.round_id)
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[redis_store] get_player_memory failed: {exc}")
        return []


def search_similar_moments(
    client: Optional["redis.Redis"],
    player_id: str,
    summary_text: str,
    top_k: int = 5,
    exclude_ids: Optional[List[str]] = None,
) -> List[SimilarMemoryItem]:
    """Find prior moments similar to `summary_text` for this player.

    Uses RediSearch KNN when available, otherwise brute-force cosine over the
    player's stored moments.
    """
    if client is None:
        return []
    exclude = set(exclude_ids or [])
    query_vec = embed_text(summary_text)

    # --- Try RediSearch KNN first ---
    if _vector_index_ready:
        try:
            from redis.commands.search.query import Query

            q = (
                Query(f"(@player_id:{{{player_id}}})=>[KNN {top_k + len(exclude)} @embedding $vec AS score]")
                .sort_by("score")
                .return_fields("moment_id", "mistake_type", "summary_text", "map", "score")
                .dialect(2)
            )
            res = client.ft(INDEX_NAME).search(q, query_params={"vec": _to_float32_bytes(query_vec)})
            items: List[SimilarMemoryItem] = []
            for doc in res.docs:
                mid = doc.moment_id.decode() if isinstance(doc.moment_id, bytes) else doc.moment_id
                if mid in exclude:
                    continue
                # COSINE distance -> similarity
                dist = float(doc.score) if hasattr(doc, "score") else 1.0
                items.append(
                    SimilarMemoryItem(
                        moment_id=mid,
                        mistake_type=_d(doc.mistake_type),
                        summary_text=_d(doc.summary_text),
                        similarity=round(1.0 - dist, 3),
                        map=_d(doc.map),
                    )
                )
                if len(items) >= top_k:
                    break
            if items:
                return items
        except Exception as exc:  # noqa: BLE001
            print(f"[redis_store] KNN search failed, falling back: {exc}")

    # --- Fallback: brute force over the player's stored moments ---
    try:
        ids = client.smembers(f"player:{player_id}:moments")
        scored: List[SimilarMemoryItem] = []
        for mid in ids:
            mid_s = mid.decode() if isinstance(mid, bytes) else mid
            if mid_s in exclude:
                continue
            data = _load_moment_json(client, mid_s)
            if not data:
                continue
            sim = _cosine(query_vec, embed_text(data["summary_text"]))
            scored.append(
                SimilarMemoryItem(
                    moment_id=mid_s,
                    mistake_type=data["mistake_type"],
                    summary_text=data["summary_text"],
                    similarity=round(sim, 3),
                    map=data["map"],
                )
            )
        scored.sort(key=lambda s: s.similarity, reverse=True)
        return scored[:top_k]
    except Exception as exc:  # noqa: BLE001
        print(f"[redis_store] fallback search failed: {exc}")
        return []


def _d(value) -> str:
    return value.decode() if isinstance(value, bytes) else (value or "")
