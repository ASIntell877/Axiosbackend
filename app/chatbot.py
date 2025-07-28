from langchain_community.callbacks.manager import get_openai_callback
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.chains import ConversationalRetrievalChain
from langchain.prompts import PromptTemplate
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain.schema import (
    messages_from_dict,
    messages_to_dict,
    SystemMessage,
    HumanMessage,
    AIMessage,
)
from pinecone import Pinecone as PineconeClient
from langchain_pinecone import PineconeVectorStore
from app.redis_utils import get_persona, increment_token_usage
from firebase_admin import firestore
import os
from datetime import datetime, timedelta, timezone
import re

from app.client_config import CLIENT_CONFIG

# === This pulls client specific system prompts from client_config
# Now we will no longer inject identity via prompt; instead rely on session memory


def get_prompt_template(system_prompt_str: str):
    return PromptTemplate(
        input_variables=["context", "question"], template=system_prompt_str
    )

# === Session-based memory store ===
import asyncio

memory_store: dict[str, ChatMessageHistory] = {}
memory_timestamps: dict[str, datetime] = {}
memory_lock = asyncio.Lock()
SESSION_TIMEOUT = timedelta(minutes=30)


async def prune_memory_store() -> None:
    now = datetime.utcnow()
    async with memory_lock:
        expired = [
            k for k, ts in memory_timestamps.items() if now - ts > SESSION_TIMEOUT
        ]
        for k in expired:
            print(f"[MEMORY DEBUG] Pruning expired memory: {k}")
            memory_store.pop(k, None)
            memory_timestamps.pop(k, None)


def is_memory_enabled(client_id: str) -> bool:
    return CLIENT_CONFIG.get(client_id, {}).get("has_chat_memory", False)


async def get_memory(chat_id: str, client_id: str) -> ChatMessageHistory:
    key = f"{client_id}:{chat_id}"
    if is_memory_enabled(client_id):
        print(f"[MEMORY DEBUG] Loading memory from Firestore for {key}")
        history = get_firebase_memory(client_id, chat_id)
    else:
        await prune_memory_store()
        async with memory_lock:
            if key not in memory_store:
                memory_store[key] = ChatMessageHistory()
                print(f"[MEMORY DEBUG] Creating new in-memory history for {key}")
            history = memory_store[key]
    async with memory_lock:
        memory_timestamps[key] = datetime.utcnow()
    print(
        f"[MEMORY DEBUG] Current chat history messages ({len(history.messages)}): {[m.content for m in history.messages]}"
    )
    return history


def save_firebase_memory(
    client_id: str, chat_id: str, chat_history: ChatMessageHistory
):
    db = firestore.client()
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    expiry_time = datetime.now(timezone.utc) + SESSION_TIMEOUT
    doc_ref.set(
        {
            "history": messages_to_dict(chat_history.messages),
            "timestamp_expires": expiry_time,
        }
    )
    print(
        f"[MEMORY DEBUG] Saved memory for session {chat_id} client {client_id} to Firestore"
    )


def get_firebase_memory(client_id: str, chat_id: str) -> ChatMessageHistory:
    db = firestore.client()
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    doc = doc_ref.get()
    history = ChatMessageHistory()
    if doc.exists:
        stored = doc.to_dict().get("history", [])
        history.messages = messages_from_dict(stored)
        print(
            f"[MEMORY DEBUG] Retrieved {len(history.messages)} messages from Firestore for {client_id}:{chat_id}"
        )
    return history


# === Identity extraction to insert as system message once ===
def extract_user_name(message: str) -> str | None:
    match = (
        re.search(r"call me (\w+)", message.lower())
        or re.search(r"my name is (\w+)", message.lower())
        or re.search(r"i am (\w+)", message.lower())
    )
    if match:
        return match.group(1).capitalize()
    return None


def summarize_recent_messages(
    history: ChatMessageHistory, max_messages: int = 5
) -> str:
    """
    Build a simple recap of the last few turns.
    """
    recent = history.messages[-max_messages:]
    lines = []
    for msg in recent:
        role = (
            "User"
            if isinstance(msg, HumanMessage)
            else "Assistant" if isinstance(msg, AIMessage) else "Other"
        )
        lines.append(f"{role}: {msg.content}")
    summary = "\n".join(lines)
    print(f"[MEMORY DEBUG] Generated summary:\n{summary}")
    return summary


