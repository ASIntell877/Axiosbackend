import json
import os
import import_firebase
import firebase_admin
from firebase_admin import firestore, credentials
from langchain_community.chat_message_histories.in_memory import ChatMessageHistory
from langchain.schema import messages_from_dict, messages_to_dict

# Ensure Firebase Admin SDK is initialized
if not firebase_admin._apps:
    try:
        # Prefer initialization provided by import_firebase.py
        import import_firebase  # noqa: F401
    except Exception:
        # Fall back to initializing directly using environment variables
        firebase_config = {
            "type": "service_account",
            "project_id": os.getenv("FIREBASE_PROJECT_ID"),
            "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
            "private_key": os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
            "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
            "client_id": os.getenv("FIREBASE_CLIENT_ID", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_X509_CERT_URL"),
        }
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)

# Now that the Admin SDK is initialized, create the Firestore client
db = firestore.client()

def save_memory(client_id: str, chat_id: str, chat_history: ChatMessageHistory):
    """Save chat history to Firestore"""
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    doc_ref.set({
        "history": messages_to_dict(chat_history.messages)
    })
    print(f"Saved memory for session {chat_id} for client {client_id}")

def get_memory(client_id: str, chat_id: str) -> ChatMessageHistory:
    """Get chat history from Firestore"""
    try:
        doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
        doc = doc_ref.get()
        history = ChatMessageHistory()
        if doc.exists:
            stored = doc.to_dict().get("history", [])
            history.messages = messages_from_dict(stored)
        return history
    except Exception as e:
        print(f"Error retrieving chat history for {client_id}_{chat_id}: {e}")
        return ChatMessageHistory()
    

def delete_memory(client_id: str, chat_id: str):
    """Delete chat history after session ends"""
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    doc_ref.delete()
    print(f"Deleted memory for session {chat_id} for client {client_id}")
