import firebase_admin
from firebase_admin import credentials, firestore, auth
import os
import json

_db = None

def _get_firebase_credentials():
    """Load Firebase credentials from environment variable or file."""
    # Try environment variable first (for production)
    firebase_creds_json = os.environ.get("FIREBASE_CREDENTIALS")
    if firebase_creds_json:
        try:
            creds_dict = json.loads(firebase_creds_json)
            return credentials.Certificate(creds_dict)
        except json.JSONDecodeError:
            pass

    # Fall back to file (for local development)
    if os.path.exists("firebase_key.json"):
        return credentials.Certificate("firebase_key.json")

    return None

def _init_firebase():
    global _db
    if _db is None:
        cred = _get_firebase_credentials()
        if cred and not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        if firebase_admin._apps:
            _db = firestore.client()
    return _db

def get_db():
    return _init_firebase()

# Keep backward compatibility
class _LazyDB:
    def __getattr__(self, name):
        return getattr(get_db(), name)

db = _LazyDB()

# def generate_custom_token(uid):
#     try:
#         # Generate a custom token for the given UID
#         token = auth.create_custom_token(uid)
#         print("Generated Custom Token:", token.decode("utf-8"))
#         return token
#     except Exception as e:
#         print("Error generating custom token:", e)
#         return None
