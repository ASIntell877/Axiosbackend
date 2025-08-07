import os
import json
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import inspect
import asyncio
from app.client_config import CLIENT_CONFIG

try:
    import redis.asyncio as redis
except Exception:
    import redis as redis

load_dotenv()

redis_url = os.getenv("REDIS_URL")
r = redis.from_url(redis_url, decode_responses=True)

async def get_last_seen(client_id: str, chat_id: str) -> datetime | None:
    raw = await r.get(f"ls:{client_id}:{chat_id}")
    if raw:
        return datetime.fromisoformat(raw)
    return None

DEFAULT_SESSION_TIMEOUT = timedelta(minutes=30)

async def get_session_timeout(client_id: str) -> timedelta:
    """Return the session timeout for a client as a timedelta."""
    cfg = await get_client_config(client_id)
    minutes = None
    if cfg:
        minutes = cfg.get("session_timeout_minutes")
    if minutes is None:
        return DEFAULT_SESSION_TIMEOUT
    try:
        return timedelta(minutes=int(minutes))
    except (TypeError, ValueError):
        return DEFAULT_SESSION_TIMEOUT

async def set_last_seen(client_id: str, chat_id: str, when: datetime):
    ttl_seconds = int((await get_session_timeout(client_id)).total_seconds())
    await r.setex(f"ls:{client_id}:{chat_id}", ttl_seconds, when.isoformat())

async def get_persona(client_id):
    raw = await r.get(f"persona:{client_id}")
    if raw is None:
        return None
    try:
        return json.loads(raw)  # Return dict with prompt/index/style
    except json.JSONDecodeError:
        return {"prompt": raw}  # Fallback for legacy prompt-only string

async def save_chat_message(client_id, session_id, role, content):
    key = f"chat:{client_id}:{session_id}"
    entry = json.dumps({"role": role, "content": content})
    await r.rpush(key, entry)


async def get_chat_history(client_id, session_id):
    key = f"chat:{client_id}:{session_id}"
    raw_history = await r.lrange(key, 0, -1)
    return [json.loads(entry) for entry in raw_history]

async def append_to_persona(client_id, additional_text):
    key = f"persona:{client_id}"
    existing = await r.get(key)
    if existing is None:
        updated_prompt = additional_text.strip()
        await r.set(key, json.dumps({"prompt": updated_prompt}))
    else:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, dict) and "prompt" in parsed:
                parsed["prompt"] = parsed["prompt"].strip() + "\n\n" + additional_text.strip()
                await r.set(key, json.dumps(parsed))
            else:
                # fallback in case existing isn't a proper dict
                updated = existing.strip() + "\n\n" + additional_text.strip()
                await r.set(key, json.dumps({"prompt": updated}))
        except json.JSONDecodeError:
            updated = existing.strip() + "\n\n" + additional_text.strip()
            await r.set(key, json.dumps({"prompt": updated}))

async def increment_token_usage(api_key: str, token_count: int, model: str = "unknown"):
    """
    Tracks token usage for a given API key in Redis.
    Includes daily, monthly, and model-specific usage.
    """
    today = time.strftime("%Y-%m-%d")
    month = time.strftime("%Y-%m")
    print(f"Incrementing token usage for {api_key}: {token_count} tokens")
    

    # 🔹 Keys we'll update
    total_key = f"token_usage:{api_key}:total"
    daily_key = f"token_usage:{api_key}:daily:{today}"
    monthly_key = f"token_usage:{api_key}:monthly:{month}"
    model_key = f"token_usage:{api_key}:model:{model}"
    print(f"Updating Redis keys: {total_key}, {daily_key}, {monthly_key}, {model_key}")

    # 🔹 Increment all counters
    await r.incrby(total_key, token_count)
    await r.incrby(daily_key, token_count)
    await r.incrby(monthly_key, token_count)
    await r.incrby(model_key, token_count)

    # 🔹 Optionally: set expiry for daily + monthly keys
    await r.expire(daily_key, 60 * 60 * 24 * 31)
    await r.expire(monthly_key, 60 * 60 * 24 * 365)

async def get_token_usage(api_key: str):
    today = time.strftime("%Y-%m-%d")
    month = time.strftime("%Y-%m")

    total_key = f"token_usage:{api_key}:total"
    daily_key = f"token_usage:{api_key}:daily:{today}"
    monthly_key = f"token_usage:{api_key}:monthly:{month}"

    # Fetch totals safely (default to 0 if none)
    total = int(await r.get(total_key) or 0)
    daily = int(await r.get(daily_key) or 0)
    monthly = int(await r.get(monthly_key) or 0)
    print(f"Fetching token usage for {api_key}: {total_key}, {daily_key}, {monthly_key}")

    # Fetch per-model usage keys dynamically
    model_prefix = f"token_usage:{api_key}:model:"
    model_tokens = {}
    async for key in r.scan_iter(match=f"{model_prefix}*"):
        model_name = key[len(model_prefix):]
        count = int(await r.get(key) or 0)
        model_tokens[model_name] = count

    return {
        "total_tokens": total,
        "daily_tokens": daily,
        "monthly_tokens": monthly,
        "per_model_tokens": model_tokens,
    }

