from dotenv import load_dotenv
load_dotenv()

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from app.chatbot import get_response

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://axiosfrontend.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    chat_id: str
    client_id: str
    question: str

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

        return {
            "answer": result["answer"],
            "source_documents": [
                {
                    "source": doc.metadata.get("source", "unknown"),
                    "text": doc.page_content[:300]
                } for doc in result.get("source_documents", [])
            ]
        }

    except ValueError as e:
        print(f"ValueError: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Exception: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

# Optional: May not be necessary if CORSMiddleware handles preflight
@app.options("/chat")
async def preflight_chat():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "https://sdclfrontend.vercel.app",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )

