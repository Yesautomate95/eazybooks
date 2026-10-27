import time

import yfinance as yf
import pandas as pd


# Function to calculate RSI
def calculate_rsi(data, period=14):
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


# Function to calculate EMA
def calculate_ema(data, period=50):
    ema = data['Close'].ewm(span=period, adjust=False).mean()
    return ema

def generate_alerts(ticker, rsi, ema, user_preferences):
    alerts = []  # List to hold individual alerts as dictionaries

    if not rsi.empty:
        latest_rsi = rsi.iloc[-1].item()  # Extract scalar value
        if latest_rsi < user_preferences["RSI Oversold"]:
            alerts.append({
                "ticker": ticker,
                "message": "Our analysis suggests that this stock is currently undervalued. This could be a good time to consider holding or buying."
            })
        elif latest_rsi > user_preferences["RSI Overbought"]:
            alerts.append({
                "ticker": ticker,
                "message": "Based on our calculations, this stock appears to be overvalued. You might consider selling or reducing your position."
            })

    if not ema.empty:
        latest_ema = ema.iloc[-1].item()  # Extract scalar value
        if latest_ema < user_preferences["EMA Threshold"]:
            alerts.append({
                "ticker": ticker,
                "message": "Our analysis indicates that this stock is showing weaker performance at the moment. You may want to monitor closely."
            })
        elif latest_ema > user_preferences["EMA Threshold"]:
            alerts.append({
                "ticker": ticker,
                "message": "This stock is showing strong performance based on our analysis. It could be a good time to hold or continue investing."
            })

    return alerts


async def generate_alert_messages(portfolio, batch_data):
    user_preferences = {
        "RSI Oversold": 30,
        "RSI Overbought": 70,
        "EMA Threshold": 200
    }

    all_alerts = []

    for holding in portfolio:
        ticker = holding.ticker
        try:
            if ticker not in batch_data or batch_data[ticker].empty:
                continue

            data = batch_data[ticker]  # Use pre-fetched batch data

            # Calculate RSI and EMA
            rsi = calculate_rsi(data)
            ema = calculate_ema(data)

            # Generate alerts
            alerts = generate_alerts(ticker, rsi, ema, user_preferences)
            all_alerts.extend(alerts)
        except Exception as e:
            print(f"Error processing {ticker}: {e}")

    return all_alerts if all_alerts else "No alerts generated for the portfolio."


