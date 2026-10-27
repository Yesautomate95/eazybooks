from datetime import datetime, timedelta
import json
import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization
from fastapi import Response

# Function to sign the policy using the private key
def sign_policy(policy_str, private_key_path):
    with open(private_key_path, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None,
        )
    signature = private_key.sign(
        policy_str.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA1()
    )
    return base64.b64encode(signature).decode("utf-8")

def get_cookies(uid: str):
    # Set expiration using UTC time
    expire_date = datetime.utcnow() + timedelta(minutes=60)  # Adjust minutes as needed

    # Create a custom policy restricting access to the user's folder.
    # Adjust the resource URL as needed (ensure it matches the URL after CloudFront rewriting).
    policy_dict = {
        "Statement": [
            {
                "Resource": f"https://ca.aureusfin.in/user-files/{uid}/*",  # Restrict to user's folder
                "Condition": {
                    "DateLessThan": {"AWS:EpochTime": int(expire_date.timestamp())}
                }
            }
        ]
    }

    # Create the policy JSON string with minimal formatting
    policy_json_str = json.dumps(policy_dict, separators=(',', ':'))

    # Sign the raw policy JSON
    signature = sign_policy(policy_json_str, "private_key.pem")

    # Base64 encode the policy JSON for the cookie value
    policy_base64 = base64.b64encode(policy_json_str.encode("utf-8")).decode("utf-8")

    # Create the signed cookies
    signed_cookies = {
        "CloudFront-Key-Pair-Id": "K3JGF1F6LCWUYZ",  # Replace with your CloudFront key pair (or public key) ID
        "CloudFront-Policy": policy_base64,
        "CloudFront-Signature": signature,
    }

    return signed_cookies
