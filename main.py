import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, Header, FastAPI, HTTPException, Response, status, Request, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi import Query
from ratelimit import r
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.redis_utils import increment_token_usage
import httpx  # For proxy requests
import import_firebase
from store_chat_firebase import delete_memory
from datetime import datetime, timedelta
from app.redis_utils import get_last_seen, set_last_seen
from app.chatbot import get_response
from app.chatbot import get_memory, save_memory, is_memory_enabled
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from app.redis_utils import get_persona, save_chat_message
from app.redis_utils import get_token_usage
from recaptcha import verify_recaptcha  # Your recaptcha verification function
from ratelimit import check_rate_limit, track_usage

# Load API keys securely from environment variables and configure rate limits
API_KEYS = {
    "maximos": {
        "key": os.getenv("MAXIMOS_API_KEY"),
        "max_requests": 20,       # 20 requests
        "window_seconds": 60,      # per 60 seconds
        "monthly_limit": 1000     # monthly usage limit
    },
    "ordinance": {
        "key": os.getenv("ORDINANCE_API_KEY"),
        "max_requests": 30,
        "window_seconds": 60,
        "monthly_limit": 1000
    },
    "marketingasst": {
        "key": os.getenv("MARKETINGASST_API_KEY"),
        "max_requests": 40,
        "window_seconds": 60,
        "monthly_limit": 1000
    },
    "samuel": {
        "key": os.getenv("SAMUEL_API_KEY"),
        "max_requests": 50,
        "window_seconds": 60,
        "monthly_limit": 1000
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

# Admin endpoint to view daily + monthly usage
@app.get("/admin/usage")
def get_usage(client_id: str = Query(...)):
    api_key_info = API_KEYS.get(client_id)
    if not api_key_info:
        raise HTTPException(status_code=400, detail="Unknown client_id")

    api_key = api_key_info["key"]

    # === DAILY USAGE ===
    today = datetime.utcnow().date()
    dates = [today - timedelta(days=i) for i in range(7)]  # Last 7 days
    daily = {}

    for date in dates:
        key = f"usage:{api_key}:{date.isoformat()}"
        count = r.get(key)
        daily[date.isoformat()] = int(count) if count else 0

    # === MONTHLY USAGE ===
    quota_key = f"quota_usage:{api_key}"
    quota_count = r.get(quota_key)
    quota_ttl = r.ttl(quota_key)

    return {
        "client_id": client_id,
        "daily_usage": daily,
        "monthly_usage": int(quota_count) if quota_count else 0,
        "resets_in_seconds": quota_ttl,
    }
# admin endpoint to view token usage by xpai
@app.get("/admin/token-usage")
def get_token_usage_endpoint(client_id: str = Query(...)):
     # Debugging: Log client_id to verify it's received correctly
    print(f"Received request for client_id: {client_id}")
    api_key_info = API_KEYS.get(client_id)
    if not api_key_info:
        raise HTTPException(status_code=400, detail="Unknown client_id")

    api_key = api_key_info["key"]

    try:
        # Debugging: Log the API key and client before attempting to fetch usage
        print(f"Fetching token usage for api_key: {api_key}")
        usage_data = get_token_usage(client_id)  # Returns dict with detailed usage
    except Exception as e:
        # Debugging: Log any errors during token usage retrieval
        print(f"Error fetching token usage for {client_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch token usage: {str(e)}")

    return {
        "client_id": client_id,
        "token_usage": usage_data  # Full detailed dict: today, monthly, total, per model
    }


# Core chat logic extracted to a reusable function
SESSION_TIMEOUT = timedelta(minutes=30)

async def process_chat(request: ChatRequest, api_key_info: dict):
    client_id = request.client_id
    chat_id   = request.chat_id

    # --- Auto‑expire logic ---
    now = datetime.utcnow()
    last = get_last_seen(client_id, chat_id)
    if last and (now - last) > SESSION_TIMEOUT:
        delete_memory(client_id, chat_id)
    set_last_seen(client_id, chat_id, now)
    # ---------------------------

    try:
        key = api_key_info["key"]
        max_req = api_key_info.get("max_requests", 20)
        window = api_key_info.get("window_seconds", 60)
        monthly_limit = api_key_info.get("monthly_limit")

        # Check per-minute rate limit
        check_rate_limit(key, max_requests=max_req, window_seconds=window)

        # Retrieve or initialize chat history
        if is_memory_enabled(client_id):
            chat_history = get_memory(chat_id, client_id)
        else:
            chat_history = ChatMessageHistory()

        # Call main chatbot logic
        result = get_response(
            chat_id=chat_id,
            question=request.question,
            client_id=client_id,
        )

        # Save updated history if memory is enabled
        if is_memory_enabled(client_id):
            chat_history.add_user_message(request.question)
            chat_history.add_ai_message(result["answer"])
            save_memory(client_id, chat_id, chat_history)

        # Return the response
        return {
            "answer": result["answer"],
            "source_documents": [
                {"source": doc.metadata.get("source","unknown"),
                 "text": doc.page_content[:300]}
                for doc in result.get("source_documents", [])
            ],
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Exception in process_chat: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")



# Internal chat endpoint — expects valid API key header
@app.post("/chat")
async def chat(request: ChatRequest, api_key_info: dict = Depends(verify_api_key)):
    print(f"Processing chat for client_id: {request.client_id}, chat_id: {request.chat_id}") #debug/logging to verify memory behavior is functioning
    return await process_chat(request, api_key_info)

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

    try:
        # Construct the request object from incoming JSON
        chat_request = ChatRequest(**body)

        # Call the extracted process_chat function directly with api_key_info
        response_data = await process_chat(chat_request, api_key_info)

        # Return proper JSON response
        return JSONResponse(content=jsonable_encoder(response_data), status_code=200)

    except Exception as e:
        print(f"Internal proxy error: {e}")
        raise HTTPException(status_code=500, detail="Internal proxy error")
    