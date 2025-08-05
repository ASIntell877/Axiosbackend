from langchain_community.callbacks.manager import get_openai_callback
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.chains import ConversationalRetrievalChain
from langchain.prompts import PromptTemplate
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain.schema import SystemMessage, HumanMessage, AIMessage
from pinecone import Pinecone as PineconeClient
from langchain_pinecone import PineconeVectorStore
from app.redis_utils import get_persona, increment_token_usage
from app import redis_memory
import os
import tiktoken
from datetime import datetime, timedelta, timezone
import re
import inspect

from app.client_config import CLIENT_CONFIG
from app.redis_utils import get_client_config


def get_prompt_template(system_prompt_str: str):
    return PromptTemplate(
        input_variables=["context", "question"],
        template=system_prompt_str
    )


async def is_memory_enabled(client_id: str) -> bool:
    config = await get_client_config(client_id)
    if not config:
        return False
    return config.get("has_chat_memory", False)


async def get_memory(chat_id: str, client_id: str) -> ChatMessageHistory:
    """Load session memory from Redis or return a new history if disabled."""
    if not await is_memory_enabled(client_id):
        return ChatMessageHistory()
    history = await redis_memory.get_memory(client_id, chat_id)
    print(
        f"[MEMORY DEBUG] Loaded Redis memory for {client_id}:{chat_id} ({len(history.messages)} messages)"
    )
    return history


async def save_redis_memory(client_id: str, chat_id: str, chat_history: ChatMessageHistory) -> None:
    """Persist session history to Redis."""
    await redis_memory.save_memory(client_id, chat_id, chat_history)
    print(f"[MEMORY DEBUG] Saved memory for session {chat_id} client {client_id} to Redis")


# Identity extraction to insert as system message once

def extract_user_name(message: str) -> str | None:
    match = (
        re.search(r"call me (\w+)", message.lower())
        or re.search(r"my name is (\w+)", message.lower())
        or re.search(r"i am (\w+)", message.lower())
    )
    if match:
        return match.group(1).capitalize()
    return None


async def summarize_recent_messages_with_llm(
    history: ChatMessageHistory,
    config: dict,
    max_messages: int | None = None,
) -> str:
    options = config.get("memory_options", {})
    if max_messages is None:
        max_messages = options.get("summary_max_messages", 5)
    recent = history.messages[-max_messages:]
    if not recent:
        return ""
    lines = []
    for msg in recent:
        if isinstance(msg, HumanMessage):
            role = "User"
        elif isinstance(msg, AIMessage):
            role = config.get("persona_name", "Assistant")
        elif isinstance(msg, SystemMessage):
            role = "System"
        else:
            role = "Other"
        lines.append(f"{role}: {msg.content}")
    prompt = (
        "Summarize the following conversation briefly.\n"
        "If the user's name is known, include it in the summary and remind the assistant to refer to the user by name in future responses.\n"
        + "\n".join(lines)
    )

    llm = ChatOpenAI(
        model_name=config.get("gpt_model"),
        openai_api_key=config.get("openai_api_key"),
        temperature=0.0,
    )
    result = await llm.ainvoke(prompt)
    summary = getattr(result, "content", str(result))
    print(f"[MEMORY DEBUG] Generated LLM summary:\n{summary}")
    return summary




def get_qa_chain(config: dict, chat_history: ChatMessageHistory):
    chat_prompt = get_prompt_template(config["system_prompt"])
    embeddings = OpenAIEmbeddings(
        model=config["embedding_model"],
        openai_api_key=config["openai_api_key"],
    )
    pc = PineconeClient(api_key=config["pinecone_api_key"])
    index = pc.Index(config["pinecone_index_name"])
    vectorstore = PineconeVectorStore(index=index, embedding=embeddings, text_key="text")
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

    def load_history(session_id: str) -> ChatMessageHistory:
        return chat_history

    qa_with_history = RunnableWithMessageHistory(
        base_chain,
        load_history,
        input_messages_key="question",
        history_messages_key="chat_history",
    )
    return qa_with_history, retriever


