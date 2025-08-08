import os
import sys
import types

import pytest
from pydantic import ValidationError

# Ensure required modules/env are available when importing main
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
sys.modules['ratelimit'] = types.SimpleNamespace(
    check_rate_limit=lambda *a, **k: None,
    track_usage=lambda *a, **k: None,
)

# Ensure repository root is on path for module imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import redis_utils
from main import FeedbackRequest


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.streams = {}

    async def hsetnx(self, key, field, value):
        h = self.hashes.setdefault(key, {})
        if field in h:
            return 0
        h[field] = value
        return 1

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def expire(self, key, ttl):
        # For testing purposes, expiration is a no-op
        return True

    async def xadd(self, stream, mapping, **kwargs):
        self.streams.setdefault(stream, []).append(mapping)


@pytest.mark.asyncio
async def test_record_feedback_vote(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_utils, "r", fake)

    first = await redis_utils.record_feedback_vote("c", "m", "u", "up")
    second = await redis_utils.record_feedback_vote("c", "m", "u", "down")

    assert first is True
    assert second is False
    assert await fake.hget("feedback:c:m", "u") == "up"


@pytest.mark.asyncio
async def test_record_feedback_vote_invalid(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_utils, "r", fake)

    with pytest.raises(ValueError):
        await redis_utils.record_feedback_vote("c", "m", "u", "invalid")


@pytest.mark.asyncio
async def test_append_feedback_event(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_utils, "r", fake)

    await redis_utils.append_feedback_event("c", "m", "u", "up")
    stream_key = "feedback_stream:c"
    assert stream_key in fake.streams
    event = fake.streams[stream_key][0]
    assert event["message_id"] == "m"
    assert event["user_id"] == "u"
    assert event["vote"] == "up"


def test_feedback_request_invalid_vote():
    with pytest.raises(ValidationError):
        FeedbackRequest(client_id="c", message_id="m", user_id="u", vote="bad")


@pytest.mark.asyncio
async def test_feedback_disabled_flag(monkeypatch):
    import main

    async def fake_get_client_config(cid):
        return {"enable_feedback": False}

    monkeypatch.setattr(main, "get_client_config", fake_get_client_config)

    async def fake_record_feedback_vote(*args, **kwargs):
        raise AssertionError("should not record")

    async def fake_append_feedback_event(*args, **kwargs):
        raise AssertionError("should not append")

    monkeypatch.setattr(main, "record_feedback_vote", fake_record_feedback_vote)
    monkeypatch.setattr(main, "append_feedback_event", fake_append_feedback_event)

    req = main.FeedbackRequest(client_id="c", message_id="m", user_id="u", vote="up")

    with pytest.raises(main.HTTPException) as exc:
        await main.submit_feedback(req, api_key_info={"client": "c"})

    assert exc.value.status_code in (403, 404)