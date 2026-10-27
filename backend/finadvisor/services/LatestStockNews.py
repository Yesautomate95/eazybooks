from finadvisor.chatbot.constants.eazybooks_secrets import ALPHA_VANTAGE_API_KEY


async def get_stocks_news(sort_by):
    """
    Get general stock market news from India using Alpha Vantage
    Args:
        sort_by: popularity or date
    Returns:
        str: Concatenated news descriptions
    """
    try:
        # Add sort parameter to match the expected behavior
        url = f'https://www.alphavantage.co/query?function=NEWS_SENTIMENT&topics=indian_market,finance&sort={sort_by}&apikey={ALPHA_VANTAGE_API_KEY}'

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()

        news_text = ""
        counter = 0

        # Extract news from the feed
        for article in data.get('feed', []):
            if counter >= 4:
                break
            if article.get('summary'):
                news_text += article.get('summary', '') + " "
                counter += 1

        return news_text.strip()
    except Exception as e:
        print(f"Error fetching general market news")
        return ""


import xmltodict
import aiohttp
import urllib.parse
import re


async def get_specific_stock_news(ticker):
    """
    Get news for a specific stock using Google News RSS
    Args:
        ticker: Stock symbol (e.g., 'RELIANCE', 'AAPL')
    Returns:
        str: Concatenated news descriptions
    """
    try:
        # URL encode the stock name to handle special characters
        encoded_query = urllib.parse.quote(f"{ticker} stock")
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                content = await response.text()
                data = xmltodict.parse(content)

        news_text = ""
        counter = 0

        # Extract news from the RSS feed
        items = data.get('rss', {}).get('channel', {}).get('item', [])

        for article in items:
            if counter >= 4:
                break
            if article.get('description'):
                # Clean up the description to remove HTML tags
                clean_description = re.sub('<.*?>', '', article['description'])
                news_text += f"{clean_description} "
                counter += 1

        return news_text.strip()
    except Exception as e:
        print(f"Error fetching stock news")
        return ""



async def main():
    ticker = input("Enter the stock ticker (e.g., 'AAPL', 'RELIANCE.BSE'): ")
    print(f"Fetching news for ticker: {ticker}")

    news = await get_specific_stock_news(ticker)

import asyncio


if __name__ == "__main__":
    asyncio.run(main())