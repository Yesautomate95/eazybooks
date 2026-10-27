from kiteconnect import KiteConnect
from fastapi import APIRouter
from finadvisor.chatbot.constants.eazybooks_secrets import kite_api_key, kite_secret_key

# Initialize Flask app and allow CORS
router = APIRouter()
# Kite Connect API keys
API_KEY = kite_api_key
API_SECRET = kite_secret_key

kite = KiteConnect(api_key=API_KEY)

# Route for login URL
@router.get('/api/login')
def login():
    login_url = kite.login_url()
    return {"login_url": login_url}

# Route for handling OAuth redirect
@router.get('/api/redirect')
def redirect_url(request_token: str = None):
    try:
        data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = data['access_token']
        kite.set_access_token(access_token)

        # Fetch user profile
        profile = kite.profile()
        return {"success": True, "profile": profile}
    except Exception as e:
        return {"success": False, "error": str(e)}

