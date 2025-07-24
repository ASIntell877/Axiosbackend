from langchain.callbacks import get_openai_callback
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.chains import ConversationalRetrievalChain
from langchain.prompts import PromptTemplate
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain.schema import messages_from_dict, messages_to_dict
from pinecone import Pinecone as PineconeClient
from langchain_pinecone import PineconeVectorStore
from app.redis_utils import get_persona, increment_token_usage
from openai import OpenAI
import import_firebase
from firebase_admin import firestore
import os
from datetime import datetime, timedelta, timezone



from app.client_config import CLIENT_CONFIG

# === This pulls client specific system prompts from client_config
def get_prompt_template(system_prompt_str: str):
    return PromptTemplate(
        input_variables=["context", "question"],
        template=system_prompt_str
    )


# === In-memory message history store ===
# This keeps track of past chat messages per session.
memory_store: dict[str, ChatMessageHistory] = {}
memory_timestamps: dict[str, datetime] = {}
SESSION_TIMEOUT = timedelta(minutes=30)

def prune_memory_store() -> None:
    """Remove expired in-memory chat sessions."""
    now = datetime.utcnow()
    expired = [k for k, ts in memory_timestamps.items() if now - ts > SESSION_TIMEOUT]
    for k in expired:
        memory_store.pop(k, None)
        memory_timestamps.pop(k, None)

# Function to check if memory is enabled for a client
def is_memory_enabled(client_id: str) -> bool:
    """Return True if chat memory is enabled for the client."""
    return CLIENT_CONFIG.get(client_id, {}).get("has_chat_memory", False)

def get_memory(chat_id: str, client_id: str) -> ChatMessageHistory:
    """Retrieve chat history from Firestore or in-memory store."""
    if is_memory_enabled(client_id):
        # If memory is enabled, fetch chat history from Firestore
        return get_firebase_memory(client_id, chat_id)
    else:
        # Prune memory store
        prune_memory_store()
        key = f"{client_id}:{chat_id}"
        if key not in memory_store:
            memory_store[key] = ChatMessageHistory()
        memory_timestamps[key] = datetime.utcnow()
        return memory_store[key]

def save_firebase_memory(client_id: str, chat_id: str, chat_history: ChatMessageHistory):
    """Save chat memory to Firestore and add TTL timestamp."""
    db = firestore.client()
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")

    # Set expiration timestamp 30 minutes from now (UTC with tz info)
    expiry_time = datetime.now(timezone.utc) + timedelta(minutes=30)

    doc_ref.set({
        "history": messages_to_dict(chat_history.messages),
        "timestamp_expires": expiry_time
    })
    print(f"Saved memory for session {chat_id} for client {client_id} to Firestore.")

def get_firebase_memory(client_id: str, chat_id: str) -> ChatMessageHistory:
    """Retrieve chat memory from Firestore."""
    db = firestore.client()
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    doc = doc_ref.get()
    history = ChatMessageHistory()
    if doc.exists:
        stored = doc.to_dict().get("history", [])
        history.messages = messages_from_dict(stored)
    return history

def get_qa_chain(config: dict):
    """
    Builds and returns the QA chain (ConversationalRetrievalChain) for the client.

    Args:
        config (dict): Client-specific configuration dictionary.

    Returns:
        RunnableWithMessageHistory: A runnable chain that maintains chat history.
    """

    # Create the prompt template using client-specific system prompt
    chat_prompt = get_prompt_template(config["system_prompt"])

    # Initialize embeddings with the OpenAI key and embedding model
    embeddings = OpenAIEmbeddings(
        model=config["embedding_model"],
        openai_api_key=config["openai_api_key"]
    )

    # Connect to Pinecone with the client's API key and index
    pc = PineconeClient(api_key=config["pinecone_api_key"])
    index = pc.Index(config["pinecone_index_name"])

    # Create Pinecone vector store for retrieval
    vectorstore = PineconeVectorStore(index=index, embedding=embeddings, text_key="text")

    # Initialize ChatOpenAI with the GPT model and OpenAI key
    llm = ChatOpenAI(
        model_name=config["gpt_model"],
        temperature=0.7,
        max_tokens=700,
        openai_api_key=config["openai_api_key"],
        streaming=True,          # Enable streaming
        stream_usage=True        # Enable token usage tracking
    )

    # Build the base ConversationalRetrievalChain using LangChain
    base_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=vectorstore.as_retriever(search_kwargs={"k": config["max_chunks"]}),
        combine_docs_chain_kwargs={
            "prompt": chat_prompt,
            "document_variable_name": "context"
        },
        return_source_documents=True
    )

    # Wrap the chain with message memory, using client ID to separate sessions
    return RunnableWithMessageHistory(
        base_chain,
        lambda session_id: get_memory(session_id, config["client_id"]),
        input_messages_key="question",
        history_messages_key="chat_history"
    )

