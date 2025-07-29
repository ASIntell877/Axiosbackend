from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from langchain.schema import AIMessage, HumanMessage, SystemMessage

from .redis_utils import r, SESSION_TIMEOUT


def _make_key(client_id: str, chat_id: str) -> str:
    return f"chatmem:{client_id}:{chat_id}"


async def save_memory(client_id: str, chat_id: str, chat_history: ChatMessageHistory) -> None:
    """Persist chat history to Redis as raw strings with expiration."""
    key = _make_key(client_id, chat_id)
    ttl_seconds = int(SESSION_TIMEOUT.total_seconds())
    pipe = r.pipeline()
    pipe.delete(key)
    for msg in chat_history.messages:
        if isinstance(msg, AIMessage):
            role = "ai"
        elif isinstance(msg, HumanMessage):
            role = "human"
        elif isinstance(msg, SystemMessage):
            role = "system"
        else:
            role = msg.type
        pipe.rpush(key, f"{role}:{msg.content}")
    pipe.expire(key, ttl_seconds)
    await pipe.execute()


async def get_memory(client_id: str, chat_id: str) -> ChatMessageHistory:
    """Retrieve chat history from Redis and rebuild ChatMessageHistory."""
    key = _make_key(client_id, chat_id)
    entries = await r.lrange(key, 0, -1)
    history = ChatMessageHistory()
    for entry in entries:
        if ":" in entry:
            role, content = entry.split(":", 1)
        else:
            role, content = "human", entry
        if role == "ai":
            history.add_ai_message(content)
        elif role == "human":
            history.add_user_message(content)
        elif role == "system":
            history.add_message(SystemMessage(content=content))
        else:
            history.add_message(HumanMessage(content=content))
    return history


async def delete_memory(client_id: str, chat_id: str) -> None:
    """Remove chat history from Redis."""
    key = _make_key(client_id, chat_id)
    await r.delete(key)