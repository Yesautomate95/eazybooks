"""
    This file contains json-schemas for the tools

"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_presigned_url",
            "description": "Generates a pre-signed URL to securely fetch a document from storage. This function should be invoked only when the user explicitly requests to download a document or confirms that they want to retrieve a document mentioned in the response.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "user_id of user (In Internal parameters)"
                    },
                    "filename": {
                        "type": "string",
                        "description": "The exact name of the document to be retrieved. This must match the stored document name."
                    },
                    "folder_id": {
                        "type": "string",
                        "description": "folder_id (In Internal parameters)"
                    }
                },
                "required": ["user_id", "filename", "folder_id"]
            }
        }
    },
{
    "type": "function",
    "function": {
      "name": "npv",
      "description": "Calculates the Net Present Value (NPV) of a series of cash flows at a given discount rate using numpy_financial. The first cash flow should typically be negative to represent the initial investment. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "rate": {
            "type": "number",
            "description": "Discount rate as a decimal (e.g., 0.1 for 10%)."
          },
          "cash_flows": {
            "type": "array",
            "items": {
              "type": "number"
            },
            "description": "List of cash flows, where the initial investment is usually a negative number followed by positive cash flows."
          }
        },
        "required": ["rate", "cash_flows"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "irr",
      "description": "Calculates the Internal Rate of Return (IRR) for a series of cash flows using numpy_financial. It returns the discount rate at which the net present value (NPV) of the cash flows equals zero. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "cash_flows": {
            "type": "array",
            "items": {
              "type": "number"
            },
            "description": "List of cash flows with the initial investment as a negative number and subsequent cash flows as positive numbers."
          }
        },
        "required": ["cash_flows"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "loan_amortization",
      "description": "Calculates loan amortization details including the periodic payment, total payment, total interest, and a detailed amortization schedule. This is based on the loan principal, annual interest rate, loan term in years, and the number of payments per year. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "principal": {
            "type": "number",
            "description": "The total loan amount."
          },
          "annual_rate": {
            "type": "number",
            "description": "Annual interest rate as a decimal (e.g., 0.05 for 5%)."
          },
          "years": {
            "type": "number",
            "description": "The term of the loan in years."
          },
          "payments_per_year": {
            "type": "number",
            "description": "Number of payments per year. Defaults to 12 if not provided."
          }
        },
        "required": ["principal", "annual_rate", "years"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "compound_interest",
      "description": "Calculates the future value using compound interest for a given principal amount, annual interest rate, number of times interest is compounded per year, and the total number of years. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "principal": {
            "type": "number",
            "description": "The initial amount invested or borrowed."
          },
          "annual_rate": {
            "type": "number",
            "description": "Annual interest rate as a decimal (e.g., 0.05 for 5%)."
          },
          "times_compounded": {
            "type": "number",
            "description": "Number of compounding periods per year."
          },
          "years": {
            "type": "number",
            "description": "Total number of years the money is invested or borrowed for."
          }
        },
        "required": ["principal", "annual_rate", "times_compounded", "years"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "effective_annual_rate",
      "description": "Calculates the Effective Annual Rate (EAR) based on a nominal annual interest rate and the number of compounding periods per year. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "nominal_rate": {
            "type": "number",
            "description": "Nominal annual interest rate as a decimal (e.g., 0.05 for 5%)."
          },
          "compounding_periods": {
            "type": "number",
            "description": "Number of times interest is compounded per year."
          }
        },
        "required": ["nominal_rate", "compounding_periods"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "future_value",
      "description": "Calculates the future value of a present amount given a periodic interest rate and the number of periods using numpy_financial. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "present_value_amt": {
            "type": "number",
            "description": "The current amount of money (present value)."
          },
          "rate": {
            "type": "number",
            "description": "The interest rate per period as a decimal."
          },
          "periods": {
            "type": "number",
            "description": "The total number of periods."
          }
        },
        "required": ["present_value_amt", "rate", "periods"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "present_value",
      "description": "Calculates the present value of a future amount given a periodic interest rate and the number of periods using numpy_financial. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "future_value_amt": {
            "type": "number",
            "description": "The future amount of money to be received."
          },
          "rate": {
            "type": "number",
            "description": "The interest rate per period as a decimal."
          },
          "periods": {
            "type": "number",
            "description": "The total number of periods."
          }
        },
        "required": ["future_value_amt", "rate", "periods"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "straight_line_depreciation",
      "description": "Calculates the annual depreciation expense using the straight-line method based on the asset's initial cost, its salvage value, and its useful life. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "cost": {
            "type": "number",
            "description": "The initial cost of the asset."
          },
          "salvage": {
            "type": "number",
            "description": "The salvage value of the asset at the end of its useful life."
          },
          "useful_life": {
            "type": "number",
            "description": "The useful life of the asset in years."
          }
        },
        "required": ["cost", "salvage", "useful_life"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "double_declining_balance",
      "description": "Calculates the depreciation expense for a specific year using the double declining balance method. It uses the asset's cost, salvage value, useful life, and the specific year (1-indexed) for which the depreciation is calculated. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "cost": {
            "type": "number",
            "description": "The initial cost of the asset."
          },
          "salvage": {
            "type": "number",
            "description": "The salvage value of the asset at the end of its useful life."
          },
          "useful_life": {
            "type": "number",
            "description": "The useful life of the asset in years."
          },
          "year": {
            "type": "number",
            "description": "The specific year (1-indexed) for which to calculate the depreciation expense."
          }
        },
        "required": ["cost", "salvage", "useful_life", "year"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "payback_period",
      "description": "Calculates the payback period for an investment by determining the number of years (which may be fractional) required for the cumulative cash flows to cover the initial investment. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "initial_investment": {
            "type": "number",
            "description": "The initial investment amount (should be a positive number)."
          },
          "cash_flows": {
            "type": "array",
            "items": {
              "type": "number"
            },
            "description": "An array of annual cash flows representing the returns received each year."
          }
        },
        "required": ["initial_investment", "cash_flows"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "profitability_index",
      "description": "Calculates the profitability index by dividing the sum of the discounted future cash flows (excluding the initial investment) by the absolute value of the initial investment. The cash flows array should start with the initial investment as a negative number followed by positive cash flows. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "cash_flows": {
            "type": "array",
            "items": {
              "type": "number"
            },
            "description": "An array of cash flows where the first element is the initial investment (negative) and the subsequent elements are the returns."
          },
          "rate": {
            "type": "number",
            "description": "Discount rate as a decimal (e.g., 0.1 for 10%)."
          }
        },
        "required": ["cash_flows", "rate"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "roi",
      "description": "Calculates the Return on Investment (ROI) as the ratio of the net gain (gain from investment minus cost of investment) to the cost of investment. If any required parameter is missing, the chatbot should prompt the user for the missing information.",
      "parameters": {
        "type": "object",
        "properties": {
          "gain_from_investment": {
            "type": "number",
            "description": "The total gains or returns from the investment."
          },
          "cost_of_investment": {
            "type": "number",
            "description": "The total cost incurred for the investment."
          }
        },
        "required": ["gain_from_investment", "cost_of_investment"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "calculate",
      "description": "Invoke this function strictly whenever any math calculation or numerical evaluation is required, including basic arithmetic, algebra, financial computations, percentages, or other numerical evaluations at any point during the generation of a response.",
      "parameters": {
        "type": "object",
        "properties": {
          "expression": {
            "type": "string",
            "description": "A valid mathematical expression to evaluate. Example: '3 + 5', '2 * (3 + 5)', '(50000 * 0.2) - 10000', 'sqrt(144)', or '(120000 + 30000 + 50000)'."
          }
        },
        "required": ["expression"]
      }
    }
  },
{
    "type": "function",
    "function": {
        "name": "perform_web_search",
        "description": "Perform a web search to get up-to-date information when real-time data is needed",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up"
                }
            },
            "required": ["query"]
        }
    }
},
{
  "type": "function",
  "function": {
    "name": "retrieve_full_document",
    "description": "Fetches the complete original document when the provided text chunks lack necessary details to generate a fully accurate or legally compliant accounting response. The chatbot will auto-trigger this function when it detects: 1) Gaps in financial data (e.g., missing ledger entries), 2) Ambiguous references in excerpts (e.g., partial tax clauses), or 3) Need for verbatim legal/audit documentation (e.g., full contracts or statements). Never prompts the user—executes as an internal precision tool.",
    "parameters": {
      "type": "object",
      "properties": {
        "user_id": {
            "type": "string",
            "description": "user_id of user (In Internal parameters)"
        },
        "filename": {
            "type": "string",
            "description": "The exact name of the document to be retrieved. This must match the stored document name."
        },
        "folder_id": {
            "type": "string",
            "description": "folder_id (In Internal parameters)"
        }
      },
      "required": ["user_id", "filename", "folder_id"]
    }
  }
}

]
