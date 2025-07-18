import httpx
import os

RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET_KEY")
# Minimum score required from reCAPTCHA v3 verification
RECAPTCHA_MIN_SCORE = float(os.getenv("RECAPTCHA_MIN_SCORE", "0.5"))

async def verify_recaptcha(token: str, expected_action: str | None = None) -> bool:
    url = "https://www.google.com/recaptcha/api/siteverify"
    data = {
        "secret": RECAPTCHA_SECRET,
        "response": token,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=data, timeout=5)
            result = resp.json()
        
        print("🔍 reCAPTCHA verification result:", result)  # Log the full response
        
        score = result.get("score", 0)
        print("🔍 reCAPTCHA score:", score)

        if not result.get("success"):
            return False

        if score < RECAPTCHA_MIN_SCORE:
            return False

        if expected_action is not None and result.get("action") != expected_action:
            return False

        return True
    except Exception as e:
        print("❌ Error verifying reCAPTCHA:", str(e))  # Log exceptions
        return False

