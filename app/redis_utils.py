import os
import redis
import json
import time
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("REDIS_URL")
r = redis.from_url(redis_url, decode_responses=True)

def get_persona(client_id):
    raw = r.get(f"persona:{client_id}")
    if raw is None:
        return None
    try:
        return json.loads(raw)  # Return dict with prompt/index/style
    except json.JSONDecodeError:
        return {"prompt": raw}  # Fallback for legacy prompt-only string

def save_chat_message(client_id, session_id, role, content):
    key = f"chat:{client_id}:{session_id}"
    entry = json.dumps({"role": role, "content": content})
    r.rpush(key, entry)


def get_chat_history(client_id, session_id):
    key = f"chat:{client_id}:{session_id}"
    raw_history = r.lrange(key, 0, -1)
    return [json.loads(entry) for entry in raw_history]

def append_to_persona(client_id, additional_text):
    key = f"persona:{client_id}"
    existing = r.get(key)
    if existing is None:
        updated_prompt = additional_text.strip()
        r.set(key, json.dumps({"prompt": updated_prompt}))
    else:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, dict) and "prompt" in parsed:
                parsed["prompt"] = parsed["prompt"].strip() + "\n\n" + additional_text.strip()
                r.set(key, json.dumps(parsed))
            else:
                # fallback in case existing isn't a proper dict
                updated = existing.strip() + "\n\n" + additional_text.strip()
                r.set(key, json.dumps({"prompt": updated}))
        except json.JSONDecodeError:
            updated = existing.strip() + "\n\n" + additional_text.strip()
            r.set(key, json.dumps({"prompt": updated}))

def increment_token_usage(api_key: str, token_count: int, model: str = "unknown"):
    """
    Tracks token usage for a given API key in Redis.
    Includes daily, monthly, and model-specific usage.
    """
    today = time.strftime("%Y-%m-%d")
    month = time.strftime("%Y-%m")

    # 🔹 Keys we'll update
    total_key = f"token_usage:{api_key}:total"
    daily_key = f"token_usage:{api_key}:daily:{today}"
    monthly_key = f"token_usage:{api_key}:monthly:{month}"
    model_key = f"token_usage:{api_key}:model:{model}"

    # 🔹 Increment all counters
    r.incrby(total_key, token_count)
    r.incrby(daily_key, token_count)
    r.incrby(monthly_key, token_count)
    r.incrby(model_key, token_count)

    # 🔹 Optionally: set expiry for daily + monthly keys
    r.expire(daily_key, 60 * 60 * 24 * 31)
    r.expire(monthly_key, 60 * 60 * 24 * 365)

def get_token_usage(api_key: str):
    today = time.strftime("%Y-%m-%d")
    month = time.strftime("%Y-%m")

    total_key = f"token_usage:{api_key}:total"
    daily_key = f"token_usage:{api_key}:daily:{today}"
    monthly_key = f"token_usage:{api_key}:monthly:{month}"

    # Fetch totals safely (default to 0 if none)
    total = int(r.get(total_key) or 0)
    daily = int(r.get(daily_key) or 0)
    monthly = int(r.get(monthly_key) or 0)

    # Fetch per-model usage keys dynamically
    model_prefix = f"token_usage:{api_key}:model:"
    model_tokens = {}
    for key in r.scan_iter(match=f"{model_prefix}*"):
        model_name = key[len(model_prefix):]
        count = int(r.get(key) or 0)
        model_tokens[model_name] = count

    return {
        "total_tokens": total,
        "daily_tokens": daily,
        "monthly_tokens": monthly,
        "per_model_tokens": model_tokens
    }
