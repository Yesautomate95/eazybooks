"""
    This file contains json-schemas for the tools

"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_info",
            "description": "Retrieves stock-related information—such as news, market updates, quotes, or financial data—for a given company or ticker symbol. Invoke this function whenever a user asks for stock news, price, performance, or other market details about a specific company or ticker. This includes requests like 'latest developments', 'recent headlines', 'current share price', 'financial details', or 'market updates' for a specific stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_name": {
                        "type": "string",
                        "description": "The name or ticker symbol of the stock, e.g. 'Apple Inc.', 'AAPL', 'Reliance', 'TSLA'."
                    }
                },
                "required": ["stock_name"]
            }
        }
    }
    ,
    {
        "type": "function",
        "function": {
            "name": "extract_financial_data",
            "description": "Extracts financial data such as income, assets, expenses, savings, and investments or significant financial events such as 'inheritance', 'bonuses', or 'gifts' from user input. Triggered when financial details are directly or indirectly mentioned.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "A unique identifier for the user. Required to associate extracted financial data with the correct user."
                    },
                    "financial_data_string": {
                        "type": "string",
                        "description": "The user's input string potentially containing financial data to be processed and extracted."
                    }
                },
                "required": ["user_id", "financial_data_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "extract_and_store_portfolio_data",
            "description": "Extracts and stores portfolio data such as ticker, weight, and invested amount from user input. The extracted data is formatted into a consistent and detailed structure, including the following fields for each holding:\n\n- **Ticker**: Stock identifier.\n- **Shares/Quantity**: Number of units held.\n- **Purchase Price**: The price at which each unit was acquired.\n- **LTP (Last Traded Price)**: Current market price of each unit.\n- **Current Value**: Total value of the holding (Shares x LTP).\n- **P&L**: Profit or Loss (difference between Current Value and invested amount).\n- **Day Change**: Percentage change in value during the day.\n\nThe output should be formatted as:\n\n`[Ticker]: [Quantity] shares at ₹[Purchase Price], LTP: ₹[LTP], Current Value: ₹[Current Value], P&L: ₹[P&L], Day Change: [Day Change]%`.\n\nThis function is triggered when portfolio or investment holdings are mentioned directly or indirectly, and it avoids responding to general investment advice queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "A unique identifier for the user. Required to associate extracted portfolio data with the correct user."
                    },
                    "portfolio_data_string": {
                        "type": "string",
                        "description": "The user's input string containing details about their portfolio, including tickers, quantities, cost prices, and other relevant details. Extracted data will follow the described format."
                    }
                },
                "required": ["user_id", "portfolio_data_string"]
            }
        }
    },
{
    "type": "function",
    "function": {
        "name": "internet_search",
        "description": "Triggers when a user requests information, resources, or recommendations on any topic requiring real-time retrieval from the internet. This includes queries like 'investment options,' 'best practices,' 'product comparisons,' and more. It interprets the user's natural language request and retrieves relevant information from top search results.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string. This represents the information or topic the user wants to find."
                },
                "max_results": {
                    "type": "integer",
                    "description": "The maximum number of search results to fetch and process. Defaults to 5 if not specified.",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    }
}


]
