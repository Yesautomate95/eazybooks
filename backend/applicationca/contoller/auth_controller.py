from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin import auth, firestore
from pydantic import BaseModel, EmailStr
import bcrypt
import logging
from uuid import uuid4

from firebase_setup import db
from utils.auth_utils import verify_jwt, generate_jwt_token
from applicationca.database.database_update import add_new_user
from applicationca.database.database_retrieval import create_connection
from applicationca.aws.services.HelperService import get_cookies
from applicationca.rbac import audit as rbac_audit
from applicationca.rbac.bootstrap import assign_default_role


def _request_metadata(request: Request | None) -> dict:
    """Capture ip + user-agent for audit metadata (best-effort)."""
    if request is None:
        return {}
    try:
        return {
            "ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
        }
    except Exception:
        return {}

router = APIRouter()
logger = logging.getLogger(__name__)

# JWT Authentication Scheme
security = HTTPBearer()

# Request Models
class GoogleSignInRequest(BaseModel):
    id_token: str

class EmailSignUpRequest(BaseModel):
    email: EmailStr
    password: str
    name: str = ""

class EmailSignInRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/auth/email-signup")
async def email_signup(request: EmailSignUpRequest, http_request: Request = None):
    """Sign up a new user with email and password."""
    logger.info(f"[Auth] Email signup request for: {request.email}")
    try:
        connection = create_connection()
        if not connection:
            logger.error("[Auth] Database connection failed")
            raise HTTPException(status_code=500, detail="Database connection failed")

        try:
            cursor = connection.cursor()

            # Check if email already exists
            cursor.execute("SELECT user_id FROM USERS WHERE email = %s", (request.email,))
            existing = cursor.fetchone()
            if existing:
                raise HTTPException(status_code=409, detail="An account with this email already exists. Please sign in.")

            # Hash password
            password_hash = bcrypt.hashpw(request.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

            # Generate user ID
            uid = str(uuid4())
            name = request.name or request.email.split('@')[0]

            # Insert user with email and password
            cursor.execute(
                "INSERT INTO USERS (user_id, username, email, password_hash) VALUES (%s, %s, %s, %s)",
                (uid, name, request.email, password_hash)
            )
            connection.commit()

            # Grant the default client role so the new user can actually use
            # the product. Defensive: a role-assignment hiccup must not fail
            # signup — bootstrap backfills role-less users on next restart.
            try:
                assign_default_role(uid)
            except Exception as role_exc:
                logger.error(f"[Auth] default role assignment failed for {uid}: {role_exc}")

            # Generate JWT
            jwt_token = generate_jwt_token(uid, request.email)

            rbac_audit.record(
                action="auth.signup",
                actor_user_id=uid,
                target_type="user",
                target_id=uid,
                new_value={"email": request.email, "method": "email"},
                metadata=_request_metadata(http_request),
            )

            return {
                "message": "Account created successfully",
                "user": {"name": name, "email": request.email},
                "jwt_token": jwt_token,
            }

        finally:
            cursor.close()
            connection.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Email signup error: {e}")
        rbac_audit.record(
            action="auth.signup.failure",
            new_value={"email": request.email, "error": str(e)[:200]},
            metadata=_request_metadata(http_request),
        )
        raise HTTPException(status_code=500, detail="Failed to create account. Please try again.")


@router.post("/auth/email-signin")
async def email_signin(request: EmailSignInRequest, http_request: Request = None):
    """Sign in with email and password."""
    logger.info(f"[Auth] Email signin request for: {request.email}")
    try:
        connection = create_connection()
        if not connection:
            logger.error("[Auth] Database connection failed")
            raise HTTPException(status_code=500, detail="Database connection failed")

        try:
            cursor = connection.cursor()

            # Find user by email
            cursor.execute("SELECT user_id, username, email, password_hash FROM USERS WHERE email = %s", (request.email,))
            user = cursor.fetchone()

            if not user:
                raise HTTPException(status_code=401, detail="No account found with this email. Please sign up first.")

            uid, name, email, password_hash = user

            if not password_hash:
                raise HTTPException(status_code=401, detail="This account uses Google sign-in. Please use the Google button.")

            # Verify password
            if not bcrypt.checkpw(request.password.encode('utf-8'), password_hash.encode('utf-8')):
                raise HTTPException(status_code=401, detail="Incorrect password. Please try again.")

            # Generate JWT
            jwt_token = generate_jwt_token(uid, email)

            rbac_audit.record(
                action="auth.login.success",
                actor_user_id=uid,
                target_type="user",
                target_id=uid,
                new_value={"method": "email"},
                metadata=_request_metadata(http_request),
            )

            return {
                "message": "Signed in successfully",
                "user": {"name": name, "email": email},
                "jwt_token": jwt_token,
            }

        finally:
            cursor.close()
            connection.close()

    except HTTPException as he:
        # Failed login (wrong password, no account) — log without actor.
        rbac_audit.record(
            action="auth.login.failure",
            new_value={"email": request.email, "reason": he.detail if isinstance(he.detail, str) else "denied"},
            metadata=_request_metadata(http_request),
        )
        raise
    except Exception as e:
        logger.error(f"Email signin error: {e}")
        raise HTTPException(status_code=500, detail="Sign in failed. Please try again.")


@router.post("/auth/google-signin")
async def google_signin(request: GoogleSignInRequest, http_request: Request = None):
    logger.info("[Auth] Google sign-in request received")
    try:
        # Verify Firebase ID token (assumed to be already implemented)
        logger.info("[Auth] Verifying Firebase ID token...")
        decoded_token = auth.verify_id_token(request.id_token, clock_skew_seconds=60)
        uid = decoded_token["uid"]
        name = decoded_token.get("name", "")
        email = decoded_token.get("email", "")
        photo_url = decoded_token.get("picture", "")
        logger.info(f"[Auth] Token verified for user: {email} (uid: {uid})")

        # Store user in Firestore (optional) and in PostgreSQL if needed
        logger.info("[Auth] Storing user in Firestore...")
        user_ref = db.collection("users").document(uid)
        user_ref.set({
            "uid": uid,
            "name": name,
            "email": email,
            "photo_url": photo_url
        }, merge=True)
        logger.info("[Auth] Adding user to PostgreSQL...")
        add_new_user(username=name, user_id=uid, email=email)
        # Grant the default client role (idempotent — safe for returning users
        # too). Defensive so a hiccup never blocks Google sign-in.
        try:
            assign_default_role(uid)
        except Exception as role_exc:
            logger.error(f"[Auth] default role assignment failed for {uid}: {role_exc}")
        logger.info("[Auth] Generating JWT token...")
        jwt_token = generate_jwt_token(uid, email)
        logger.info(f"[Auth] Google sign-in successful for: {email}")

        rbac_audit.record(
            action="auth.login.success",
            actor_user_id=uid,
            target_type="user",
            target_id=uid,
            new_value={"method": "google"},
            metadata=_request_metadata(http_request),
        )

        return {
            "message": "User authenticated successfully",
            "user": {"name": name, "email": email, "photo_url": photo_url},
            "jwt_token": jwt_token,
            "signed_cookies": get_cookies(uid)  # Only needed if the client must set them manually
        }

    except Exception as e:
        logger.error(f"[Auth] Google sign-in error: {str(e)}")
        rbac_audit.record(
            action="auth.login.failure",
            new_value={"method": "google", "error": str(e)[:200]},
            metadata=_request_metadata(http_request),
        )
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

        return {
            "token": new_token,
            "signed_cookies": get_cookies(uid)
        }

    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

@router.post("/auth/logout")
async def logout(http_request: Request = None, credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False))):
    # Best-effort: capture the user so we can attribute the audit row.
    actor_user_id = None
    if credentials is not None:
        try:
            actor_user_id = verify_jwt(credentials.credentials).get("uid")
        except Exception:
            pass
    rbac_audit.record(
        action="auth.logout",
        actor_user_id=actor_user_id,
        target_type="user",
        target_id=actor_user_id,
        metadata=_request_metadata(http_request),
    )
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
