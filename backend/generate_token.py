import firebase_admin
from firebase_admin import credentials, auth
import requests

# Initialize Firebase Admin SDK
cred = credentials.Certificate("firebase_key.json")  # Replace with your Firebase private key JSON
firebase_admin.initialize_app(cred)

# Function to generate Firebase ID token
def get_firebase_id_token(uid, api_key):
    """Get a Firebase ID token for a given UID."""
    try:
        # Step 1: Generate Custom Token
        custom_token = auth.create_custom_token(uid).decode('utf-8')

        # Step 2: Exchange Custom Token for ID Token
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={api_key}"
        payload = {
            "token": custom_token,
            "returnSecureToken": True
        }
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            id_token = response.json().get("idToken")
            return id_token
        else:
            return None
    except Exception as e:
        return None

# Function to test the /profile API endpoint
def test_profile_api(id_token):
    """Test the /profile API endpoint."""
    api_url = "http://127.0.0.1:8000/profile"
    headers = {
        "Authorization": f"Bearer {id_token}",
        "Content-Type": "application/json"
    }
    data = {
        "token": id_token,
        "name": "John Doe",
        "email": "johndoe@example.com"
    }

    try:
        response = requests.post(api_url, headers=headers, json=data)
    except Exception as e:

def get_profile_api(id_token):
    """Retrieve user profile from the API."""
    api_url = "http://127.0.0.1:8000/profile"
    headers = {
        "Authorization": f"Bearer {id_token}",
        "Content-Type": "application/json"
    }
    params = {"token": id_token}

    try:
        response = requests.get(api_url, headers=headers, params=params)
    except Exception as e:
        print("Error testing API:")

# Main Function
if __name__ == "__main__":
    # Replace with your UID and Firebase Web API Key
    uid = "qwToUMaVRpbhui9XTqnx6Rog2gj1"  # Replace with the actual UID
    api_key = "AIzaSyDaDoJnNmyIZGAJP9T2OQNEbOqE0Ikf2ss"  # Replace with your Firebase Web API Key

    # Step 1: Get Firebase ID Token
    id_token = get_firebase_id_token(uid, api_key)

    if id_token:
        test_profile_api(id_token)  # Save profile
        # get_profile_api(id_token)  # Retrieve profile

