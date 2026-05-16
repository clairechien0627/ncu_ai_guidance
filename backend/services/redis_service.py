"""Redis connection management with graceful in-memory fallback.

If REDIS_URL is unset or Redis is unreachable, all `get_redis()` calls return
None and job_service falls back to its original in-memory behaviour.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

# ── Key / channel names ───────────────────────────────────────────────────────
BROADCAST_CHANNEL = "job_service:broadcast"    # Pub/Sub: full jobs-list snapshots
EVENTS_CHANNEL    = "job_service:events"       # Pub/Sub: control events (cancel, …)
QUEUE_REINDEX     = "job_service:queue:reindex"
QUEUE_EXTRACT     = "job_service:queue:extract"
QUEUE_PARSE       = "job_service:queue:parse"

# ── Singleton ─────────────────────────────────────────────────────────────────
_client = None   # redis.asyncio.Redis | None


def redis_url() -> str:
    return os.environ.get("REDIS_URL", "").strip()


async def init_redis() -> None:
    """Connect to Redis and verify with PING.  Silently disables on failure."""
    global _client
    url = redis_url()
    if not url:
        logger.info("REDIS_URL not set — job_service running in single-instance mode")
        return
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=5)
        await client.ping()
        _client = client
        safe_url = url.split("@")[-1] if "@" in url else url
        logger.info("Redis connected: %s", safe_url)
    except Exception as exc:
        logger.warning("Redis unavailable (%s) — falling back to in-memory mode", exc)
        _client = None


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


def get_redis():
    """Return the async Redis client, or None when Redis is not configured."""
    return _client


def redis_enabled() -> bool:
    return _client is not None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def redis_publish(channel: str, payload: str) -> None:
    """Fire-and-forget publish; silently ignores errors."""
    r = get_redis()
    if r:
        try:
            await r.publish(channel, payload)
        except Exception as exc:
            logger.debug("redis_publish(%s) failed: %s", channel, exc)


async def redis_rpush(key: str, item: dict) -> None:
    """Append a JSON item to a Redis LIST; silently ignores errors."""
    r = get_redis()
    if r:
        try:
            await r.rpush(key, json.dumps(item, ensure_ascii=False))
        except Exception as exc:
            logger.debug("redis_rpush(%s) failed: %s", key, exc)


async def redis_blpop(key: str, timeout: float = 5) -> dict | None:
    """Blocking pop from a Redis LIST.  Returns parsed dict or None on timeout."""
    r = get_redis()
    if not r:
        return None
    try:
        result = await r.blpop(key, timeout=timeout)
        if result:
            _, data = result
            return json.loads(data)
    except Exception as exc:
        logger.debug("redis_blpop(%s) failed: %s", key, exc)
    return None


async def redis_remove_from_queue(key: str, doc_id: int) -> None:
    """Remove all items with doc_id from a Redis LIST (LRANGE → filter → replace)."""
    r = get_redis()
    if not r:
        return
    try:
        items = await r.lrange(key, 0, -1)
        filtered = [it for it in items if json.loads(it).get("doc_id") != doc_id]
        if len(filtered) == len(items):
            return
        async with r.pipeline() as pipe:
            pipe.delete(key)
            if filtered:
                pipe.rpush(key, *filtered)
            await pipe.execute()
    except Exception as exc:
        logger.debug("redis_remove_from_queue(%s, %d) failed: %s", key, doc_id, exc)


async def redis_reorder_queue(key: str, doc_id: int, new_index: int) -> None:
    """Move doc_id item to new_index within a Redis LIST."""
    r = get_redis()
    if not r:
        return
    try:
        items = await r.lrange(key, 0, -1)
        parsed = [json.loads(it) for it in items]
        job = next((p for p in parsed if p.get("doc_id") == doc_id), None)
        if not job:
            return
        parsed.remove(job)
        idx = max(0, min(new_index, len(parsed)))
        parsed.insert(idx, job)
        async with r.pipeline() as pipe:
            pipe.delete(key)
            if parsed:
                pipe.rpush(key, *[json.dumps(p, ensure_ascii=False) for p in parsed])
            await pipe.execute()
    except Exception as exc:
        logger.debug("redis_reorder_queue(%s, %d) failed: %s", key, doc_id, exc)
