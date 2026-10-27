import pickle

import requests

from finadvisor.chatbot.constants.eazybooks_secrets import stock_news_api_key
from utils.yfinanceFunc import get_ticker_data


# Example payload

def get_company_name(ticker):
    """Fetch the company name using the yfinance library."""
    try:
        serialized_data = get_ticker_data(ticker)
        if serialized_data is None:
            return ticker  # Fallback to ticker if data fetching fails

        stock = pickle.loads(serialized_data)
        stock_info = stock["info"]
        return stock_info.get("longName", ticker)  # Return longName or fallback to ticker
    except Exception as e:
        print(f"Error fetching company name for {ticker}: {e}")
        return ticker  # Fallback to ticker if there's an error


def fetch_trending_news(tickers):
    """Fetch one trending news article for each ticker's company name."""
    api_key = stock_news_api_key
    base_url = "https://newsapi.org/v2/everything"

    articles = []
    for ticker in tickers:
        company_name = get_company_name(ticker)  # Fetch the company name
        try:
            # Request news articles for the company name
            params = {
                "q": company_name,
                "sortBy": "popularity",
                "apiKey": api_key,
            }
            response = requests.get(base_url, params=params)
            response.raise_for_status()  # Raise error for bad status codes
            data = response.json()

            # Add the first article for the company (if available)
            if data.get("articles"):
                article = data["articles"][0]
                articles.append({
                    "ticker": ticker,
                    "company_name": company_name,
                    "title": article["title"],
                    "url": article["url"]
                })
        except Exception as e:
            print(f"Error fetching news for {company_name}: {e}")

    return articles


async def fetch_news_portfolio(payload):
    tickers = [holding.ticker for holding in payload]
    news_articles = fetch_trending_news(tickers)
    return news_articles