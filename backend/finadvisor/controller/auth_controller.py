from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin import auth, firestore
from pydantic import BaseModel

from firebase_setup import db
from utils.auth_utils import verify_jwt, generate_jwt_token

router = APIRouter()

# JWT Authentication Scheme
security = HTTPBearer()

# User Request Model
class GoogleSignInRequest(BaseModel):
    id_token: str  # Token received from frontend after Google sign-in

@router.post("/auth/google-signin")
async def google_signin(request: GoogleSignInRequest):
    try:
        # Verify Firebase ID token
        decoded_token = auth.verify_id_token(request.id_token)
        uid = decoded_token["uid"]
        name = decoded_token.get("name", "")
        email = decoded_token.get("email", "")
        photo_url = decoded_token.get("picture", "")

        # Store user in Firestore (optional)
        user_ref = db.collection("users").document(uid)
        user_ref.set({
            "uid": uid,
            "name": name,
            "email": email,
            "photo_url": photo_url
        }, merge=True)

        # Generate JWT token
        jwt_token = generate_jwt_token(uid, email)

        return {
            "message": "User authenticated successfully",
            "user": {"name": name, "email": email, "photo_url": photo_url},
            "jwt_token": jwt_token
        }

    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.get("/protected-route")
async def protected_route(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """ Protected route that requires JWT authentication """
    token = credentials.credentials
    user_data = verify_jwt(token)
    return {"message": "Authenticated", "user": user_data}

class RefreshTokenRequest(BaseModel):
    token: str

# Logout Endpoint (Frontend should remove JWT from storage)
@router.post("/auth/refresh-token")
async def refresh_token(request: RefreshTokenRequest):
    try:
        decoded_token = verify_jwt(request.token)
        uid = decoded_token["uid"]
        email = decoded_token["email"]

        # Generate a new token with updated expiration
        new_token = generate_jwt_token(uid, email)

        return {"token": new_token}

    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

@router.post("/auth/logout")
async def logout():
    return {"message": "User logged out. Remove JWT from local storage on frontend."}

@router.get("/sessions")
async def get_sessions(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        # Extract the token from Authorization header
        token = credentials.credentials

        # Verify Firebase JWT token
        user_data = verify_jwt(token)
        user_id = user_data["uid"]  # Extract the authenticated user ID

        # Fetch sessions from Firestore
        sessions_ref = db.collection("users").document(user_id).collection("sessions").order_by("last_active", direction=firestore.Query.DESCENDING)
        sessions = sessions_ref.stream()

        # Convert Firestore documents to JSON response
        session_list = [{"id": doc.id, **doc.to_dict()} for doc in sessions]

        return {"sessions": session_list}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class MessageRequest(BaseModel):
    session_id: str

@router.post("/messages")
async def get_messages(
    request: MessageRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    try:
        # Extract token from Authorization header
        token = credentials.credentials

        # Verify Firebase JWT token
        user_data = verify_jwt(token)
        user_id = user_data["uid"]  # Extract the authenticated user ID

        # Get session_id from request body
        session_id = request.session_id

        # Fetch messages from Firestore
        messages_ref = (
            db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .order_by("timestamp", direction=firestore.Query.ASCENDING)
        )

        messages = messages_ref.stream()

        # Convert Firestore documents to JSON response
        messages_list = [{"id": doc.id, **doc.to_dict()} for doc in messages]

        return {"messages": messages_list}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
