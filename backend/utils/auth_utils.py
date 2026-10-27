from firebase_admin import auth
from fastapi import HTTPException
import datetime
import os

import jwt

from config.settings import settings

SECRET_KEY = settings.JWT_SECRET

def verify_token(token: str):
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token["uid"]
    except auth.ExpiredIdTokenError:
        print("Error: Token has expired")
    except auth.RevokedIdTokenError:
        print("Error: Token has been revoked")
    except auth.InvalidIdTokenError:
        print("Error: Invalid ID token")
    except Exception as e:
        print(f"Unexpected error")

# Middleware to verify JWT on protected routes
def verify_jwt(token: str):
    try:
        decoded_token = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return decoded_token
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Generate JWT Token
def generate_jwt_token(uid: str, email: str):
    payload = {
        "uid": uid,
        "email": email,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7),  # Token expires in 7 days
        "iat": datetime.datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_token_email(token: str):
    try:
        # Decode the token using Firebase Admin SDK
        decoded_token = auth.verify_id_token(token)

        # Extract user ID and email
        user_id = decoded_token.get("uid")
        email = decoded_token.get("email")

        if not user_id or not email:
            raise HTTPException(status_code=400, detail="Invalid token payload. Missing user ID or email.")

        return {"user_id": user_id, "email": email}

    except auth.ExpiredIdTokenError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except auth.RevokedIdTokenError:
        raise HTTPException(status_code=401, detail="Token has been revoked.")
    except auth.InvalidIdTokenError:
        raise HTTPException(status_code=401, detail="Invalid ID token.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")