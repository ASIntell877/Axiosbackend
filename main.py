import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, Header, FastAPI, HTTPException, Response, status, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx  # For proxy requests

from app.chatbot import get_response
from app.redis_utils import get_persona, save_chat_message
from app.recaptcha import verify_recaptcha  # Your recaptcha verification function

# Load API keys securely from environment variables
API_KEYS = {
    "maximos": os.getenv("MAXIMOS_API_KEY"),
    "ordinance": os.getenv("ORDINANCE_API_KEY"),
    "marketingasst": os.getenv("MARKETINGASST_API_KEY"),
    "samuel": os.getenv("SAMUEL_API_KEY"),
}

# FastAPI app instance
app = FastAPI()

# Allowed frontend origins (adjust as needed)
ALLOWED_ORIGINS = [
    "https://axiosfrontend.vercel.app",
]

# CORS Middleware to allow cross-origin calls from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency to validate x-api-key header on /chat endpoint
def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key not in API_KEYS.values():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )
    return x_api_key

# Request model for /chat and proxy endpoint
class ChatRequest(BaseModel):
    chat_id: str
    client_id: str
    question: str
    recaptcha_token: str

# Get persona info endpoint
@app.get("/persona/{client_id}")
def read_persona(client_id: str):
    prompt = get_persona(client_id)
    return {"client_id": client_id, "persona": prompt}

# Internal chat endpoint — expects valid API key header
@app.post("/chat")
async def chat(request: ChatRequest, api_key: str = Depends(verify_api_key)):
    # Verify reCAPTCHA token from client request
    if not await verify_recaptcha(request.recaptcha_token):
        raise HTTPException(status_code=403, detail="Failed reCAPTCHA verification")

    try:
        print(f"Received chat request: client_id={request.client_id}, chat_id={request.chat_id}, question={request.question}")

        # Call your chatbot logic
        result = get_response(
            chat_id=request.chat_id,
            question=request.question,
            client_id=request.client_id,
        )

        print(f"Response generated: answer preview={result['answer'][:100]}")

        # Save conversation messages to Redis or wherever
        save_chat_message(request.client_id, request.chat_id, "user", request.question)
        save_chat_message(request.client_id, request.chat_id, "assistant", result["answer"])

        # Return response + up to 300 chars of each source document
        return {
            "answer": result["answer"],
            "source_documents": [
                {
                    "source": doc.metadata.get("source", "unknown"),
                    "text": doc.page_content[:300],
                }
                for doc in result.get("source_documents", [])
            ],
        }

    except ValueError as e:
        print(f"ValueError: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Exception: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

# CORS preflight for /chat route
@app.options("/chat")
async def preflight_chat():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": ALLOWED_ORIGINS[0],
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )

# Public proxy endpoint: frontend calls here without API key,
# backend validates recaptcha and injects API key when calling internal /chat
@app.post("/proxy-chat")
async def proxy_chat(request: Request):
    body = await request.json()
    client_id = body.get("client_id")
    recaptcha_token = body.get("recaptcha_token")

    if not client_id or not recaptcha_token:
        raise HTTPException(status_code=400, detail="Missing client_id or recaptcha_token")

    if not await verify_recaptcha(recaptcha_token):
        raise HTTPException(status_code=403, detail="reCAPTCHA verification failed")

    api_key = API_KEYS.get(client_id)
    if not api_key:
        raise HTTPException(status_code=400, detail="Unknown client")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }

    async with httpx.AsyncClient() as client:
        backend_url = os.getenv("SELF_API_BASE_URL", "https://sdcl-backend.onrender.com")
        res = await client.post(f"{backend_url}/chat", headers=headers, json=body)

        return Response(content=res.content, status_code=res.status_code)

