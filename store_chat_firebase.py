import json
import firebase_admin
from firebase_admin import firestore

# Initialize Firebase if not already done
db = firestore.client()

def save_memory(client_id: str, chat_id: str, chat_history: list):
    """Save chat history to Firestore"""
    # Reference to the specific client's session
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    
    # Save chat history as a list of messages
    doc_ref.set({
        "history": chat_history
    })
    print(f"Saved memory for session {chat_id} for client {client_id}")

def get_memory(client_id: str, chat_id: str):
    """Get chat history from Firestore"""
    try:
        doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
        doc = doc_ref.get()
        
        if doc.exists:
            return doc.to_dict()["history"]
        else:
            return []
    except Exception as e:
        print(f"Error retrieving chat history for {client_id}_{chat_id}: {e}")
        return []
    

def delete_memory(client_id: str, chat_id: str):
    """Delete chat history after session ends"""
    doc_ref = db.collection("chat_memory").document(f"{client_id}_{chat_id}")
    doc_ref.delete()
    print(f"Deleted memory for session {chat_id} for client {client_id}")
