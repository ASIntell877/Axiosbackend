# Axiosbackend

This service provides chat capabilities for multiple clients via FastAPI. Chat history can be stored in Firestore or kept in-memory depending on client configuration.

## Session Expiration

Chat sessions automatically expire 30 minutes after the last message. When a session expires the corresponding chat history is cleared from memory and, if applicable, from Firestore.

## Local Testing

A lightweight test server is provided in `test_app.py` for local development. It exposes a simplified `/chat` endpoint without rate limits or reCAPTCHA checks.

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn test_app:app --reload
```