import httpx
import os

RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET_KEY")

async def verify_recaptcha(token: str) -> bool:
    url = "https://www.google.com/recaptcha/api/siteverify"
    data = {
        "secret": RECAPTCHA_SECRET,
        "response": token,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=data, timeout=5)
            result = await resp.json()
        
        print("🔍 reCAPTCHA verification result:", result)  # Log the full response
        
        return result.get("success", False)
    except Exception as e:
        print("❌ Error verifying reCAPTCHA:", str(e))  # Log exceptions
        return False

