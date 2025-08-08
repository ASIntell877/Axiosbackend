import os
import logging
from dotenv import load_dotenv

load_dotenv()

from fastapi import (
    Depends,
    Header,
    FastAPI,
    HTTPException,
    Response,
    status,
    Request,
    APIRouter,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi import Query
from app.redis_utils import r
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.redis_utils import increment_token_usage
from typing import Literal
import httpx  # For proxy requests
from app.redis_memory import delete_memory
from app.redis_utils import get_last_seen, set_last_seen
from app.chatbot import get_response
from app.chatbot import get_memory, save_redis_memory, is_memory_enabled
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from app.redis_utils import (
    get_persona,
    save_chat_message,
    get_token_usage,
    get_client_config,
    get_all_client_configs,
    record_feedback_vote,
    append_feedback_event,
)
from recaptcha import verify_recaptcha  # Your recaptcha verification function
from ratelimit import check_rate_limit, track_usage
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")


# Retrieve client configuration from Redis with fallback
async def get_client_by_api_key(api_key: str):
    configs = await get_all_client_configs()
    for client_id, config in configs.items():
        if config.get("key") == api_key:
            return client_id, config
    return None, None


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
async def validate_client_id(client_id: str) -> None:
    cfg = await get_client_config(client_id)
    if cfg is None:
        raise HTTPException(status_code=400, detail="Unknown client")
    

# Dependency to validate x-api-key header on /chat endpoint
async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key == ADMIN_API_KEY:
        return {"client": "admin", "key": x_api_key}

    client_id, config = await get_client_by_api_key(x_api_key)
    if client_id and config:
        return {
            "client": client_id,
            "key": x_api_key,
            "max_requests": config.get("max_requests", 20),
            "window_seconds": config.get("window_seconds", 60),
            "monthly_limit": config.get("monthly_limit", 1000),
        }

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


class FeedbackRequest(BaseModel):
    client_id: str
    message_id: str
    user_id: str
    vote: Literal["up", "down"]


# Get persona info endpoint
@app.get("/persona/{client_id}")
async def read_persona(
    client_id: str,
    api_key_info: dict = Depends(verify_api_key),
):
    await validate_client_id(client_id)
    if api_key_info["client"] not in ("admin", client_id):
        raise HTTPException(403, "Forbidden")
    prompt = await get_persona(client_id)
    return {"client_id": client_id, "persona": prompt}


# Admin endpoint to view daily + monthly usage
@app.get("/admin/usage")
async def get_usage(
    client_id: str = Query(...),
    api_key_info: dict = Depends(verify_api_key),
):
    """Return daily and monthly usage for the given client_id.

    Raises a 400 error if the client_id does not exist in ``API_KEYS``.
    """
    await validate_client_id(client_id)
    if api_key_info["client"] != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")

    info = await get_client_config(client_id)
    if info is None:
        raise HTTPException(status_code=400, detail="Unknown client_id")
    if not info.get("allow_proxy_chat", False): #checks that client config is set to allow proxy chat-added security
        raise HTTPException(
            status_code=403,
            detail="This client is not authorized to use the proxy-chat endpoint."
        )
    api_key = info["key"]

    # === DAILY USAGE ===
    today = datetime.utcnow().date()
    dates = [today - timedelta(days=i) for i in range(7)]  # Last 7 days
    daily = {}

    for date in dates:
        key = f"usage:{api_key}:{date.isoformat()}"
        count = await r.get(key)
        daily[date.isoformat()] = int(count) if count else 0

    # === MONTHLY USAGE ===
    quota_key = f"quota_usage:{api_key}"
    quota_count = await r.get(quota_key)
    quota_ttl = await r.ttl(quota_key)

    return {
        "client_id": client_id,
        "daily_usage": daily,
        "monthly_usage": int(quota_count) if quota_count else 0,
        "resets_in_seconds": quota_ttl,
    }


# admin endpoint to view token usage by xpai
@app.get("/admin/token-usage")
async def get_token_usage_endpoint(
    client_id: str = Query(...),
    api_key_info: dict = Depends(verify_api_key),
):
    await validate_client_id(client_id)
    if api_key_info["client"] != "admin":
        raise HTTPException(403, "Forbidden")

    api_key = api_key_info["key"]

    try:
        # Debugging: Log the API key and client before attempting to fetch usage
        print(f"Fetching token usage for api_key: {api_key}")
        usage_data = await get_token_usage(client_id)   # Returns dict with detailed usage
    except Exception as e:
        # Debugging: Log any errors during token usage retrieval
        print(f"Error fetching token usage for {client_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch token usage: {str(e)}"
        )

    return {
        "client_id": client_id,
        "token_usage": usage_data,  # Full detailed dict: today, monthly, total, per model
    }


# Core chat logic extracted to a reusable function
SESSION_TIMEOUT = timedelta(minutes=30)


@app.get("/history")
async def get_history(
    client_id: str = Query(..., description="Which client/pastorate"),
    chat_id: str = Query(..., description="The chat session ID"),
    api_key_info: dict = Depends(verify_api_key),
):
    """
    Return the saved chat messages (as {role, text}) for this client_id + chat_id.
    """
    await validate_client_id(client_id)
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
            detail="Forbidden: you can only access your own history",
        )

    # Safe to read memory
    if not await is_memory_enabled(client_id):
        return {"history": []}

    # Retrieve your LangChain ChatMessageHistory
    history_obj = await get_memory(chat_id, client_id)

    # Convert each LangChain BaseMessage into {role, text}
    msgs = [
        {"role": "assistant" if m.type == "ai" else "user", "text": m.content}
        for m in history_obj.messages
    ]
    return {"history": msgs}


