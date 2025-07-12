import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, Header, FastAPI, HTTPException, Response, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import httpx  # For proxy requests

from app.chatbot import get_response
from app.redis_utils import get_persona, save_chat_message
from recaptcha import verify_recaptcha  # Your recaptcha verification function
from ratelimit import check_rate_limit, track_usage

# Load API keys securely from environment variables and configure rate limits
API_KEYS = {
    "maximos": {
        "key": os.getenv("MAXIMOS_API_KEY"),
        "max_requests": 20,       # 20 requests
        "window_seconds": 60      # per 60 seconds
    },
    "ordinance": {
        "key": os.getenv("ORDINANCE_API_KEY"),
        "max_requests": 30,
        "window_seconds": 60
    },
    "marketingasst": {
        "key": os.getenv("MARKETINGASST_API_KEY"),
        "max_requests": 40,
        "window_seconds": 60
    },
    "samuel": {
        "key": os.getenv("SAMUEL_API_KEY"),
        "max_requests": 50,
        "window_seconds": 60
    },
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
    for client, info in API_KEYS.items():
        if info["key"] == x_api_key:
            # Return the whole info dict plus client label for later use
            return {"client": client, **info}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API Key",
    )

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
async def chat(request: ChatRequest, api_key_info: dict = Depends(verify_api_key)):
    try:
        key = api_key_info["key"]
        max_req = api_key_info.get("max_requests", 20)
        window = api_key_info.get("window_seconds", 60)

        # Check rate limit with the correct key and limits
        check_rate_limit(key, max_requests=max_req, window_seconds=window)
        track_usage(key)

        # Rest is your existing logic:
        result = get_response(
            chat_id=request.chat_id,
            question=request.question,
            client_id=request.client_id,
        )

        print(f"Received chat request: client_id={request.client_id}, chat_id={request.chat_id}, question={request.question}")
        print(f"Response generated: answer preview={result['answer'][:100]}")

        save_chat_message(request.client_id, request.chat_id, "user", request.question)
        save_chat_message(request.client_id, request.chat_id, "assistant", result["answer"])

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

    except HTTPException as he:
        # Re-raise HTTP errors like rate limiting
        raise he
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

    api_key_info = API_KEYS.get(client_id)
    if not api_key_info:
        raise HTTPException(status_code=400, detail="Unknown client")

    api_key = api_key_info["key"]  # <-- Extract the actual key string here

    try:
        # Construct the request object from incoming JSON
        chat_request = ChatRequest(**body)

        # ✅ Call internal `chat()` function with full api_key_info dict
        # Since your chat expects dict with limits, pass the whole info
        response_data = await chat(chat_request, api_key=api_key_info)

        # ✅ Return proper JSON response
        return JSONResponse(content=jsonable_encoder(response_data), status_code=200)

    except Exception as e:
        print(f"Internal proxy error: {e}")
        raise HTTPException(status_code=500, detail="Internal proxy error")

