import pytest

from app import redis_utils


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

    async def xadd(self, stream, mapping):
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
async def test_append_feedback_event(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_utils, "r", fake)

    await redis_utils.append_feedback_event("c", "m", "u", "up")

    assert "feedback_stream" in fake.streams
    event = fake.streams["feedback_stream"][0]
    assert event["client"] == "c"
    assert event["message"] == "m"
    assert event["user"] == "u"
    assert event["vote"] == "up"