async def process_chat(request: ChatRequest, api_key_info: dict):
    client_id = request.client_id
    chat_id = request.chat_id

    await validate_client_id(client_id)

    # ---Check whether gpt fallback is allowed for client, default to false for strict indexing only
    client_settings = await get_client_config(client_id) or {}
    allow_fallback = client_settings.get("allow_gpt_fallback", False)
    print(
        f"[Chat] client_id: {client_id} | allow_fallback: {allow_fallback}"
    )  # log whether fallback allowed

    # --- Auto‑expire logic ---
    now = datetime.utcnow()
    last = await get_last_seen(client_id, chat_id)
    if last is None:
        await delete_memory(client_id, chat_id)
    elif (now - last) > SESSION_TIMEOUT:
        await delete_memory(client_id, chat_id)
    await set_last_seen(client_id, chat_id, now)
    # ---------------------------

    try:
        key = api_key_info["key"]
        max_req = api_key_info.get("max_requests", 20)
        window = api_key_info.get("window_seconds", 60)
        monthly_limit = api_key_info.get("monthly_limit")

        # Rate‑limit checks
        await check_rate_limit(
            key,
            max_requests=max_req,
            window_seconds=window,
        )

        # Monthly quota enforcement
        used = int(await r.get(f"quota_usage:{key}") or 0)
        if monthly_limit and used >= monthly_limit:
            raise HTTPException(status_code=429, detail="Monthly quota exceeded")

        #  Record this request against the quota
        await track_usage(key)

        # Retrieve or initialize chat history
        if await is_memory_enabled(client_id):
            chat_history = await get_memory(chat_id, client_id)
        else:
            chat_history = ChatMessageHistory()

        # Call main chatbot logic
        result = await get_response(
            chat_id=chat_id,
            question=request.question,
            client_id=client_id,
            allow_fallback=allow_fallback,  # GPT fallback passed here
        )

        # Save updated history if memory is enabled
        if await is_memory_enabled(client_id):
            chat_history.add_user_message(request.question)
            chat_history.add_ai_message(result["answer"])
            await save_redis_memory(client_id, chat_id, chat_history)

        # Return the response
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
        raise he
    except Exception as e:
        print(f"Exception in process_chat: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")


# Internal chat endpoint — expects valid API key header
@app.post("/chat")
async def chat(request: ChatRequest, api_key_info: dict = Depends(verify_api_key)):
    await validate_client_id(request.client_id)
    # admins can't impersonate clients
    if api_key_info["client"] == "admin":
        raise HTTPException(403, "Admins may not call /chat")
    # Clients only hit their own client_id
    if api_key_info["client"] != request.client_id:
        raise HTTPException(403, "Forbidden: key does not match client_id")
    print(
        f"Processing chat for client_id: {request.client_id}, chat_id: {request.chat_id}"
    )  # debug/logging to verify memory behavior is functioning

    
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


# Proxy endpoint - public IP limiter - recaptcha verifications
@limiter.limit("30/minute")
@app.post("/proxy-chat")
async def proxy_chat(request: Request):
    body = await request.json()
    client_id = body.get("client_id")
    recaptcha_token = body.get("recaptcha_token")

    if not client_id or not recaptcha_token:
        raise HTTPException(
            status_code=400, detail="Missing client_id or recaptcha_token"
        )

    if not await verify_recaptcha(recaptcha_token):
        raise HTTPException(status_code=403, detail="reCAPTCHA verification failed")

    info = await get_client_config(client_id)
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


@app.post("/feedback")
async def submit_feedback(
    req: FeedbackRequest, api_key_info: dict = Depends(verify_api_key)
):
    """Record user feedback for a specific message.

    Ensures that each user may vote once per message using ``HSETNX`` and logs the
    event to a Redis stream for later analytics.
    """

    await validate_client_id(req.client_id)
    if api_key_info["client"] != req.client_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    cfg = await get_client_config(req.client_id)
    if not cfg or not cfg.get("enable_feedback", True):
        raise HTTPException(status_code=403, detail="Feedback disabled")

    vote_recorded = await record_feedback_vote(
        req.client_id, req.message_id, req.user_id, req.vote
    )
    if not vote_recorded:
        raise HTTPException(status_code=409, detail="User already voted")

    await append_feedback_event(
        req.client_id, req.message_id, req.user_id, req.vote
    )
    print(f"[FEEDBACK_RECORDED] client={req.client_id} message_id={req.message_id} user_id={req.user_id} vote={req.vote}")

    return {"status": "recorded"}