def get_qa_chain(config: dict, chat_history: ChatMessageHistory):
    chat_prompt = get_prompt_template(config["system_prompt"])
    embeddings = OpenAIEmbeddings(
        model=config["embedding_model"],
        openai_api_key=config["openai_api_key"],
        model=config["embedding_model"], openai_api_key=config["openai_api_key"]        
    )
    pc = PineconeClient(api_key=config["pinecone_api_key"])
    index = pc.Index(config["pinecone_index_name"])
    vectorstore = PineconeVectorStore(
        index=index, embedding=embeddings, text_key="text"
    )
    llm = ChatOpenAI(
        model_name=config["gpt_model"],
        temperature=0.7,
        max_tokens=700,
        openai_api_key=config["openai_api_key"],
        streaming=True,
        stream_usage=True,
    )
    retriever = vectorstore.as_retriever(search_kwargs={"k": config["max_chunks"]})
    base_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        combine_docs_chain_kwargs={
            "prompt": chat_prompt,
            "document_variable_name": "context",
        },
        return_source_documents=True,
    )
    
    async def load_history(session_id: str) -> ChatMessageHistory:
        return await get_memory(session_id, config["client_id"])


    qa_with_history = RunnableWithMessageHistory(
        base_chain,
        load_history,
        input_messages_key="question",
        history_messages_key="chat_history",
    )
    return qa_with_history, retriever


# === Main response function ===
async def get_response(
    chat_id: str, question: str, client_id: str, allow_fallback: bool = False
):
    print(f"\n--- Incoming request ---")
    print(f"client_id: {client_id}, chat_id: {chat_id}, question: {question}")
    config = CLIENT_CONFIG.get(client_id)
    if not config:
        raise ValueError(f"Unknown client ID: {client_id}")
    config["client_id"] = client_id

    # --- Load session memory ---
    chat_history = await get_memory(chat_id, client_id)
    # --- Inject name as system message if extracted and not already present ---
    if config.get("enable_user_naming"):
        name = extract_user_name(question)
        if name:
            # prevent duplicate
            existing_names = [
                m.content
                for m in chat_history.messages
                if "identified themselves as" in m.content
            ]
            if not existing_names:
                system_msg = SystemMessage(
                    content=f"The user has identified themselves as {name}. Refer to them by this name."
                )
                chat_history.add_message(system_msg)
                print(f"[MEMORY DEBUG] Added system message for user name: {name}")


    # --- Build dynamic persona or fallback ---
    redis_persona = get_persona(client_id)
    if redis_persona:
        prompt_text = (
            redis_persona.get("prompt")
            if isinstance(redis_persona, dict)
            else redis_persona
        )
        if "{context}" not in prompt_text or "{question}" not in prompt_text:
            prompt_text += "\n\nContext:\n{context}\n\nQuestion:\n{question}"
        config["system_prompt"] = prompt_text
        if config.get("enable_memory_summary"):
            summary = summarize_recent_messages(chat_history)
            if summary:
                config["system_prompt"] = (
                    f"Recent conversation summary:\n{summary}\n\n"
                    f"{config['system_prompt']}"
                )
                print("[MEMORY DEBUG] Prepended session summary to system_prompt")

        print(f"Using Pinecone index: {config['pinecone_index_name']}")
        if isinstance(redis_persona, dict) and redis_persona.get("max_chunks"):
            config["max_chunks"] = redis_persona.get("max_chunks")
        print(f"⚙️ Dynamic persona loaded for {client_id}")
    else:
        print("📝 Using static system prompt from CLIENT_CONFIG")
        if "max_chunks" not in config:
            config["max_chunks"] = 5

    print(f"Using Pinecone index: {config['pinecone_index_name']}")
    print(f"System prompt (first line): {config['system_prompt'].splitlines()[0]}")
    print(f"max_chunks: {config['max_chunks']}")

    # --- Invoke the QA chain ---
    with get_openai_callback() as callback:
        qa_chain, retriever = get_qa_chain(config, chat_history)
        retrieved_docs = retriever.get_relevant_documents(question)
        if not retrieved_docs and not allow_fallback:
            return {
                "answer": "No relevant information found.",
                "source_documents": [],
                "token_usage": 0,
                "cost_estimation": 0.0,
            }
        result = qa_chain.invoke(
            {"question": question}, config={"configurable": {"session_id": chat_id}}
        )
        result["source_documents"] = retrieved_docs
        token_usage = callback.total_tokens
        cost_estimation = callback.total_cost
        increment_token_usage(
            api_key=client_id, token_count=token_usage, model=config["gpt_model"]
        )
        result.update({"token_usage": token_usage, "cost_estimation": cost_estimation})
    return result
