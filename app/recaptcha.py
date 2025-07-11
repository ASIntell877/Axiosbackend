import httpx
import os

RECAPTCHA_SECRET = os.getenv("GOOGLE_RECAPTCHA_SECRET")

async def verify_recaptcha(token: str) -> bool:
    url = "https://www.google.com/recaptcha/api/siteverify"
    data = {
        "secret": RECAPTCHA_SECRET,
        "response": token,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=data)
        result = resp.json()
    return result.get("success", False)
