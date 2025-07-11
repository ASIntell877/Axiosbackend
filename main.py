import os
from dotenv import load_dotenv
load_dotenv()

from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends, Header, FastAPI, HTTPException, Response, status
from pydantic import BaseModel
from app.chatbot import get_response
from app.redis_utils import get_persona, save_chat_message

API_KEYS = {
    "maximos": os.getenv("MAXIMOS_API_KEY"),
    "ordinance": os.getenv("ORDINANCE_API_KEY"),
    "marketingasst": os.getenv("MARKETINGASST_API_KEY"),
    "samuel": os.getenv("SAMUEL_API_KEY"),
}

def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key not in API_KEYS.values():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )

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
    recaptcha_token: str 

@app.get("/persona/{client_id}")
def read_persona(client_id: str):
    prompt = get_persona(client_id)
    return {"client_id": client_id, "persona": prompt}

@app.post("/chat")
async def chat(request: ChatRequest, api_key: str = Depends(verify_api_key)):
    # ✅ Verify reCAPTCHA before processing
    if not await verify_recaptcha(request.recaptcha_token):
        raise HTTPException(status_code=403, detail="Failed reCAPTCHA verification")
    try:
        print(f"Received chat request: client_id={request.client_id}, chat_id={request.chat_id}, question={request.question}")
        result = get_response(
            chat_id=request.chat_id,
            question=request.question,
            client_id=request.client_id
        )
        print(f"Response generated: answer preview={result['answer'][:100]}")
        save_chat_message(request.client_id, request.chat_id, "user", request.question)
        save_chat_message(request.client_id, request.chat_id, "assistant", result["answer"])

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
            "Access-Control-Allow-Origin": "https://axiosfrontend.vercel.app",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )

