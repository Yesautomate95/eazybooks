import pickle

from google.cloud import vision
from google.oauth2 import service_account
from io import BytesIO
from PyPDF2 import PdfReader
import threading
import json
import pandas as pd
import boto3

import logging

from utils.yfinanceFunc import get_ticker_data

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from finadvisor.services.FindTickerName import find_ticker, remove_commas_from_numbers
from finadvisor.services.LatestStockNews import get_stocks_news, get_specific_stock_news
from finadvisor.services.PortfolioScore import get_current_price
from finadvisor.services.RedditSearch import search_reddit
from finadvisor.services.InternetSearch import internet_search
from finadvisor.services.profile_service import (
    update_financial_data,
    get_financial_data,
    get_user_field,
    update_user_field,
)
from utils.gpt import run_gpt_completion
from firebase_setup import firestore
from datetime import datetime,timedelta,timezone
import uuid

# Import prompts from financialPrompts
from finadvisor.prompts.financialPrompts import (
    MERGE_FINANCIAL_DATA_PROMPT,
    FORMAT_FINANCIAL_REPORT_PROMPT,
    STRUCTURE_PORTFOLIO_DATA_PROMPT,
    GET_STOCK_INFO_PROMPT,
)


async def get_stock_info(stock_name):
    """
    Retrieve stock information and news for the given ticker and stock name.
    """
    ticker = find_ticker(stock_name)
    logger.info("ticker fetched: " + ticker)

    data = search(ticker)
    stock_news = await get_specific_stock_news(ticker)

    if data is None:
        return "Please provide a correct stock name."

    prompt = GET_STOCK_INFO_PROMPT.format(ticker=ticker, stock_name=stock_name)
    return f"{prompt}\n\nData: {data}\n\nRelevant News: {stock_news}"


async def extract_financial_data(user_id, financial_data_string):
    """
    Extract and save financial data for a specific user by merging old and new data intelligently.
    Updates both the 'financial_data' and 'financial_report' fields using a predefined template.
    """
    try:
        def submit_task_backend():
            # Load financial_report_template from sample_financial_data.json
            with open("./data/sample_financial_report.json", "r") as file:
                financial_report_template = json.load(file)

            # Fetch old financial data
            old_data = get_user_field(user_id, "financial_data")
            old_string = old_data.get("data", {}).get("financial_data", "")

            # Use GPT to intelligently merge old and new data
            prompt = MERGE_FINANCIAL_DATA_PROMPT.format(
                old_data=old_string,
                new_data=financial_data_string,
                example_format="salary: 15 LPA, major expenses: rent, investments: 5 LPA",
            )

            messages = [
                {"role": "system", "content": "You are a financial assistant."},
                {"role": "user", "content": prompt},
            ]

            consolidated_data = run_gpt_completion(messages)

            if consolidated_data is None:
                raise ValueError("Failed to parse GPT response.")

            update_financial_data(user_id, consolidated_data)
            update_user_field(user_id, "financial_report", consolidated_data)

            # Generate the 'financial_report' using the template
            report_prompt = FORMAT_FINANCIAL_REPORT_PROMPT.format(
                consolidated_data=json.dumps(consolidated_data),
                template=json.dumps(financial_report_template, indent=2),
            )

            report_messages = [
                {"role": "system", "content": "You are an assistant that formats financial data."},
                {"role": "user", "content": report_prompt},
            ]

            financial_report = run_gpt_completion(report_messages)

            if financial_report is None:
                raise ValueError("Failed to parse GPT response for financial report.")

            # Update the 'financial_report' field
            update_user_field(user_id, "financial_report", financial_report)

        thread = threading.Thread(target=submit_task_backend)
        thread.start()
        return f"Financial data and report updated for user {user_id}."

    except Exception as e:
        raise


async def extract_and_store_portfolio_data(user_id, portfolio_data_string):
    """
    Extract and store portfolio data for a specific user.
    Uses GPT to parse and structure the portfolio data into the desired format.
    """
    try:
        def submit_task_backend():
            # Define the portfolio template as a Python dictionary
            portfolio_format = {
                "holdings": [
                    {
                        "ticker": "RELIANCE.NS",
                        "weight": 25.0,
                        "quantity": 25.0,
                        "invested_date": "04-12-2024",
                        "cost_price" : 2500
                    }
                ],
                "invested_amount": 0
            }

            # Serialize the template dictionary into JSON
            template_json = json.dumps(portfolio_format, indent=4)
            # Create the prompt by formatting the template
            prompt = STRUCTURE_PORTFOLIO_DATA_PROMPT.format(
                portfolio_data=portfolio_data_string.strip(),
                template=template_json
            )


            # Generate structured portfolio data using GPT
            messages = [
                {"role": "system", "content": "You are a financial assistant."},
                {"role": "user", "content": prompt},
            ]

            structured_portfolio_data = run_gpt_completion(messages)

            remove_commas_from_numbers(structured_portfolio_data)


            if structured_portfolio_data is None:
                raise ValueError("Failed to parse GPT response for portfolio data.")

            portfolio_data = json.loads(structured_portfolio_data)
            invested_amount = 0

            # Calculate and populate 'invested_date' for each holding
            for holding in portfolio_data["holdings"]:
                ticker = find_ticker(holding["ticker"])
                holding["ticker"] = ticker
                cost_price = holding.get("cost_price", 0)
                current_price = get_current_price(ticker)
                holding["current_price"] = current_price

                if cost_price > 0:
                    invested_date = calculate_invested_date(ticker, cost_price)
                    invested_amount = invested_amount + cost_price * holding["quantity"]
                    holding["invested_date"] = invested_date or "Not Found"

            for holding in portfolio_data["holdings"]:
                try:
                    # Check if we have valid price data
                    if isinstance(holding["current_price"], (int, float)) or (
                            isinstance(holding["current_price"], str) and holding["current_price"].replace(".",
                                                                                                           "").isdigit()
                    ):
                        current_price = float(holding["current_price"])
                        quantity = float(holding["quantity"])
                        holding["weight"] = current_price * quantity / invested_amount * 100
                    else:
                        print(f"Invalid price data for holding")
                        holding["weight"] = 0  # Set weight to 0 for holdings with invalid price

                except (ValueError, KeyError) as e:
                    print(f"Error processing holding")
                    holding["weight"] = 0  # Set default weight for error cases


            portfolio_data["invested_amount"] = invested_amount
            update_user_field(user_id, "portfolio_data", json.dumps(portfolio_data))

        thread = threading.Thread(target=submit_task_backend)
        thread.start()
        return f"Portfolio data successfully updated for user {user_id}."

    except Exception as e:
        print(f"Error in extract_and_store_portfolio_data")
        raise