async def get_response(
    chat_id: str,
    question: str,
    client_id: str,
    allow_fallback: bool = False
):
    print("\n--- Incoming request ---")
    print(f"client_id: {client_id}, chat_id: {chat_id}, question: {question}")
    config = await get_client_config(client_id)
    if not config:
        raise ValueError(f"Unknown client ID: {client_id}")
    # Work with a per-request copy so global settings remain unchanged
    config = config.copy()
    config["client_id"] = client_id

    # Load session memory
    mem_res = get_memory(chat_id, client_id)
    chat_history = await mem_res if inspect.isawaitable(mem_res) else mem_res

    # Inject user name if enabled
    if config.get("enable_user_naming"):
        name = extract_user_name(question)
        if name:
            existing = [m.content for m in chat_history.messages if "identified themselves as" in m.content]
            if not existing:
                chat_history.add_message(SystemMessage(content=f"The user has identified themselves as {name}. Refer to them by this name."))
                print(f"[MEMORY DEBUG] Added system message for user name: {name}")

    # Build system_prompt: dynamic if flagged, else use static config
    use_dynamic = config.get("use_dynamic_persona", False)

    if use_dynamic:
        # load dynamic persona from Redis
        redis_persona = await get_persona(client_id)
        if redis_persona:
            prompt_text = (
                redis_persona.get("prompt")
                if isinstance(redis_persona, dict)
                else redis_persona
            )
            # ensure context/question placeholders
            if "{context}" not in prompt_text or "{question}" not in prompt_text:
                prompt_text += "\n\nContext:\n{context}\n\nQuestion:\n{question}"
            config["system_prompt"] = prompt_text
            print("[MEMORY DEBUG] Using Redis persona for system_prompt")
    else:
        # static prompt from client_config
        sp = config.get("system_prompt", "")
        # ensure RAG placeholders are always present
        if "{context}" not in sp or "{question}" not in sp:
            sp += "\n\nContext:\n{context}\n\nQuestion:\n{question}"
        config["system_prompt"] = sp
        # fallback chunk size if missing
        if "max_chunks" not in config:
            config["max_chunks"] = 5

    # Inject session-memory summary if enabled
    if config.get("enable_memory_summary"):
        summary = await summarize_recent_messages_with_llm(chat_history, config)
        if summary:
            config["system_prompt"] = (
                f"Recent conversation summary:\n{summary}\n\n"
                f"{config['system_prompt']}"
            )
            print("[MEMORY DEBUG] Injected conversation summary into system_prompt")
    user_name = "my friend" # attempt to pull name from session memory, default to "my friend"
    for msg in chat_history.messages:
        if isinstance(msg, SystemMessage) and "identified themselves as" in msg.content:
            user_name = msg.content.split("identified themselves as ")[1].rstrip(".")
            break
    print(f"[DEBUG] user_name from memory: {user_name}")


    # Perform the .format() with the user_name
    config["system_prompt"] = config["system_prompt"].format(
        user_name=user_name, context="{context}", question="{question}"
    )
    print(f"[DEBUG] Final system prompt:\n{config['system_prompt']}")



    # Invoke QA chain
    with get_openai_callback() as callback:
        qa_chain, retriever = get_qa_chain(config, chat_history)
        retrieved_docs = await retriever.ainvoke(question)
        if not retrieved_docs and not allow_fallback:
            return {"answer": "No relevant information found.", "source_documents": [], "token_usage": 0, "cost_estimation": 0.0}
        result = await qa_chain.ainvoke(
            {"question": question},
            config={"configurable": {"session_id": chat_id}},
        )
        result["source_documents"] = retrieved_docs
        token_usage = callback.total_tokens
        cost_estimation = callback.total_cost
        await increment_token_usage(api_key=client_id, token_count=token_usage, model=config.get("gpt_model", "unknown"))
        result.update({"token_usage": token_usage, "cost_estimation": cost_estimation})
    return result
