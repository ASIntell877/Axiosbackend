# === this loads the environment so that keys and secrets referenced in client_config will know where to look
from dotenv import load_dotenv
load_dotenv()

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from app.chatbot import get_response  # your working logic

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://sdclfrontend.vercel.app/"],  # React dev server origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Define request body ===
class ChatRequest(BaseModel):
    chat_id: str
    client_id: str
    question: str

# === Define response format (optional) ===
class ChatResponse(BaseModel):
    answer: str
    source_docs: list = []  # Optional: return source doc metadata

# === POST endpoint ===
@app.post("/chat")
async def chat(request: ChatRequest):
    try:
        print(f"Received chat request: client_id={request.client_id}, chat_id={request.chat_id}, question={request.question}")
        result = get_response(
            chat_id=request.chat_id,
            question=request.question,
            client_id=request.client_id
        )
        print(f"Response generated: answer preview={result['answer'][:100]}")

        # Return the structured response
        return {
            "answer": result["answer"],
            "source_documents": [
                {
                    "source": doc.metadata.get("source", "unknown"),
                    "text": doc.page_content[:300]  # Optional: truncate
                } for doc in result.get("source_documents", [])
            ]
        }

    except ValueError as e:
        print(f"ValueError: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Exception: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
        # === responds to browsers CORS prelight check ===
@app.options("/chat")
async def preflight_chat():
    return {"message": "CORS preflight passed"}
