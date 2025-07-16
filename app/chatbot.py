from langchain.callbacks import get_openai_callback
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.chains import ConversationalRetrievalChain
from langchain.prompts import PromptTemplate
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from pinecone import Pinecone as PineconeClient
from langchain_pinecone import PineconeVectorStore
from app.redis_utils import get_persona, increment_token_usage
from openai import OpenAI
from firebase_admin import firestore
from store_chat_firebase import save_memory, get_memory
import os

from app.client_config import client_config

# === This pulls client specific system prompts from client_config
def get_prompt_template(system_prompt_str: str):
    return PromptTemplate(
        input_variables=["context", "question"],
        template=system_prompt_str
    )


# === In-memory message history store ===
# This keeps track of past chat messages per session.
memory_store = {}

# Function to check if memory is enabled for a client
def is_memory_enabled(client_id: str) -> bool:
    """
    Check if chat memory is enabled for a particular client.
    You could replace this with logic to query your Firestore or a config file.
    """
    client_memory_config = {
        "client_id_1": True,  # Memory enabled for client 1
        "client_id_2": False,  # Memory disabled for client 2
    }
    return client_memory_config.get(client_id, False)  # Default to False if client not found

def get_memory(chat_id: str, client_id: str):
    """Retrieve chat history from Firestore or in-memory store."""
    if is_memory_enabled(client_id):
        # If memory is enabled, fetch chat history from Firestore
        return get_firebase_memory(client_id, chat_id)
    else:
        # If memory is not enabled, use in-memory store
        key = f"{client_id}:{chat_id}"
        if key not in memory_store:
            memory_store[key] = []  # Initialize an empty list for new sessions
        return memory_store[key]

def save_memory(client_id: str, chat_id: str, chat_history: list):
    """Save chat history to Firestore or in-memory store."""
    if is_memory_enabled(client_id):
        # If memory is enabled, save to Firestore
        save_firebase_memory(client_id, chat_id, chat_history)
    else:
        # If memory is not enabled, store in memory
        key = f"{client_id}:{chat_id}"
        memory_store[key] = chat_history

def get_firebase_memory(client_id: str, chat_id: str):
    """Retrieve chat memory from Firestore."""
    db = firestore.client()
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict().get("history", [])
    else:
        return []  # Return an empty list if no memory found

def save_firebase_memory(client_id: str, chat_id: str, chat_history: list):
    """Save chat memory to Firestore."""
    db = firestore.client()
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    doc_ref.set({
        "history": chat_history
    })
    print(f"Saved memory for session {chat_id} for client {client_id} to Firestore.")

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
def get_response(chat_id: str, question: str, client_id: str):
    print(f"\n--- Incoming request ---")
    print(f"client_id: {client_id}")
    print(f"chat_id: {chat_id}")
    print(f"question: {question}")

    config = client_config.get(client_id)
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
        print("📝 Using static system prompt from client_config")

    if "max_chunks" not in config:
        config["max_chunks"] = 5  # your preferred default

    print(f"Using Pinecone index: {config['pinecone_index_name']}")
    print(f"System prompt (first line): {config['system_prompt'].splitlines()[0]}")
    print(f"Using max_chunks: {config['max_chunks']}")

    # Using get_openai_callback context manager for automatic token tracking
    with get_openai_callback() as callback:
        # Call the LangChain QA chain
        qa_chain = get_qa_chain(config)
        result = qa_chain.invoke(
            {"question": question},
            config={"configurable": {"session_id": chat_id}}
        )

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
