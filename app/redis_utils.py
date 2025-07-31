import os
import redis.asyncio as redis
import json
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("REDIS_URL")
r = redis.from_url(redis_url, decode_responses=True)

async def get_last_seen(client_id: str, chat_id: str) -> datetime | None:
    raw = await r.get(f"ls:{client_id}:{chat_id}")
    if raw:
        return datetime.fromisoformat(raw)
    return None

SESSION_TIMEOUT = timedelta(minutes=30)
async def set_last_seen(client_id: str, chat_id: str, when: datetime):
    ttl_seconds = int(SESSION_TIMEOUT.total_seconds())
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

