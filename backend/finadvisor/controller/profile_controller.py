from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from utils.auth_utils import verify_token
from finadvisor.services.profile_service import save_profile, get_financial_data, update_financial_data

import firebase_admin
from firebase_admin import credentials, firestore, auth
import os
import json

# Load Firebase credentials from environment variable or file
def get_firebase_credentials():
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

    # Return None if no credentials found
    return None

cred = get_firebase_credentials()

# Initialize Firebase Admin SDK
if not firebase_admin._apps and cred:
    firebase_admin.initialize_app(cred)

# Firestore client
db = firestore.client()

router = APIRouter()

# Define the request body schema
class ProfileRequest(BaseModel):
    token: str
    name: str
    email: str

@router.post("/profile")
def save_user_profile(profile: ProfileRequest):
    """
    Save or update a user profile.
    """
    try:
        # Validate token and extract user_id
        user_id = verify_token(profile.token)
        response = save_profile(user_id, profile.name, profile.email)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/profile")
def get_user_profile(token: str):
    """
    Retrieve a user profile.
    """
    try:
        # Validate token and extract user_id
        user_id = verify_token(token)
        response = get_profile(user_id)
        if "error" in response:
            raise HTTPException(status_code=404, detail=response["error"])
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/dummy")
def func():
    return "dummy"