# Call OpenAI, log token usage
def get_response(chat_id: str, question: str, client_id: str, allow_fallback: bool = False):
    print(f"\n--- Incoming request ---")
    print(f"client_id: {client_id}")
    print(f"chat_id: {chat_id}")
    print(f"question: {question}")

    config = CLIENT_CONFIG.get(client_id)
    if not config:
        raise ValueError(f"Unknown client ID: {client_id}")

    config["client_id"] = client_id  # MUST be passed to memory/session logic
    if is_memory_enabled(client_id):
        print("Chat memory is enabled for this client.")
        chat_history = get_memory(chat_id, client_id)  # Retrieve previous chat history
    else:
        print("Chat memory is disabled for this client.")
        chat_history = []  # No history for this client
        
    redis_persona = get_persona(client_id)

    if redis_persona:
        print("⚙️ Dynamic persona loaded from Redis")

        if isinstance(redis_persona, dict):
            prompt_text = redis_persona.get("prompt", "") or ""
            max_chunks = redis_persona.get("max_chunks")
        else:
            prompt_text = redis_persona or ""
            max_chunks = None

        if "{context}" not in prompt_text or "{question}" not in prompt_text:
            print("⚠️ Placeholders missing in Redis persona, appending defaults.")
            prompt_text = prompt_text.strip() + "\n\nContext:\n{context}\n\nQuestion:\n{question}"

        config["system_prompt"] = prompt_text

        if max_chunks is not None:
            print(f"⚙️ Overriding max_chunks to {max_chunks} from Redis persona")
            config["max_chunks"] = max_chunks
    else:
        print("📝 Using static system prompt from CLIENT_CONFIG")

    if "max_chunks" not in config:
        config["max_chunks"] = 5  # your preferred default

    print(f"Using Pinecone index: {config['pinecone_index_name']}")
    print(f"System prompt (first line): {config['system_prompt'].splitlines()[0]}")
    print(f"Using max_chunks: {config['max_chunks']}")

    # Using get_openai_callback context manager for automatic token tracking
    with get_openai_callback() as callback:
        # Call the LangChain QA chain
        qa_chain = get_qa_chain(config)
        retriever = qa_chain.chain.retriever
        retrieved_docs = retriever.get_relevant_documents(question)

        if not retrieved_docs:
            print("⚠️ No relevant documents found in vector index.")
            if not allow_fallback:
                print("🔒 Fallback disabled. Returning default no-answer response.")
                return {
                    "answer": "No relevant information was found in the index. Please contact a staff member for help.",
                    "source_documents": [],
                    "token_usage": 0,
                    "cost_estimation": 0.0
                }
            else:
                print("⚠️ Fallback allowed. Proceeding with general GPT response.")

        result = qa_chain.invoke(
            {"question": question},
            config={"configurable": {"session_id": chat_id}}
        )
        result["source_documents"] = retrieved_docs  # ✅ Ensure docs are returned

        # Access token usage and cost from the callback
        token_usage = callback.total_tokens  # Correct way to access total tokens
        cost_estimation = callback.total_cost  # Correct way to access cost

        print(f"✅ LangChain token usage: {token_usage} tokens")
        print(f"💰 Estimated cost: ${cost_estimation:.4f}")

        # Log usage to Redis
        increment_token_usage(
            api_key=client_id,
            token_count=token_usage,
            model=config["gpt_model"]
        )

        result["token_usage"] = token_usage
        result["cost_estimation"] = cost_estimation

    return result
