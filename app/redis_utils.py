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
    r.rpush(key, f"{role}:{content}")

def get_chat_history(client_id, session_id):
    key = f"chat:{client_id}:{session_id}"
    return r.lrange(key, 0, -1)

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

def increment_token_usage(api_key: str, tokens: int):
    if tokens <= 0:
        return

    date = time.strftime("%Y-%m-%d")
    token_key = f"token_usage:{api_key}:{date}"
    r.incrby(token_key, tokens)
