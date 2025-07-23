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
from app.redis_utils import get_last_seen, set_last_seen
from app.chatbot import get_response
from app.chatbot import get_memory, save_firebase_memory, is_memory_enabled
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from app.redis_utils import get_persona, save_chat_message
from app.redis_utils import get_token_usage
from recaptcha import verify_recaptcha  # Your recaptcha verification function
from ratelimit import check_rate_limit, track_usage
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")

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
        "prairiepastorate": {
        "key": os.getenv("PRAPASTORATE_API_KEY"),
        "max_requests": 50,
        "window_seconds": 60,
        "monthly_limit": 1000
    },
}

# FastAPI app instance
app = FastAPI()

# Allowed frontend origins (adjust as needed)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").split(",")

# CORS Middleware to allow cross-origin calls from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# Ensure a provided client_id is known
def validate_client_id(client_id: str) -> None:
    if client_id not in API_KEYS:
        raise HTTPException(status_code=400, detail="Unknown client")

# Dependency to validate x-api-key header on /chat endpoint
def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key == ADMIN_API_KEY:
        return {"client": "admin", "key": x_api_key}

    # 2) check regular clients
    for client, info in API_KEYS.items():
        if info["key"] == x_api_key:
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
def read_persona(
    client_id: str,
    api_key_info: dict = Depends(verify_api_key),
):
    validate_client_id(client_id)
    if api_key_info["client"] not in ("admin", client_id):
        raise HTTPException(403, "Forbidden")
    prompt = get_persona(client_id)
    return {"client_id": client_id, "persona": prompt}

# Admin endpoint to view daily + monthly usage
@app.get("/admin/usage")
def get_usage(
    client_id: str = Query(...),
    api_key_info: dict = Depends(verify_api_key),
):
    """Return daily and monthly usage for the given client_id.

    Raises a 400 error if the client_id does not exist in ``API_KEYS``.
    """
    validate_client_id(client_id)
    if api_key_info["client"] != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")

    info = API_KEYS.get(client_id)
    if info is None:
        raise HTTPException(status_code=400, detail="Unknown client_id")
    api_key = info["key"]

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
def get_token_usage_endpoint(
    client_id: str = Query(...),
    api_key_info: dict = Depends(verify_api_key),
):
    validate_client_id(client_id)
    if api_key_info["client"] != "admin":
        raise HTTPException(403, "Forbidden")

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

@app.get("/history")
async def get_history(
     client_id: str = Query(..., description="Which client/pastorate"),
     chat_id:   str = Query(..., description="The chat session ID"),
     api_key_info: dict = Depends(verify_api_key),
):
    """
    Return the saved chat messages (as {role, text}) for this client_id + chat_id.
    """
    validate_client_id(client_id)
    caller = api_key_info["client"]

    # 1) Admins can fetch any history
    if caller == "admin":
        pass

    # 2) Clients may only fetch their own history
    elif caller == client_id:
        pass

    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: you can only access your own history"
        )

    # Safe to read memory
    if not is_memory_enabled(client_id):
        return {"history": []}

    # Retrieve your LangChain ChatMessageHistory
    history_obj = get_memory(chat_id, client_id)

    # Convert each LangChain BaseMessage into {role, text}
    msgs = [
        {"role": "assistant" if m.type=="ai" else "user", "text": m.content}
        for m in history_obj.messages
    ]
    return {"history": msgs}

async def process_chat(request: ChatRequest, api_key_info: dict):
    client_id = request.client_id
    chat_id   = request.chat_id

    validate_client_id(client_id)

    # --- Auto‑expire logic ---
    now = datetime.utcnow()
    last = get_last_seen(client_id, chat_id)
    if last is None:
        delete_memory(client_id, chat_id)
    elif (now - last) > SESSION_TIMEOUT:
        delete_memory(client_id, chat_id)
    set_last_seen(client_id, chat_id, now)
    # ---------------------------

    try:
        key = api_key_info["key"]
        max_req = api_key_info.get("max_requests", 20)
        window = api_key_info.get("window_seconds", 60)
        monthly_limit = api_key_info.get("monthly_limit")

        # Rate‑limit checks 
        check_rate_limit(
            key,
            max_requests=max_req,
            window_seconds=window,
        )

        # Monthly quota enforcement
        used = int(r.get(f"quota_usage:{key}") or 0)
        if monthly_limit and used >= monthly_limit:
            raise HTTPException(
                status_code=429,
                detail="Monthly quota exceeded"
            )

        #  Record this request against the quota
        track_usage(key)

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
            save_firebase_memory(client_id, chat_id, chat_history)

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
    validate_client_id(request.client_id)
    #admins can't impersonate clients
    if api_key_info["client"] == "admin":
        raise HTTPException(403, "Admins may not call /chat")
    # Clients only hit their own client_id
    if api_key_info["client"] != request.client_id:
        raise HTTPException(403, "Forbidden: key does not match client_id")
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
@limiter.limit("30/minute")
@app.post("/proxy-chat")
async def proxy_chat(request: Request):
    body = await request.json()
    client_id = body.get("client_id")
    recaptcha_token = body.get("recaptcha_token")

    if not client_id or not recaptcha_token:
        raise HTTPException(status_code=400, detail="Missing client_id or recaptcha_token")

    if not await verify_recaptcha(recaptcha_token):
        raise HTTPException(status_code=403, detail="reCAPTCHA verification failed")

    info = API_KEYS.get(client_id)
    if not info:
        raise HTTPException(status_code=400, detail="Unknown client")
    
    api_key_info = {"client": client_id, **info}
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
    