async def set_persona(client_id: str, prompt: str):
    """
    Overwrites the entire persona prompt in Redis for the given client_id.
    The prompt is stored as JSON under the key persona:{client_id}.
    """
    key = f"persona:{client_id}"
    await r.set(key, json.dumps({"prompt": prompt.strip()}))

async def set_client_config(client_id: str, config: dict):
    key = f"client_config:{client_id}"
    await r.set(key, json.dumps(config))


async def get_client_config(client_id: str) -> dict | None:
    """Fetch client config from Redis, falling back to CLIENT_CONFIG."""
    key = f"client_config:{client_id}"
    raw = r.get(key)
    raw = await raw if inspect.isawaitable(raw) else raw
    if raw is None:
        print(f"[DEBUG] Using fallback config for {client_id}")
        return CLIENT_CONFIG.get(client_id)
    try:
        cfg = json.loads(raw)
        print(f"[DEBUG] Loaded {client_id} config from Redis")
        return cfg
    except json.JSONDecodeError:
        return CLIENT_CONFIG.get(client_id)


async def get_all_client_configs() -> dict:
    """Return combined configs from Redis and fallback file."""
    configs = CLIENT_CONFIG.copy()
    iterator = r.scan_iter(match="client_config:*")
    if hasattr(iterator, "__aiter__"):
        async for key in iterator:
            cid = key.split(":", 1)[1]
            val = r.get(key)
            val = await val if inspect.isawaitable(val) else val
            if not val:
                continue
            try:
                configs[cid] = json.loads(val)
                print(f"[DEBUG] Loaded {cid} config from Redis")
            except json.JSONDecodeError:
                pass
    else:
        for key in iterator:
            cid = key.split(":", 1)[1]
            val = r.get(key)
            if inspect.isawaitable(val):
                val = asyncio.run(val)
            if not val:
                continue
            try:
                configs[cid] = json.loads(val)
            except json.JSONDecodeError:
                pass
    return configs


async def record_feedback_vote(
    client_id: str,
    message_id: str,
    user_id: str,
    vote: str,
) -> bool:
    """Record a feedback vote for a specific user and message.

    Uses a Redis hash ``feedback:{client_id}:{message_id}`` where each field is a
    ``user_id``. ``HSETNX`` ensures that a user may only vote once per message.

    Returns ``True`` if the vote was recorded, ``False`` if the user has already
    voted on this message.
    """

    key = f"feedback:{client_id}:{message_id}"
    # hsetnx returns 1 if field is a new field in the hash and value was set.
    return bool(await r.hsetnx(key, user_id, vote))


async def append_feedback_event(
    client_id: str,
    message_id: str,
    user_id: str,
    vote: str,
    stream_name: str = "feedback_stream",
):
    """Append a feedback event to a Redis stream for analytics.

    ``xadd`` writes a new event to ``stream_name`` capturing metadata about the
    vote so that downstream consumers can process it later.
    """

    event = {
        "client": client_id,
        "message": message_id,
        "user": user_id,
        "vote": vote,
        "timestamp": datetime.utcnow().isoformat(),
    }
    await r.xadd(stream_name, event)

    

async def store_vote(
    client_id: str,
    chat_id: str,
    message_id: str,
    user_id: str,
    vote: str,
    reasons: list[str] | None = None,
) -> None:
    """Persist a user's vote for a chat message.

    The vote is stored in a hash keyed by ``feedback:{client_id}:{chat_id}``
    where each field is the ``message_id``.  The value is a JSON payload
    containing the ``user_id``, ``vote`` and optional ``reasons``.
    """
    key = f"feedback:{client_id}:{chat_id}"
    payload = {
        "message_id": message_id,
        "user_id": user_id,
        "vote": vote,
        "reasons": reasons or [],
        "timestamp": datetime.utcnow().isoformat(),
    }
    await r.hset(key, message_id, json.dumps(payload))


async def append_event(client_id: str, event: dict) -> None:
    """Append an arbitrary event for a client to Redis."""
    key = f"events:{client_id}"
    event = {"timestamp": datetime.utcnow().isoformat(), **event}
    await r.rpush(key, json.dumps(event))