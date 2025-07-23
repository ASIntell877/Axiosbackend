import firebase_admin
from firebase_admin import credentials
import os

private_key = os.getenv("FIREBASE_PRIVATE_KEY")
if private_key is None:
    raise RuntimeError(
        "FIREBASE_PRIVATE_KEY environment variable is not set"
    )


firebase_config = {
    "type": "service_account",
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": private_key.replace(r"\n", "\n"),
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "client_id": os.getenv("FIREBASE_CLIENT_ID", ""),  # Optional, you may not need this
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",  # Optional, defaults are fine
    "token_uri": "https://oauth2.googleapis.com/token",  # Optional, defaults are fine
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",  # Optional, defaults are fine
    "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_X509_CERT_URL")  # Use the environment variable correctly
}

# Initialize the Firebase Admin SDK with the credentials
cred = credentials.Certificate(firebase_config)
firebase_admin.initialize_app(cred)
