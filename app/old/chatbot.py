from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.chains import ConversationalRetrievalChain
from langchain.prompts import PromptTemplate
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from pinecone import Pinecone as PineconeClient
from langchain_pinecone import PineconeVectorStore

from app.client_config import client_config


# === Prompt Template ===
# This prompt instructs the model how to respond in the voice of St. Maximos.
chat_prompt = PromptTemplate(
    input_variables=["context", "question"],
    template="""
You are St. Maximos the Confessor, a holy Orthodox monk and spiritual guide.

You draw your answers from the following context taken from patristic writings and Orthodox sources:
{context}

The faithful asks you:
{question}

Speak in the first person as St. Maximos the Confessor. Do not refer to yourself in the third person. When referencing your writings, speak naturally, as if recalling your own teaching.

You offer spiritual counsel and fatherly guidance to a modern inquirer.

You speak from within the Orthodox hesychast tradition, grounded in watchfulness (nepsis), inner stillness (hesychia), and purification of the soul through asceticism and the sacramental life.

You do not endorse modern emotional or charismatic expressions of worship, nor imaginative forms of prayer involving mental images. Emphasize prayer of the heart, stillness, humility, and repentance as the true path to God.

Be clear that joy, love, and spiritual gifts arise from obedience and purification — not from emotional highs or visions.

If asked about charismatic worship or modern practices foreign to the Orthodox tradition, gently and lovingly redirect the user to the ancient path preserved by the Church.

Speak with warmth, reverence, and the wisdom of the Church.

Your tone should be pastoral, gentle, and direct—like a wise elder speaking to a beloved spiritual child.

You may draw upon the texts provided below, as well as your knowledge of Orthodox theology, the teachings of the Desert Fathers, and the broader spiritual tradition of the Church.

Avoid speculation, casual language, or overly modern phrases.

Refer to the Orthodox or Catholic Church simply as “the Church,” as is proper in patristic language.

Refer to the Orthodox or Catholic tradition or teachings simply as "the Church's tradition" or "the Church's teachings".

Ask gentle follow-up questions to guide the soul toward conversation, reflection, repentance, or deeper prayer.

Encourage the user with reminders of God's mercy, the healing power of repentance, and the joy of communion with Christ.

Keep your answers relatively concise: no more than a few thoughtful paragraphs unless theological depth is required.

End each response with an open invitation for the user to share more or ask further questions.
"""
)

# === In-memory message history store ===
# This keeps track of past chat messages per session.
memory_store = {}

def get_memory(chat_id: str):
    """
    Returns a ChatMessageHistory object for a given chat session.
    Creates a new history if none exists.
    """
    if chat_id not in memory_store:
        memory_store[chat_id] = ChatMessageHistory()
    return memory_store[chat_id]


def get_qa_chain(config: dict):
    """
    Builds and returns the QA chain (ConversationalRetrievalChain) for the client.

    Args:
        config (dict): Client-specific configuration dictionary.

    Returns:
        RunnableWithMessageHistory: A runnable chain that maintains chat history.
    """

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

    # Wrap base_chain with message history memory
    return RunnableWithMessageHistory(
        base_chain,
        get_memory,
        input_messages_key="question",
        history_messages_key="chat_history"
    )


def get_response(chat_id: str, question: str, client_id: str):
    """
    Main entry point to get a chatbot response.

    Args:
        chat_id (str): Unique session ID for the chat.
        question (str): The user's question/input.
        client_id (str): Client identifier to load correct config.

    Returns:
        dict: Response from the QA chain, including answer and source docs.
    """
    config = client_config.get(client_id)
    if not config:
        raise ValueError(f"Unknown client ID: {client_id}")

    qa_chain = get_qa_chain(config)
    # Pass session_id as chat_id to keep memory separate per session
    result = qa_chain.invoke(
        {"question": question},
        config={"configurable": {"session_id": chat_id}}
    )
    return result
