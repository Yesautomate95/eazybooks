import pickle
from typing import List

from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from finadvisor.services.Alerts import generate_alert_messages
from finadvisor.services.Functions import search, get_user_response
from finadvisor.services.PortfolioScore import calculate_portfolio_score, calculate_portfolio_current_value, \
    fetch_batch_ticker_data, remove_nan_entries, process_holding, categorize_market_cap
from finadvisor.services.StockNews import fetch_news_portfolio
import asyncio
from finadvisor.chatbot.services.chat import ChatService
from utils.stream import stream_generator
from firebase_admin import firestore
from fastapi import APIRouter, Query, Depends, Body
from fastapi import HTTPException

from utils.yfinanceFunc import get_ticker_data
from finadvisor.validation.validate_assistant import validate_assistant
import uuid
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from utils.auth_utils import verify_jwt


router = APIRouter()

import json


from fastapi import File, Form, UploadFile
from fastapi.responses import JSONResponse

_chat_service = None

def _get_chat_service():
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service
# JWT Authentication Scheme
security = HTTPBearer()

def generate_session_id():
    """ Generate a new session ID """
    return str(uuid.uuid4())

@router.post("/chat")
async def get_chat_response(
        chat_id: str = Form(...),
        message: str = Form(...),
        file: UploadFile = File(None),
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    # Initial response text
    user_message = await get_user_response(file, message)
    user_message += (f" user_id: {user_data.get('uid')}")
    response = _get_chat_service().generate(chat_id, user_message)
    return StreamingResponse(stream_generator(response))



# Health Check Endpoint
@router.get("/health")
def health():
    return {"status": "API is running"}

class PortfolioHolding(BaseModel):
    ticker: str
    weight: float
    quantity: float
    cost_price: float
    current_price: float

class PortfolioRequest(BaseModel):
    holdings: List[PortfolioHolding]
    invested_amount: float


@router.post("/report")
async def fetch_financial_report(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Fetches the financial report for a user based on their UID from Firebase.
    """

    # Verify the token and get the user ID
    token = credentials.credentials
    user_data = verify_jwt(token)
    user_id = user_data.get('uid')
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or missing authentication token.")

    try:
        # Initialize Firestore client
        db = firestore.client()

        # Fetch the user's financial data from Firestore
        doc_ref = db.collection("profiles").document(user_id)
        doc = doc_ref.get()

        if doc.exists:
            # Return the financial report as JSON
            data = doc.to_dict()
            financial_report = data.get("financial_report")

            if not financial_report:
                raise HTTPException(status_code=404, detail="Financial report not found.")

            return financial_report

        else:
            raise HTTPException(status_code=404, detail="User profile not found.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching financial report: {str(e)}")



@router.post("/portfolio")
async def get_portfolio_data(request: PortfolioRequest):
    # Extract all tickers from the request
    tickers = [holding.ticker for holding in request.holdings]
    tickers.append("^NSEI")
    # Fetch historical data for all tickers in a single batch request
    batch_data = await fetch_batch_ticker_data(tickers)

    # Fetch additional data (e.g., sector, market cap, etc.) for all tickers
    ticker_info = {}
    for ticker in tickers:
        serialized_data = get_ticker_data(ticker)
        if serialized_data is None:
            return ticker  # Fallback to ticker if data fetching fails

        stock = pickle.loads(serialized_data)
        stock_info = stock["info"]

        ticker_info[ticker] = {
            "sector": stock_info.get("sector", "Unknown"),
            "market_cap": categorize_market_cap(stock_info.get("marketCap", None)),
            "pe_ratio": stock_info.get("trailingPE", None),
            "eps": stock_info.get("trailingEps", None),
        }

    # Process each holding using the pre-fetched batch data and ticker_info
    tasks = [process_holding(holding, batch_data, ticker_info) for holding in request.holdings]
    results = await asyncio.gather(*tasks)
    results = remove_nan_entries(results)

    # Filter out invalid tickers and build holdingsResponse
    valid_holdings = []
    holdingsResponse = {}
    for result in results:
        if result:
            ticker = result["ticker"]
            holdingsResponse[ticker] = result
            valid_holdings.append(next(h for h in request.holdings if h.ticker == ticker))

    # Only include valid holdings in request
    request.holdings = valid_holdings

    # Calculate portfolio score, alerts, current value, and news in parallel
    score_task = asyncio.create_task(calculate_portfolio_score(holdingsResponse, ticker_info, batch_data))
    alerts_task = asyncio.create_task(generate_alert_messages(valid_holdings, batch_data))
    current_value_task = asyncio.create_task(calculate_portfolio_current_value(request.invested_amount, holdingsResponse))
    news_task = asyncio.create_task(fetch_news_portfolio(valid_holdings))

    # Await all tasks
    score, alerts, current_value, news = await asyncio.gather(
        score_task, alerts_task, current_value_task, news_task
    )

    # Build response
    response = {
        "holdings": holdingsResponse,
        "portfolio_score": score,
        "alerts": alerts,
        "current_value": current_value,
        "news": news,
    }

    return response


@router.post("/portfolio_data")
async def fetch_portfolio_data(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Fetches the financial report for a user based on their UID from Firebase.
    """

    # Verify the token and get the user ID
    token = credentials.credentials
    user_data = verify_jwt(token)
    user_id = user_data.get('uid')
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or missing authentication token.")

    try:
        # Initialize Firestore client
        db = firestore.client()

        # Fetch the user's financial data from Firestore
        doc_ref = db.collection("profiles").document(user_id)
        doc = doc_ref.get()

        if doc.exists:
            # Return the financial report as JSON
            data = doc.to_dict()
            financial_report = data.get("portfolio_data")

            if not financial_report:
                raise HTTPException(status_code=404, detail="Portfolio data not found.")

            # Parse the financial report string into JSON
            try:
                financial_report_json = json.loads(financial_report)
            except json.JSONDecodeError as e:
                raise HTTPException(status_code=500, detail=f"Error parsing portfolio data: {str(e)}")

            return financial_report_json

        else:
            raise HTTPException(status_code=404, detail="User profile not found.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching financial report: {str(e)}")

@router.post("/service")
async def func():
    await validate_assistant()
    return "function called"


@router.post("/save_ca_registration")
async def fetch_portfolio_data(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Fetches the financial report for a user based on their UID from Firebase.
    """

    # Verify the token and get the user ID
    token = credentials.credentials
    user_data = verify_jwt(token)
    user_id = user_data.get('uid')
    email = user_data.get("email")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or missing authentication token.")

    try:
        # Initialize Firestore client
        db = firestore.client()

        # Fetch the user's financial data from Firestore
        doc_ref = db.collection("profiles").document(user_id)

        # Set the registration data in Firestore
        doc_ref.set({
            "registered_for_ca": True,
            "email": email
        }, merge=True)

        return {"message": "CA registration data saved successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching financial report: {str(e)}")


@router.post("/check_ca_registration")
async def check_ca_registration(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Checks if the user is registered for CA services.
    """
    # Verify the token and extract user information
    token = credentials.credentials
    user_data = verify_jwt(token)
    user_id = user_data.get('uid')

    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid token payload. Missing user_id.")

    try:
        # Initialize Firestore client
        db = firestore.client()

        # Reference to the user's document in the Firestore collection
        doc_ref = db.collection("profiles").document(user_id)
        doc = doc_ref.get()

        if not doc.exists:
            return {False}

        # Check the "registered_for_ca" field
        data = doc.to_dict()
        registered_for_ca = data.get("registered_for_ca", False)

        return registered_for_ca

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking CA registration: {str(e)}")

@router.get("/fetch_chats")
async def fetch_chats(user_id: str = Query(..., description="The user's ID"),
                      session_id: str = Query(..., description="The session ID")):
    """
    Fetches chat messages for a specific user and session from Firestore.
    """
    try:
        # Initialize Firestore client
        db = firestore.client()

        # Reference to the user's session
        messages_ref = db.collection('users').document(user_id).collection('sessions').document(session_id).collection(
            'messages')

        # Fetch messages ordered by timestamp
        messages = messages_ref.order_by('timestamp').get()

        # Convert messages to list of dicts
        chat_messages = [msg.to_dict() for msg in messages]

        if not chat_messages:
            return {"error": "No messages found for the specified session."}, 404

        return {"messages": chat_messages}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chats: {str(e)}")


class AppointmentRequest(BaseModel):
    name: str
    email: str
    city: str
    date: str
    selected_service: str

from datetime import datetime

@router.post("/book_appointment")
async def book_appointment(
    appointment_data: AppointmentRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Saves the appointment details for a user in Firestore.
    """
    db = firestore.client()
    # Verify the token and get the user ID
    token = credentials.credentials
    user_data = verify_jwt(token)
    user_id = user_data.get('uid')

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or missing authentication token.")

    try:
        # Fetch the user's document reference
        doc_ref = db.collection("profiles").document(user_id)

        # Save appointment details in Firestore
        doc_ref.set({
            "appointments": firestore.ArrayUnion([{
                "name": appointment_data.name,
                "email": appointment_data.email,
                "city": appointment_data.city,
                "date": appointment_data.date,
                "selected_service": appointment_data.selected_service,
                "timestamp": datetime.now().isoformat()
            }])
        }, merge=True)

        return {"message": "Appointment booked successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error booking appointment: {str(e)}")


import openai
_openai_client = None

def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.OpenAI()
    return _openai_client

@router.post("/autocomplete")
async def autocomplete(input_data: dict = Body(...)):
    """
    Generate autocomplete suggestions for financial questions using OpenAI's GPT model.
    """
    try:
        user_input = input_data.get("input", "").strip()

        if len(user_input) < 3:
            return {"suggestions": []}

        # Improved system prompt to focus on completion
        system_prompt = (
            "You are an AI trained for financial assistance. Given the start of a question, "
            "provide **only** possible logical completions that should make it a financial question, just complete the sentence do not change it, relevant, "
            "and grammatically correct. Do not start a new sentence, and do not rephrase."
        )

        response = _get_openai_client().chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Complete this question: {user_input}"},
            ],
            max_tokens=20,  # Reduce to force short, natural completions
            n=3,  # Generate 3 possible completions
            temperature=0.8,  # Lower for more reliable completions
            top_p=0.8,
            stop=["?", "\n"],  # Stop when a question is complete
        )

        # Extract clean suggestions
        suggestions = [choice.message.content.strip() for choice in response.choices]

        return {"suggestions": suggestions}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="An error occurred while generating autocomplete suggestions.",
        )
