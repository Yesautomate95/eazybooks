import numpy as np
from sympy import sympify, SympifyError
import numpy_financial as npf
import json

async def npv(rate, cash_flows):
    """
    Calculate the Net Present Value (NPV) using numpy_financial.

    :param rate: Discount rate (decimal, e.g., 0.1 for 10%)
    :param cash_flows: List or array of cash flows (first cash flow is typically negative)
    :return: NPV as a float
    """
    return npf.npv(rate, cash_flows)


async def irr(cash_flows):
    """
    Calculate the Internal Rate of Return (IRR) using numpy_financial.

    :param cash_flows: List or array of cash flows
    :return: IRR as a float
    """
    return npf.irr(cash_flows)


async def loan_amortization(principal, annual_rate, years, payments_per_year=12):
    """
    Calculate loan amortization details using numpy_financial.

    :param principal: Loan amount
    :param annual_rate: Annual interest rate (decimal)
    :param years: Loan term in years
    :param payments_per_year: Number of payments per year (default is 12)
    :return: Dictionary with keys: 'payment', 'total_payment', 'total_interest', and 'amortization_schedule'
    """
    r = annual_rate / payments_per_year
    n = years * payments_per_year
    # npf.pmt returns a negative value for cash outflows; use abs() to return a positive payment.
    payment = abs(npf.pmt(r, n, principal))
    total_payment = payment * n
    total_interest = total_payment - principal

    schedule = []
    balance = principal
    for i in range(1, n + 1):
        interest_payment = abs(npf.ipmt(r, i, n, principal))
        principal_payment = abs(npf.ppmt(r, i, n, principal))
        balance = max(balance - principal_payment, 0)
        schedule.append({
            'payment_number': i,
            'payment': payment,
            'principal_payment': principal_payment,
            'interest_payment': interest_payment,
            'remaining_balance': balance
        })
    return {
        'payment': payment,
        'total_payment': total_payment,
        'total_interest': total_interest,
        'amortization_schedule': schedule
    }


async def compound_interest(principal, annual_rate, times_compounded, years):
    """
    Calculate future value using compound interest via numpy_financial.

    :param principal: Initial investment amount
    :param annual_rate: Annual interest rate (decimal)
    :param times_compounded: Number of compounding periods per year
    :param years: Number of years
    :return: Future value as a float
    """
    # npf.fv uses the rate per period and requires the present value as a negative number.
    return abs(npf.fv(annual_rate / times_compounded, times_compounded * years, 0, -principal))


async def effective_annual_rate(nominal_rate, compounding_periods):
    """
    Calculate the Effective Annual Rate (EAR).

    :param nominal_rate: Nominal annual interest rate (decimal)
    :param compounding_periods: Number of compounding periods per year
    :return: Effective annual rate as a decimal
    """
    return (1 + nominal_rate / compounding_periods) ** compounding_periods - 1


async def future_value(present_value_amt, rate, periods):
    """
    Calculate future value using numpy_financial.fv.

    :param present_value_amt: Present value amount
    :param rate: Interest rate per period (decimal)
    :param periods: Number of periods
    :return: Future value as a float
    """
    return abs(npf.fv(rate, periods, 0, -present_value_amt))


async def present_value(future_value_amt, rate, periods):
    """
    Calculate present value using numpy_financial.pv.

    :param future_value_amt: Future value amount
    :param rate: Interest rate per period (decimal)
    :param periods: Number of periods
    :return: Present value as a float
    """
    return abs(npf.pv(rate, periods, 0, -future_value_amt))


async def straight_line_depreciation(cost, salvage, useful_life):
    """
    Calculate the annual depreciation expense using the straight-line method.

    :param cost: Initial cost of the asset
    :param salvage: Salvage value at the end of its useful life
    :param useful_life: Useful life of the asset in years
    :return: Annual depreciation expense as a float
    """
    return (cost - salvage) / useful_life


async def double_declining_balance(cost, salvage, useful_life, year):
    """
    Calculate depreciation for a given year using the double declining balance method.

    :param cost: Initial cost of the asset
    :param salvage: Salvage value at the end of its useful life
    :param useful_life: Useful life of the asset in years
    :param year: Specific year (1-indexed) for which depreciation is calculated
    :return: Depreciation expense for the specified year
    :raises ValueError: If the year is out of valid range (1 to useful_life)
    """
    if year < 1 or year > useful_life:
        raise ValueError("Year must be between 1 and the useful life of the asset.")
    rate = 2 / useful_life
    book_value = cost
    depreciation = 0
    for y in range(1, year + 1):
        depreciation = book_value * rate
        if book_value - depreciation < salvage:
            depreciation = book_value - salvage
        book_value -= depreciation
    return depreciation


async def payback_period(initial_investment, cash_flows):
    """
    Calculate the payback period for an investment.

    :param initial_investment: Initial investment amount (positive value)
    :param cash_flows: List of annual cash flows (positive values)
    :return: Payback period in years (may be fractional) or None if the investment is never recovered.
    """
    cumulative = 0
    for i, cf in enumerate(cash_flows):
        cumulative += cf
        if cumulative >= initial_investment:
            previous_cumulative = cumulative - cf
            fraction = (initial_investment - previous_cumulative) / cf
            return i + fraction + 1  # +1 because cash flow years are assumed to start at year 1
    return None


async def profitability_index(cash_flows, rate):
    """
    Calculate the profitability index.

    :param cash_flows: List of cash flows where the first element is the initial investment (negative)
    :param rate: Discount rate (decimal)
    :return: Profitability index as a float
    """
    initial_investment = -cash_flows[0]
    discounted_cash_flows = sum(cf / ((1 + rate) ** i) for i, cf in enumerate(cash_flows[1:], start=1))
    return discounted_cash_flows / initial_investment


async def roi(gain_from_investment, cost_of_investment):
    """
    Calculate the Return on Investment (ROI).

    :param gain_from_investment: Total gains or returns from the investment
    :param cost_of_investment: Total cost of the investment
    :return: ROI as a decimal (e.g., 0.2 represents a 20% return)
    """
    return (gain_from_investment - cost_of_investment) / cost_of_investment

async def calculate(expression):
    try:
        print(expression)
        # Evaluate the expression using sympy
        result = sympify(expression).evalf()
        # Convert the result to a string for JSON serialization
        result_str = str(result)
        # Optionally, you could convert to float if the result is numeric
        result_float = float(result)
        # Create a dictionary with the results
        output = {
            "result_str": result_str,
            "result_float": result_float
        }
        # Dump the dictionary to a JSON string
        output = json.dumps(output)
        return output
    except SympifyError:
        return "Invalid math expression."



# Dictionary mapping function names to functions for easy integration into your chatbot.
financial_tools = {
    "npv": npv,
    "irr": irr,
    "loan_amortization": loan_amortization,
    "compound_interest": compound_interest,
    "effective_annual_rate": effective_annual_rate,
    "future_value": future_value,
    "present_value": present_value,
    "straight_line_depreciation": straight_line_depreciation,
    "double_declining_balance": double_declining_balance,
    "payback_period": payback_period,
    "profitability_index": profitability_index,
    "roi": roi
}
