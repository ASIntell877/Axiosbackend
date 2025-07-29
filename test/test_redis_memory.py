import os
import sys
import types
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Provide lightweight stand-ins for langchain classes if not installed
if 'langchain_community.chat_message_histories.in_memory' not in sys.modules:
    fake_hist = types.ModuleType('langchain_community.chat_message_histories.in_memory')
    class ChatMessageHistory:
        def __init__(self):
            self.messages = []
        def add_ai_message(self, text):
            self.messages.append(type('M', (), {'type':'ai', 'content': text}))
        def add_user_message(self, text):
            self.messages.append(type('M', (), {'type':'human', 'content': text}))
        def add_message(self, msg):
            self.messages.append(msg)
    fake_hist.ChatMessageHistory = ChatMessageHistory
    sys.modules['langchain_community.chat_message_histories.in_memory'] = fake_hist

if 'langchain.schema' not in sys.modules:
    fake_schema = types.ModuleType('langchain.schema')
    class AIMessage:
        def __init__(self, content):
            self.content = content
    class HumanMessage:
        def __init__(self, content):
            self.content = content
    class SystemMessage:
        def __init__(self, content):
            self.content = content
    fake_schema.AIMessage = AIMessage
    fake_schema.HumanMessage = HumanMessage
    fake_schema.SystemMessage = SystemMessage
    sys.modules['langchain.schema'] = fake_schema

if 'redis.asyncio' not in sys.modules:
    fake_rmod = types.ModuleType('redis.asyncio')
    class Dummy:
        pass
    def from_url(*args, **kwargs):
        return Dummy()
    fake_rmod.from_url = from_url
    sys.modules['redis.asyncio'] = fake_rmod
    root_mod = types.ModuleType('redis')
    root_mod.asyncio = fake_rmod
    sys.modules['redis'] = root_mod

if 'dotenv' not in sys.modules:
    fake_dotenv = types.ModuleType('dotenv')
    def load_dotenv(*args, **kwargs):
        pass
    fake_dotenv.load_dotenv = load_dotenv
    sys.modules['dotenv'] = fake_dotenv

from app import redis_memory
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory

class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []
    def delete(self, key):
        self.commands.append(('delete', key))
    def rpush(self, key, value):
        self.commands.append(('rpush', key, value))
    def expire(self, key, ttl):
        self.commands.append(('expire', key, ttl))
    async def execute(self):
        for cmd in self.commands:
            if cmd[0] == 'delete':
                self.redis.store.pop(cmd[1], None)
            elif cmd[0] == 'rpush':
                self.redis.store.setdefault(cmd[1], []).append(cmd[2])
        self.commands = []

class FakeRedis:
    def __init__(self):
        self.store = {}
    def pipeline(self):
        return FakePipeline(self)
    async def lrange(self, key, start, end):
        lst = self.store.get(key, [])
        if end == -1:
            end = None
        else:
            end = end + 1
        return lst[start:end]
    async def delete(self, key):
        self.store.pop(key, None)
    async def rpush(self, key, value):
        self.store.setdefault(key, []).append(value)

def test_memory_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_memory, 'r', fake)

    async def run():
        history = ChatMessageHistory()
        history.add_user_message('hello')
        history.add_ai_message('hi there')

        await redis_memory.save_memory('client', 'chat', history)
        loaded = await redis_memory.get_memory('client', 'chat')

        assert [m.content for m in loaded.messages] == ['hello', 'hi there']

        await redis_memory.delete_memory('client', 'chat')
        cleared = await redis_memory.get_memory('client', 'chat')
        assert len(cleared.messages) == 0

    import asyncio
    asyncio.run(run())