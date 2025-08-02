import os
import sys
import types
import asyncio

class DummyCallback:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        pass
    total_tokens = 0
    total_cost = 0.0

# Stub required external modules before importing app.chatbot
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
    "redis.asyncio": types.ModuleType("redis.asyncio"),
    "dotenv": types.ModuleType("dotenv"),
}

# basic message classes
class SystemMessage:
    def __init__(self, content):
        self.content = content
        self.type = "system"
class HumanMessage:
    def __init__(self, content):
        self.content = content
        self.type = "human"
class AIMessage:
    def __init__(self, content):
        self.content = content
        self.type = "ai"

class ChatMessageHistory:
    def __init__(self):
        self.messages = []
    def add_message(self, msg):
        self.messages.append(msg)
    def add_user_message(self, text):
        self.messages.append(HumanMessage(text))
    def add_ai_message(self, text):
        self.messages.append(AIMessage(text))

stub_modules["langchain.schema"].SystemMessage = SystemMessage
stub_modules["langchain.schema"].HumanMessage = HumanMessage
stub_modules["langchain.schema"].AIMessage = AIMessage
stub_modules["langchain_community.chat_message_histories.in_memory"].ChatMessageHistory = ChatMessageHistory

stub_modules["langchain_community.callbacks.manager"].get_openai_callback = lambda: DummyCallback()
stub_modules["langchain_openai"].OpenAIEmbeddings = object
stub_modules["langchain_openai"].ChatOpenAI = object
stub_modules["langchain.chains"].ConversationalRetrievalChain = object
stub_modules["langchain.prompts"].PromptTemplate = object
stub_modules["langchain_core.runnables.history"].RunnableWithMessageHistory = object
stub_modules["pinecone"].Pinecone = object
stub_modules["langchain_pinecone"].PineconeVectorStore = object
stub_modules["redis"].from_url = lambda *a, **k: object()
stub_modules["redis.asyncio"].from_url = lambda *a, **k: object()
stub_modules["dotenv"].load_dotenv = lambda *a, **k: None

for name, module in stub_modules.items():
    sys.modules.setdefault(name, module)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.chatbot import get_response
import app.chatbot as chatbot_module
import app.redis_memory as redis_memory_module

# Ensure modules use our message classes
chatbot_module.SystemMessage = SystemMessage
chatbot_module.HumanMessage = HumanMessage
chatbot_module.AIMessage = AIMessage
redis_memory_module.SystemMessage = SystemMessage
redis_memory_module.HumanMessage = HumanMessage
redis_memory_module.AIMessage = AIMessage


def test_summary_injected(monkeypatch):
    history = ChatMessageHistory()
    history.add_user_message("hello")
    history.add_ai_message("hi there")

    # Dummy LLM to mock ChatOpenAI
    class DummyLLM:
        def __init__(self, *args, **kwargs):
            self.prompts = []
        async def ainvoke(self, prompt):
            self.prompts.append(prompt)
            return chatbot_module.AIMessage("summary-text")
    dummy_llm = DummyLLM()
    monkeypatch.setattr(chatbot_module, "ChatOpenAI", lambda *a, **k: dummy_llm)

    config = {
        "openai_api_key": "key",
        "pinecone_api_key": "pkey",
        "embedding_model": "e",
        "pinecone_index_name": "i",
        "gpt_model": "gpt",
        "max_chunks": 1,
        "system_prompt": "Base prompt",
        "has_chat_memory": True,
        "enable_memory_summary": True,
        "memory_options": {"summary_max_messages": 2},
    }

    async def fake_get_client_config(cid):
        return config
    monkeypatch.setattr(chatbot_module, "get_client_config", fake_get_client_config)
    monkeypatch.setattr(chatbot_module, "get_persona", lambda cid: None)
    monkeypatch.setattr(chatbot_module, "get_memory", lambda chat_id, client_id: history)
    class DummyChain:
        async def ainvoke(self, *a, **k):
            return {"answer": "ok"}
    class DummyRetriever:
        async def ainvoke(self, *a, **k):
            return ["doc"]
    captured = {}
    def fake_get_qa_chain(cfg, ch):
        captured["prompt"] = cfg["system_prompt"]
        return DummyChain(), DummyRetriever()
    monkeypatch.setattr(chatbot_module, "get_qa_chain", fake_get_qa_chain)
    async def fake_increment_token_usage(**k):
        return None
    monkeypatch.setattr(chatbot_module, "increment_token_usage", fake_increment_token_usage)
    monkeypatch.setattr(chatbot_module, "get_openai_callback", lambda: DummyCallback())

    async def run():
        res = await get_response("chat", "question?", "cid")
        assert res["answer"] == "ok"
        assert captured["prompt"].startswith("Recent conversation summary:\nsummary-text\n\n")
        assert any("Summarize the following conversation" in p for p in dummy_llm.prompts)

    asyncio.run(run())