import os
import redis
import json
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("REDIS_URL")
r = redis.from_url(redis_url, decode_responses=True)

def get_persona(client_id):
    raw = r.get(f"persona:{client_id}")
    if raw is None:
        return None
    try:
        return json.loads(raw)  # parse the JSON string into a dict
    except json.JSONDecodeError:
        return raw  # fallback to raw string for old format

def set_persona_json(client_id, prompt, index=None, style=None):
    persona_data = {
        "prompt": prompt,
        "index": index,
        "style": style
    }
    r.set(f"persona:{client_id}", json.dumps(persona_data))

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
        updated = additional_text.strip()
    else:
        updated = existing.strip() + "\n\n" + additional_text.strip()
    r.set(key, updated)