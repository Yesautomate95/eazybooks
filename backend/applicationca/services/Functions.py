import json

from google.cloud import vision
from google.oauth2 import service_account
from io import BytesIO
from PyPDF2 import PdfReader
import boto3

from applicationca.database.database_update import add_new_chat
from utils.gpt import run_gpt_completion
from firebase_setup import firestore
from datetime import datetime,timedelta,timezone
import uuid
import mimetypes
from firebase_setup import db

credentials = service_account.Credentials.from_service_account_file(
    "data/deft-set-448318-p4-dc4d486cfd82.json"
)

# Initialize the Vision API client with the credentials
vision_client = vision.ImageAnnotatorClient(credentials=credentials)
async def get_user_response(file_content, file_type):
    response_text = ""
    try:
        if file_type.startswith("image/"):

            # Use Google Cloud Vision OCR to extract text from the image
            image = vision.Image(content=file_content)
            response = vision_client.text_detection(image=image)
            annotations = response.text_annotations

            if annotations:
                # The first annotation contains the full extracted text
                extracted_text = annotations[0].description
                response_text += f" (Extracted text from image: {extracted_text})"
            else:
                response_text += " (No text detected in the image)"

        elif file_type == "application/pdf":
            # Process PDF files
            pdf_reader = PdfReader(BytesIO(file_content))
            pdf_text = "\n".join(page.extract_text() for page in pdf_reader.pages)
            response_text += f" (Extracted content from PDF: {pdf_text}...)"

        elif file_type.startswith("text/"):
            # Process text files
            text_content = file_content.decode("utf-8")
            response_text += f" (File content: {text_content}...)"

        else:
            response_text += " (Unsupported file type. Unable to process.)"

    except Exception as e:
        response_text += f" (Error processing file: {str(e)})"
    # Return the response
    return response_text


def get_or_create_session(user_id, message, chat_id_frontend, client_id, lockr_id):
    db=firestore.client()
    sessions_ref = db.collection('users').document(user_id).collection('sessions')
    latest_session = sessions_ref.order_by('last_active', direction=firestore.Query.DESCENDING).limit(1).get()

    for session in latest_session:
        if chat_id_frontend == session.id:
            return session.id

    new_session_id = chat_id_frontend
    messages = [
        {"role": "system", "content": "You are a expert in suggesting titles for chats. You will be given an initial message, you need to suggest a title in not more than 4-5 words "},
        {"role": "user", "content": message},
    ]

    session_title = run_gpt_completion(messages)
    session_title = session_title.strip('"')
    sessions_ref.document(new_session_id).set({
        'created_at': datetime.now(),
        'last_active': datetime.now(),
        'session_title': session_title
    })
    add_new_chat(user_id, new_session_id, client_id, lockr_id, session_title)
    return new_session_id

async def store_message(user_id, session_id, role, content):
    db = firestore.client()
    messages_ref = db.collection('users').document(user_id).collection('sessions').document(session_id).collection('messages')
    message_id = messages_ref.add({
        'role': role,
        'content': content,
        'timestamp': datetime.now(timezone.utc),
    })
    return message_id

def get_last_messages(user_id, session_id, limit=4):

    # Query the latest messages ordered descending by timestamp,
    # and limit the results to the provided limit.
    messages_ref = (
        db.collection("users")
        .document(user_id)
        .collection("sessions")
        .document(session_id)
        .collection("messages")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )

    # Stream the messages.
    messages = list(messages_ref.stream())

    # Convert Firestore documents to a JSON-like list.
    messages_list = [
        {**{k: v for k, v in doc.to_dict().items() if k != "timestamp"}}
        for doc in messages
    ]

    # Reverse the list to restore ascending order (oldest to newest).
    messages_list.reverse()
    return json.dumps(messages_list)