def search(ticker):
    """
    Fetch stock data for the given ticker.
    """
    try:
        payload = {
            "function_name": "get_stock_data",
            "ticker": ticker
        }
        return call_lambda(payload, "StockDataFetcher")
    except Exception as e:
        return None


def call_lambda(payload, function_name):
    try:
        lambda_client = boto3.client('lambda', region_name='ap-south-1')

        response = lambda_client.invoke(
            FunctionName=function_name,  # Lambda function name
            InvocationType="RequestResponse",  # Sync call
            Payload=json.dumps(payload)
        )

        response_payload = json.loads(response['Payload'].read())

        return response_payload
    except Exception as e:
        return None

_vision_client = None

def get_vision_client():
    global _vision_client
    if _vision_client is None:
        creds = service_account.Credentials.from_service_account_file(
            "data/deft-set-448318-p4-dc4d486cfd82.json"
        )
        _vision_client = vision.ImageAnnotatorClient(credentials=creds)
    return _vision_client
async def get_user_response(file, message):
    response_text = message

    # Process the file, if provided
    if file:
        file_content = await file.read()
        file_type = file.content_type

        try:
            if file_type.startswith("image/"):

                # Use Google Cloud Vision OCR to extract text from the image
                image = vision.Image(content=file_content)
                response = get_vision_client().text_detection(image=image)
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


def calculate_invested_date(ticker, cost_price):
    """
    Calculate the invested date for a stock based on its cost price.

    Parameters:
        ticker (str): The Yahoo Finance ticker symbol of the stock.
        cost_price (float): The cost price or buy price of the stock.

    Returns:
        str: The invested date in 'YYYY-MM-DD' format, or today's date if not found.
    """
    try:
        serialized_data = get_ticker_data(ticker)
        if serialized_data is None:
            return ticker  # Fallback to ticker if data fetching fails

        stock = pickle.loads(serialized_data)

        # Fetch historical price data with a buffer for price tolerance
        stock_data = stock["history"]
        if stock_data.empty:
            print(f"Invalid or delisted ticker: {ticker}")
            return datetime.today().strftime('%Y-%m-%d')

        # Add a small tolerance (0.5%) to account for intraday price movements
        tolerance = cost_price * 0.005

        stock_data = stock_data.reset_index()

        # More flexible matching criteria with tolerance
        matching_data = stock_data[
            (stock_data["Low"] <= cost_price + tolerance) &
            (stock_data["High"] >= cost_price - tolerance)
            ]

        if not matching_data.empty:
            # Convert to datetime if it's not already
            invested_date = pd.to_datetime(matching_data.iloc[-1]["Date"])
            result_date = invested_date.strftime('%Y-%m-%d')
            return result_date
        else:
            print(f"No match found for cost price {cost_price} in {ticker}'s historical data")
            return datetime.today().strftime('%Y-%m-%d')

    except Exception as e:
        print(f"Error processing {ticker}")
        return datetime.today().strftime('%Y-%m-%d')


def get_or_create_session(user_id, message):
    db=firestore.client()
    sessions_ref = db.collection('users').document(user_id).collection('sessions')
    latest_session = sessions_ref.order_by('last_active', direction=firestore.Query.DESCENDING).limit(1).get()
    current_time = datetime.now()  # Naive datetime

    for session in latest_session:
        session_dict = session.to_dict()
        last_active_time = session_dict['last_active'].replace(tzinfo=None)  # Convert to naive by removing timezone

        if last_active_time + timedelta(hours=1) > current_time:
            return session.id

    new_session_id = str(uuid.uuid4())
    messages = [
        {"role": "system", "content": "You are a expert in suggesting titles for chats. You will be given an initial message, you need to suggest a title in not more than 4-5 words "},
        {"role": "user", "content": message},
    ]
    sessions_ref.document(new_session_id).set({
        'created_at': datetime.now(),
        'last_active': datetime.now(),
        'session_title': run_gpt_completion(messages)
    })
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


