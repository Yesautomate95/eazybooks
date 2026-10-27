import pickle

import pandas as pd
from typing import List, Dict
import math
from utils.yfinanceFunc import get_ticker_data, get_batch_ticker_data


async def fetch_batch_ticker_data(tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """
    Fetch historical data for multiple tickers in a single batch request.
    """
    try:
        # Fetch data for all tickers in one request
        serialized_data = get_batch_ticker_data(tickers)
        if serialized_data is None:
            return {}  # Return empty dict if data fetching fails

        # Deserialize the data
        data = pickle.loads(serialized_data)
        return data
    except Exception as e:
        print(f"Error fetching batch data: {e}")
        return {}


def remove_nan_entries(data):
    """Remove historical prices that have NaN values."""
    for stock in data:
        stock["historical_prices"] = [
            entry for entry in stock["historical_prices"]
            if not (isinstance(entry["price"], float) and math.isnan(entry["price"]))
        ]
    return data


# List of Nifty 50 stock tickers and their weights in the index (in percentage)
nifty_50_weights = {
    "RELIANCE.NS": 10.1, "TCS.NS": 5.1, "INFY.NS": 5.0, "HDFCBANK.NS": 9.0,
    "ICICIBANK.NS": 7.5, "HINDUNILVR.NS": 3.5, "KOTAKBANK.NS": 4.2, "SBIN.NS": 3.8,
    "BHARTIARTL.NS": 3.3, "ITC.NS": 3.2, "ASIANPAINT.NS": 3.1, "HCLTECH.NS": 2.9,
    "WIPRO.NS": 1.5, "LT.NS": 2.8, "AXISBANK.NS": 2.7, "BAJFINANCE.NS": 2.6,
    "ADANIENT.NS": 2.5, "DMART.NS": 2.0, "SUNPHARMA.NS": 1.9, "TITAN.NS": 1.8,
    "ULTRACEMCO.NS": 1.7, "NTPC.NS": 1.6, "POWERGRID.NS": 1.5, "BAJAJFINSV.NS": 1.4,
    "JSWSTEEL.NS": 1.3, "MARUTI.NS": 1.2, "ONGC.NS": 1.1, "HDFC.NS": 1.0,
    "BPCL.NS": 0.9, "TATAMOTORS.NS": 0.8, "GRASIM.NS": 0.7, "CIPLA.NS": 0.6,
    "INDUSINDBK.NS": 0.5, "DIVISLAB.NS": 0.4, "APOLLOHOSP.NS": 0.3, "TECHM.NS": 0.2,
    "UPL.NS": 0.1, "ADANIPORTS.NS": 0.1, "SHREECEM.NS": 0.1, "EICHERMOT.NS": 0.1,
    "COALINDIA.NS": 0.1, "DRREDDY.NS": 0.1, "BRITANNIA.NS": 0.1, "HEROMOTOCO.NS": 0.1,
    "TATACONSUM.NS": 0.1, "M&M.NS": 0.1, "SBILIFE.NS": 0.1, "ICICIPRULI.NS": 0.1,
    "BAJAJ-AUTO.NS": 0.1, "GAIL.NS": 0.1
}

# Function to fetch stock data
def fetch_weighted_average_pe(tickers_with_weights):
    import pandas as pd

    data = []
    for ticker, weight in tickers_with_weights.items():
        try:
            serialized_data = get_ticker_data(ticker)
            if serialized_data is None:
                return ticker  # Fallback to ticker if data fetching fails

            stock = pickle.loads(serialized_data)
            price = stock["current_price"]  # Latest closing price
            eps = stock["info"].get('trailingEps', None)  # Trailing EPS
            pe = price / eps if eps else None  # Calculate P/E ratio
            data.append({
                "Ticker": ticker,
                "Price": price,
                "EPS": eps,
                "P/E": pe,
                "Weight (%)": weight
            })
        except Exception as e:
            print(f"Error fetching data for {ticker}")
            data.append({
                "Ticker": ticker,
                "Price": None,
                "EPS": None,
                "P/E": None,
                "Weight (%)": weight
            })

    # Convert data to a DataFrame
    nifty_pe_data = pd.DataFrame(data)

    # Calculate Weighted P/E
    nifty_pe_data['Weighted P/E'] = nifty_pe_data['P/E'] * nifty_pe_data['Weight (%)'] / 100
    weighted_average_pe = nifty_pe_data['Weighted P/E'].sum()

    # Return only the weighted average P/E
    return weighted_average_pe



import numpy as np


async def calculate_portfolio_score(portfolio_data, ticker_info, batch_data):
    """
    Calculate a performance score for the user's portfolio.
    """
    if not portfolio_data:
        return {"error": "Portfolio data is empty"}

    # Initialize variables for scoring
    total_score = 0
    metrics = {}
    total_current_value = 0
    sector_distribution = {}
    returns = []
    volatilities = []
    pe_ratios = []
    earliest_date = None
    latest_date = None

    # Process each ticker in the portfolio
    for ticker, data in portfolio_data.items():
        quantity = data.get("quantity", 0)
        cost_price = data.get("cost_price", 0)
        historical_prices = data.get("historical_prices", [])
        if not historical_prices or quantity <= 0:
            continue

        # Extract the latest price
        latest_price_entry = max(historical_prices, key=lambda x: datetime.strptime(x["date"], "%Y-%m-%d"))
        current_price = latest_price_entry["price"]
        current_value = quantity * current_price
        total_current_value += current_value

        # Store sector information from ticker_info
        sector = ticker_info[ticker]["sector"]
        sector_distribution[sector] = sector_distribution.get(sector, 0) + current_value

        # Store market cap and PE ratio from ticker_info
        market_cap = ticker_info[ticker]["market_cap"]
        pe_ratio = ticker_info[ticker]["pe_ratio"]
        if pe_ratio is not None:
            pe_ratios.append(pe_ratio)

        # Calculate returns and volatility
        prices = [entry["price"] for entry in historical_prices]
        dates = [entry["date"] for entry in historical_prices]
        if len(prices) > 1:
            individual_return = (current_price - cost_price) / cost_price * 100
            returns.append(individual_return)

            daily_returns = np.diff(prices) / prices[:-1]
            volatilities.append(np.std(daily_returns) * 100)

            # Update date range for Nifty data
            if not earliest_date or dates[0] < earliest_date:
                earliest_date = dates[0]
            if not latest_date or dates[-1] > latest_date:
                latest_date = dates[-1]

    # Portfolio metrics
    metrics["average_return"] = np.mean(returns) if returns else "N/A"
    metrics["average_volatility"] = np.mean(volatilities) if volatilities else "N/A"
    metrics["average_pe_ratio"] = np.mean(pe_ratios) if pe_ratios else "N/A"

    # Fetch Nifty data for the same period
    if earliest_date and latest_date:
        try:
            if "^NSEI" in batch_data.columns.levels[0]:  # Check if ^NSEI exists in multi-index columns
                nifty_data = batch_data["^NSEI"]  # Extract ^NSEI-specific data

                # Filter data within the required date range
                nifty_data = nifty_data.loc[earliest_date:latest_date]

                if not nifty_data.empty:
                    nifty_return = (
                            (nifty_data["Close"].iloc[-1] - nifty_data["Close"].iloc[0]) /
                            nifty_data["Close"].iloc[0] * 100
                    )
                    nifty_return_pd = pd.Series({"^NSEI": nifty_return}, name="Ticker")
                    metrics["nifty_return"] = nifty_return_pd
                else:
                    metrics["nifty_return"] = "N/A"
            else:
                metrics["nifty_return"] = "N/A"
        except Exception as e:
            print(f"Error processing ^NSEI: {e}")
            metrics["nifty_return"] = "N/A"
    else:
        metrics["nifty_return"] = "N/A"

    # Diversification score
    diversification_score = len(portfolio_data)
    metrics["diversification"] = diversification_score
    metrics["nifty_weighted_average_pe"] = 29.649779297008667
    total_score += min(diversification_score, 20)  # Cap at 20

    # Sector diversification
    for sector in sector_distribution:
        sector_distribution[sector] = (sector_distribution[sector] / total_current_value) * 100
    metrics["sector_diversification"] = sector_distribution

    # Final score (normalize to 0-100)
    metrics["final_score"] = min(max(total_score, 0), 100)
    return metrics

from datetime import datetime, timedelta


async def calculate_portfolio_current_value(total_portfolio_value, portfolio_data):
    portfolio_current_value = {}
    total_current_value = 0
    total_yesterday_value = 0

    if total_portfolio_value <= 0:
        raise ValueError("Total portfolio value must be greater than zero.")

    for ticker, data in portfolio_data.items():
        try:
            quantity = data.get("quantity", 0)
            if quantity <= 0:
                raise ValueError(f"Quantity for {ticker} must be greater than zero.")

            historical_prices = data.get("historical_prices", [])
            if not historical_prices:
                raise ValueError(f"No historical prices available for {ticker}.")

            latest_price_entry = max(historical_prices, key=lambda x: datetime.strptime(x["date"], "%Y-%m-%d"))
            current_price = latest_price_entry["price"]

            yesterday_date = (datetime.strptime(latest_price_entry["date"], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday_price_entry = next((price for price in historical_prices if price["date"] == yesterday_date), None)
            yesterday_price = yesterday_price_entry["price"] if yesterday_price_entry else current_price

            current_stock_value = quantity * current_price
            yesterday_stock_value = quantity * yesterday_price

            portfolio_current_value[ticker] = {
                "current_price": current_price,
                "quantity": quantity,
                "current_value": current_stock_value,
                "sector": data.get("sector", "Unknown")
            }

            total_current_value += current_stock_value
            total_yesterday_value += yesterday_stock_value
        except Exception as e:
            portfolio_current_value[ticker] = {
                "error": str(e),
                "current_price": None,
                "quantity": data.get("quantity", 0),
                "current_value": 0,
                "sector": data.get("sector", "Unknown"),
            }

    return {
        "portfolio_current_value": portfolio_current_value,
        "total_current_value": total_current_value,
        "total_value": total_portfolio_value,
        "total_yesterday_value": total_yesterday_value
    }


def get_current_price(ticker_symbol):
    """Fetch the current price using the deserialized ticker data."""
    try:
        # Fetch and deserialize the ticker data
        serialized_data = get_ticker_data(ticker_symbol)
        if serialized_data is None:
            return None  # Return None if data fetching fails

        ticker_data = pickle.loads(serialized_data)
        current_price = ticker_data["current_price"]
        return current_price
    except Exception as e:
        return f"Error: {str(e)}"

async def process_holding(holding, batch_data: Dict[str, pd.DataFrame], ticker_info: Dict) -> Dict:
    """
    Process a single holding using pre-fetched batch data and ticker_info.
    """
    ticker = holding.ticker
    if ticker in batch_data:
        historical_prices = [
            {"date": date.strftime("%Y-%m-%d"), "price": row["Close"]}
            for date, row in batch_data[ticker].iterrows()
        ]
        return {
            "ticker": ticker,
            "historical_prices": historical_prices,
            "weight": holding.weight,
            "quantity": holding.quantity,
            "cost_price": holding.cost_price,
            "current_price": holding.current_price,
            "sector": ticker_info[ticker]["sector"],
            "market_cap": ticker_info[ticker]["market_cap"],
            "pe_ratio": ticker_info[ticker]["pe_ratio"],
            "eps": ticker_info[ticker]["eps"],
        }
    else:
        print(f"No data available for ticker: {ticker}")
        return None


def categorize_market_cap(market_cap):
    """
    Categorize the market cap into 'Large Cap', 'Mid Cap', or 'Small Cap'.

    Parameters:
    - market_cap (int or float): The raw market cap value.

    Returns:
    - str: The market cap category.
    """
    if market_cap is None:
        return "Unknown"
    if market_cap >= 10 ** 10:  # Market cap >= $10 billion
        return "Large Cap"
    elif 2 * 10 ** 9 <= market_cap < 10 ** 10:  # Market cap between $2 billion and $10 billion
        return "Mid Cap"
    elif market_cap < 2 * 10 ** 9:  # Market cap < $2 billion
        return "Small Cap"
    else:
        return "Unknown"