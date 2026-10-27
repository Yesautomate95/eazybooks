import requests
from urllib.parse import quote
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
def search_yahoo_finance(query):
    """
    Search Yahoo Finance for a ticker using their search API
    Returns the best match only
    """
    try:
        encoded_query = quote(query)
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={encoded_query}&quotesCount=1&newsCount=0&enableFuzzyQuery=false&quotesQueryId=tss_match_phrase_query"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            quotes = data.get('quotes', [])
            if quotes:
                return quotes[0].get('symbol')
        return None
    except Exception as e:
        print(f"Search error")
        return None


def find_ticker(search_string):
    """
    Find the best matching ticker for the search string
    Returns: tuple (ticker, name) or (None, None) if not found
    """

    # If no result, try direct variations
    variations = [
        search_string + '.NS',
        search_string + '.BO',
        search_string,
        search_string + '.MF'
    ]

    for variant in variations:
        ticker = search_yahoo_finance(variant)
        if ticker:
            return ticker

    return None

def main():
    cleaned_data = remove_commas_from_numbers(json_str)


import re
import ast


def remove_commas_from_numbers(data_str):
    # Convert the string to a Python dictionary using `ast.literal_eval`
    data = ast.literal_eval(data_str)

    # Iterate through the holdings and clean numerical values
    for holding in data["holdings"]:
        for key, value in holding.items():
            # Remove commas if the value is a string and contains digits
            if isinstance(value, str) and re.match(r'^[\d,]+$', value):
                holding[key] = value.replace(",", "")

    return data

json_str = """
{
    'holdings': [
        {
            'ticker': 'Motilal Oswal Midcap',
            'weight': 17.69,
            'quantity': '41,926',
            'invested_date': '10-04-2024',
            'cost_price': '34,998'
        },
        {
            'ticker': 'Nippon India Small Cap',
            'weight': 17.45,
            'quantity': '41,297',
            'invested_date': '10-04-2024',
            'cost_price': '34,998'
        },
        {
            'ticker': 'Tata Small Cap',
            'weight': 13.99,
            'quantity': '33,886',
            'invested_date': '10-04-2024',
            'cost_price': '28,999'
        }
    ],
    'invested_amount': '1,266,077'
}
"""

if __name__ == "__main__":
    main()