# Axiosbackend

This service provides chat capabilities for multiple clients via FastAPI. Chat history can be stored in Firestore or kept in-memory depending on client configuration.

## Session Expiration

Chat sessions automatically expire 30 minutes after the last message. When a session expires the corresponding chat history is cleared from memory and, if applicable, from Firestore.