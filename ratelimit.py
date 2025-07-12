import redis
import os
import time
from fastapi import HTTPException

redis_url = os.getenv("REDIS_URL")
r = redis.from_url(redis_url, decode_responses=True)

def check_rate_limit(api_key: str, max_requests: int = 20, window_seconds: int = 60):
    key = f"ratelimit:{api_key}"
    current = r.get(key)

    if current and int(current) >= max_requests:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if not current:
        r.set(key, 1, ex=window_seconds)
    else:
        r.incr(key)

def track_usage(api_key: str):
    date = time.strftime("%Y-%m-%d")
    key = f"usage:{api_key}:{date}"
    r.incr(key)
