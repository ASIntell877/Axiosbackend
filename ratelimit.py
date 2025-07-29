import redis.asyncio as redis
import os
import time
from fastapi import HTTPException

redis_url = os.getenv("REDIS_URL")
r = redis.from_url(redis_url, decode_responses=True)

async def check_rate_limit(api_key: str, max_requests: int = 20, window_seconds: int = 60):
    key = f"ratelimit:{api_key}"
    current = await r.get(key)

    if current and int(current) >= max_requests:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if not current:
        await r.set(key, 1, ex=window_seconds)
    else:
        await r.incr(key)

async def track_usage(api_key: str, monthly_limit: int = None, tokens: int = 0):
    # Daily request count
    date = time.strftime("%Y-%m-%d")
    daily_key = f"usage:{api_key}:{date}"
    await r.incr(daily_key)

    # Monthly request count
    quota_key = f"quota_usage:{api_key}"
    current_quota = await r.incr(quota_key)

    # Apply 30-day expiry if not already set
    if await r.ttl(quota_key) == -1:
        await r.expire(quota_key, 60 * 60 * 24 * 30)

    # 🚫 Enforce monthly request limit
    if monthly_limit and current_quota > monthly_limit:
        raise HTTPException(status_code=429, detail="Monthly quota exceeded")

    # Optional: Track tokens per day if provided
    if tokens > 0:
        token_key = f"token_usage:{api_key}:{date}"
        await r.incrby(token_key, tokens)
