import subprocess
import json

from finadvisor.chatbot.constants import facebook_access_token

# Replace with your Facebook Graph API credentials and information
ACCESS_TOKEN = facebook_access_token
WHATSAPP_API_URL = "https://graph.facebook.com/v21.0/529251713606166/messages"
USER_WHATSAPP_NUMBER = "919467999932"  # User's WhatsApp number (without 'whatsapp:' prefix)
TEMPLATE_NAME = "hello_world"  # Name of the WhatsApp template
LANGUAGE_CODE = "en_US"  # Template language code

def send_whatsapp_alert(user_name, portfolio_alert):
    # Prepare the message payload
    payload = {
        "messaging_product": "whatsapp",
        "to": USER_WHATSAPP_NUMBER,
        "type": "template",
        "template": {
            "name": TEMPLATE_NAME,
            "language": {
                "code": LANGUAGE_CODE
            }
        }
    }

    # Convert payload to JSON string
    payload_json = json.dumps(payload)

    # Build the curl command
    command = [
        "curl", "-i", "-X", "POST",
        WHATSAPP_API_URL,
        "-H", f"Authorization: Bearer {ACCESS_TOKEN}",
        "-H", "Content-Type: application/json",
        "-d", payload_json
    ]

    # Execute the curl command
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        pass
