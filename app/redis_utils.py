import os
import redis
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("REDIS_URL")
r = redis.from_url(redis_url, decode_responses=True)

def get_persona(client_id):
    return r.get(f"persona:{client_id}")

def set_persona(client_id, persona_prompt):
    r.set(f"persona:{client_id}", persona_prompt)

def save_chat_message(client_id, session_id, role, content):
    key = f"chat:{client_id}:{session_id}"
    r.rpush(key, f"{role}:{content}")

def get_chat_history(client_id, session_id):
    key = f"chat:{client_id}:{session_id}"
    return r.lrange(key, 0, -1)
