from typing import List
import boto3
import json

def get_ticker_data(ticker):
    """Fetch ticker data and return it as a serialized DataFrame."""
    try:
        payload = {
            "function_name": "get_ticker_data",
            "ticker": ticker
        }
        return call_lambda(payload, "StockDataFetcher")
    except Exception as e:
        return None


def get_batch_ticker_data(tickers: List[str]) -> bytes:
    """
    Fetch historical data for multiple tickers in a single batch request and return serialized data.
    """
    try:
        payload = {
            "function_name": "get_batch_ticker_data",
            "ticker": tickers
        }
        return call_lambda(payload, "StockDataFetcher")
    except Exception as e:
        return None

def call_lambda(payload, function_name):
    try:
        print("in yFinance call lambda")
        lambda_client = boto3.client('lambda', region_name='ap-south-1')

        response = lambda_client.invoke(
            FunctionName=function_name,  # Lambda function name
            InvocationType="RequestResponse",  # Sync call
            Payload=json.dumps(payload)
        )
        response_payload = response['Payload'].read()
        return response_payload
    except Exception as e:
        return None