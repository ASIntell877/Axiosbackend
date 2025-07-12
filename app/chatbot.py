from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.chains import ConversationalRetrievalChain
from langchain.prompts import PromptTemplate
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from pinecone import Pinecone as PineconeClient
from langchain_pinecone import PineconeVectorStore
from app.redis_utils import get_persona
from openai import OpenAI
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

def get_memory(chat_id: str, client_id: str):
    key = f"{client_id}:{chat_id}"
    if key not in memory_store:
        memory_store[key] = ChatMessageHistory()
    return memory_store[key]



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
        openai_api_key=config["openai_api_key"]
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

# Call open AI, log token usage
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
def get_response(chat_id: str, question: str, client_id: str):
    print(f"\n--- Incoming request ---")
    print(f"client_id: {client_id}")
    print(f"chat_id: {chat_id}")
    print(f"question: {question}")

    config = client_config.get(client_id)
    if not config:
        raise ValueError(f"Unknown client ID: {client_id}")

    config["client_id"] = client_id  # MUST be passed to memory/session logic

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

    # Call your existing chain:
    qa_chain = get_qa_chain(config)
    result = qa_chain.invoke(
        {"question": question},
        config={"configurable": {"session_id": chat_id}}
    )

    # --- New: Call OpenAI directly here to get token usage ---

    # Prepare messages for openai call - adapt as needed for your prompts
    messages = [
        {"role": "system", "content": config["system_prompt"]},
        {"role": "user", "content": question},
    ]

    # Call OpenAI chat completion directly
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        max_tokens=500,  # adjust as needed
    )

    # Extract answer & token usage
    openai_answer = response.choices[0].message.content
    usage = response.usage.total_tokens

    print(f"OpenAI token usage for this request: {usage}")

    # You can choose to:
    # - return your original chain 'result' with added usage info
    # - or replace answer with openai_answer if preferred

    # Here, we'll just add token usage to your original result dict
    result['token_usage'] = usage

    return result