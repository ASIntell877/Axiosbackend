import os
import sys
import types
import pytest

class DummyCallback:
    def __init__(self):
        self.total_tokens = 0
        self.total_cost = 0.0
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        pass

# Create stub modules so app.chatbot imports succeed without external deps
stub_modules = {
    "langchain_community.callbacks.manager": types.ModuleType("lc_cb"),
    "langchain_openai": types.ModuleType("lc_openai"),
    "langchain.chains": types.ModuleType("lc_chains"),
    "langchain.prompts": types.ModuleType("lc_prompts"),
    "langchain_community.chat_message_histories.in_memory": types.ModuleType("lc_hist"),
    "langchain_core.runnables.history": types.ModuleType("lc_run_history"),
    "langchain.schema": types.ModuleType("lc_schema"),
    "pinecone": types.ModuleType("pinecone"),
    "langchain_pinecone": types.ModuleType("lc_pine"),
    "redis": types.ModuleType("redis"),
    "dotenv": types.ModuleType("dotenv")
}

class _Dummy:
    pass

stub_modules["langchain_community.callbacks.manager"].get_openai_callback = lambda: DummyCallback()
stub_modules["langchain_openai"].OpenAIEmbeddings = _Dummy
stub_modules["langchain_openai"].ChatOpenAI = _Dummy
stub_modules["langchain.chains"].ConversationalRetrievalChain = _Dummy
stub_modules["langchain.prompts"].PromptTemplate = _Dummy
stub_modules["langchain_community.chat_message_histories.in_memory"].ChatMessageHistory = _Dummy
stub_modules["langchain_core.runnables.history"].RunnableWithMessageHistory = _Dummy
stub_modules["langchain.schema"].SystemMessage = _Dummy
stub_modules["langchain.schema"].HumanMessage = _Dummy
stub_modules["langchain.schema"].AIMessage = _Dummy
stub_modules["pinecone"].Pinecone = _Dummy
stub_modules["langchain_pinecone"].PineconeVectorStore = _Dummy
class DummyRedis:
    def __getattr__(self, name):
        return lambda *a, **k: None
stub_modules["redis"].from_url = lambda *a, **k: DummyRedis()
stub_modules["dotenv"].load_dotenv = lambda *a, **k: None

for name, module in stub_modules.items():
    sys.modules.setdefault(name, module)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.chatbot import get_response, CLIENT_CONFIG

class ChatMessageHistory:
    def __init__(self):
        self.messages = []
    def add_message(self, msg):
        self.messages.append(msg)

class DummyChain:
    async def ainvoke(self, *args, **kwargs):
        return {"answer": "ok"}

class DummyRetriever:
    def get_relevant_documents(self, question):
        return []

import asyncio

def test_config_not_mutated(monkeypatch):
    async def run():
        original = CLIENT_CONFIG["maximos"].copy()

        monkeypatch.setattr("app.chatbot.get_openai_callback", lambda: DummyCallback())
        monkeypatch.setattr("app.chatbot.get_persona", lambda client_id: None)
        monkeypatch.setattr("app.chatbot.get_memory", lambda chat_id, client_id: ChatMessageHistory())
        monkeypatch.setattr("app.chatbot.get_qa_chain", lambda config, chat_history: (DummyChain(), DummyRetriever()))
        monkeypatch.setattr("app.chatbot.increment_token_usage", lambda **kwargs: None)

        for _ in range(2):
            await get_response(chat_id="1", question="hi", client_id="maximos")

        assert CLIENT_CONFIG["maximos"] == original

    asyncio.run(run())