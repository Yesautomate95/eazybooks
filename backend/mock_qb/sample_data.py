"""
Sample QuickBooks Desktop data for mock server.

QB terminology mapping (vs Tally):
- Tally Company        -> QB Company File
- Tally Ledger (Party) -> QB Customer / Vendor
- Tally Ledger (Other) -> QB Account
- Tally Group          -> QB Account Type
- Tally Inventory      -> QB Item
- Tally Voucher        -> QB Transaction (Invoice / Bill / etc.)
"""

import uuid
from typing import Dict, List, Optional


def _list_id() -> str:
    """QB ListIDs look like 80000001-1234567890. Mock with UUID prefix."""
    return f"800000{uuid.uuid4().hex[:8].upper()}-{uuid.uuid4().hex[:10]}"


# --- Companies (QB company files) ---
COMPANIES: List[Dict] = [
    {
        "name": "Acme Trading LLC",
        "file": "C:\\Users\\Public\\Documents\\Intuit\\QuickBooks\\Company Files\\Acme.QBW",
        "country": "US",
        "fiscal_year_start": "January",
    },
    {
        "name": "Beta Services Inc",
        "file": "C:\\Users\\Public\\Documents\\Intuit\\QuickBooks\\Company Files\\Beta.QBW",
        "country": "US",
        "fiscal_year_start": "April",
    },
]

# --- Accounts (QB Chart of Accounts) ---
# AccountType values: Bank, AccountsReceivable, AccountsPayable, Income, Expense, OtherCurrentAsset, etc.
ACCOUNTS: Dict[str, List[Dict]] = {
    "Acme Trading LLC": [
        {"list_id": _list_id(), "name": "Checking", "account_type": "Bank", "balance": "12500.00"},
        {"list_id": _list_id(), "name": "Accounts Receivable", "account_type": "AccountsReceivable", "balance": "8200.00"},
        {"list_id": _list_id(), "name": "Accounts Payable", "account_type": "AccountsPayable", "balance": "3400.00"},
        {"list_id": _list_id(), "name": "Sales", "account_type": "Income", "balance": "0.00"},
        {"list_id": _list_id(), "name": "Cost of Goods Sold", "account_type": "CostOfGoodsSold", "balance": "0.00"},
        {"list_id": _list_id(), "name": "Office Supplies", "account_type": "Expense", "balance": "0.00"},
        {"list_id": _list_id(), "name": "Sales Tax Payable", "account_type": "OtherCurrentLiability", "balance": "0.00"},
    ],
    "Beta Services Inc": [
        {"list_id": _list_id(), "name": "Checking", "account_type": "Bank", "balance": "5000.00"},
        {"list_id": _list_id(), "name": "Consulting Revenue", "account_type": "Income", "balance": "0.00"},
        {"list_id": _list_id(), "name": "Travel", "account_type": "Expense", "balance": "0.00"},
    ],
}

# --- Customers ---
CUSTOMERS: Dict[str, List[Dict]] = {
    "Acme Trading LLC": [
        {"list_id": _list_id(), "name": "Globex Corp", "company": "Globex Corp", "balance": "1200.00", "email": "ap@globex.com"},
        {"list_id": _list_id(), "name": "Initech LLC", "company": "Initech LLC", "balance": "0.00", "email": "billing@initech.com"},
        {"list_id": _list_id(), "name": "Soylent Inc", "company": "Soylent Inc", "balance": "7000.00", "email": "ap@soylent.com"},
    ],
    "Beta Services Inc": [
        {"list_id": _list_id(), "name": "Hooli", "company": "Hooli", "balance": "0.00", "email": "ap@hooli.com"},
    ],
}

# --- Vendors ---
VENDORS: Dict[str, List[Dict]] = {
    "Acme Trading LLC": [
        {"list_id": _list_id(), "name": "Staples", "company": "Staples", "balance": "200.00", "email": "ar@staples.com"},
        {"list_id": _list_id(), "name": "ACME Logistics", "company": "ACME Logistics", "balance": "3200.00", "email": "billing@acmelog.com"},
    ],
    "Beta Services Inc": [
        {"list_id": _list_id(), "name": "United Airlines", "company": "United Airlines", "balance": "0.00", "email": "corp@ua.com"},
    ],
}

# --- Items (products/services) ---
ITEMS: Dict[str, List[Dict]] = {
    "Acme Trading LLC": [
        {"list_id": _list_id(), "name": "Widget-A", "type": "Inventory", "rate": "25.00", "income_account": "Sales"},
        {"list_id": _list_id(), "name": "Widget-B", "type": "Inventory", "rate": "40.00", "income_account": "Sales"},
        {"list_id": _list_id(), "name": "Consulting Hour", "type": "Service", "rate": "150.00", "income_account": "Sales"},
    ],
    "Beta Services Inc": [
        {"list_id": _list_id(), "name": "Strategy Hour", "type": "Service", "rate": "300.00", "income_account": "Consulting Revenue"},
    ],
}

# --- Transaction store (invoices / bills pushed via mock) ---
TRANSACTIONS: List[Dict] = []


# --- Lookup helpers ---

def find_company(name: str) -> Optional[Dict]:
    return next((c for c in COMPANIES if c["name"].lower() == (name or "").lower()), None)


def get_accounts(company: str) -> List[Dict]:
    return ACCOUNTS.get(company, [])


def get_customers(company: str) -> List[Dict]:
    return CUSTOMERS.get(company, [])


def get_vendors(company: str) -> List[Dict]:
    return VENDORS.get(company, [])


def get_items(company: str) -> List[Dict]:
    return ITEMS.get(company, [])


def add_customer(company: str, customer: Dict) -> Dict:
    customer.setdefault("list_id", _list_id())
    customer.setdefault("balance", "0.00")
    CUSTOMERS.setdefault(company, []).append(customer)
    return customer


def add_vendor(company: str, vendor: Dict) -> Dict:
    vendor.setdefault("list_id", _list_id())
    vendor.setdefault("balance", "0.00")
    VENDORS.setdefault(company, []).append(vendor)
    return vendor


def add_account(company: str, account: Dict) -> Dict:
    account.setdefault("list_id", _list_id())
    account.setdefault("balance", "0.00")
    ACCOUNTS.setdefault(company, []).append(account)
    return account


def add_transaction(txn: Dict) -> Dict:
    txn.setdefault("txn_id", _list_id())
    TRANSACTIONS.append(txn)
    return txn
