"""
Sample Data for Mock Tally Server
=================================
Comprehensive Indian business data that mimics real Tally installations.
Includes companies, ledgers, groups, inventory items, godowns, and vouchers.
"""

from typing import List, Dict, Any
from datetime import datetime, timedelta
import random

# ============================================================================
# COMPANIES
# ============================================================================
COMPANIES: List[Dict[str, str]] = [
    {"name": "Sharma Enterprises Pvt Ltd"},
    {"name": "Gupta Trading Co"},
    {"name": "Patel Industries"},
    {"name": "Kumar Electronics"},
    {"name": "Mehta Textiles"},
    {"name": "Agarwal & Sons"},
    {"name": "Demo Company (Educational)"},
]

# ============================================================================
# STANDARD TALLY GROUPS (Hierarchy)
# ============================================================================
GROUPS: List[Dict[str, Any]] = [
    # Primary Groups (Top Level)
    {"name": "Capital Account", "parent": "", "isPrimary": True, "nature": "Liabilities"},
    {"name": "Current Assets", "parent": "", "isPrimary": True, "nature": "Assets"},
    {"name": "Current Liabilities", "parent": "", "isPrimary": True, "nature": "Liabilities"},
    {"name": "Direct Expenses", "parent": "", "isPrimary": True, "nature": "Expenses"},
    {"name": "Direct Incomes", "parent": "", "isPrimary": True, "nature": "Income"},
    {"name": "Fixed Assets", "parent": "", "isPrimary": True, "nature": "Assets"},
    {"name": "Indirect Expenses", "parent": "", "isPrimary": True, "nature": "Expenses"},
    {"name": "Indirect Incomes", "parent": "", "isPrimary": True, "nature": "Income"},
    {"name": "Investments", "parent": "", "isPrimary": True, "nature": "Assets"},
    {"name": "Loans (Liability)", "parent": "", "isPrimary": True, "nature": "Liabilities"},
    {"name": "Misc. Expenses (ASSET)", "parent": "", "isPrimary": True, "nature": "Assets"},
    {"name": "Purchase Accounts", "parent": "", "isPrimary": True, "nature": "Expenses"},
    {"name": "Sales Accounts", "parent": "", "isPrimary": True, "nature": "Income"},
    {"name": "Suspense A/c", "parent": "", "isPrimary": True, "nature": "Liabilities"},
    {"name": "Branch / Divisions", "parent": "", "isPrimary": True, "nature": "Liabilities"},

    # Sub-groups under Current Assets
    {"name": "Bank Accounts", "parent": "Current Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Bank OCC A/c", "parent": "Bank Accounts", "isPrimary": False, "nature": "Assets"},
    {"name": "Bank OD A/c", "parent": "Bank Accounts", "isPrimary": False, "nature": "Assets"},
    {"name": "Cash-in-Hand", "parent": "Current Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Deposits (Asset)", "parent": "Current Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Loans & Advances (Asset)", "parent": "Current Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Stock-in-Hand", "parent": "Current Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Sundry Debtors", "parent": "Current Assets", "isPrimary": False, "nature": "Assets"},

    # Sub-groups under Current Liabilities
    {"name": "Sundry Creditors", "parent": "Current Liabilities", "isPrimary": False, "nature": "Liabilities"},
    {"name": "Duties & Taxes", "parent": "Current Liabilities", "isPrimary": False, "nature": "Liabilities"},
    {"name": "Provisions", "parent": "Current Liabilities", "isPrimary": False, "nature": "Liabilities"},

    # Sub-groups under Indirect Expenses
    {"name": "Administrative Expenses", "parent": "Indirect Expenses", "isPrimary": False, "nature": "Expenses"},
    {"name": "Selling Expenses", "parent": "Indirect Expenses", "isPrimary": False, "nature": "Expenses"},

    # Sub-groups under Fixed Assets
    {"name": "Furniture & Fixtures", "parent": "Fixed Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Plant & Machinery", "parent": "Fixed Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Computer & IT Equipment", "parent": "Fixed Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Vehicles", "parent": "Fixed Assets", "isPrimary": False, "nature": "Assets"},
    {"name": "Land & Building", "parent": "Fixed Assets", "isPrimary": False, "nature": "Assets"},

    # Sub-groups under Loans (Liability)
    {"name": "Secured Loans", "parent": "Loans (Liability)", "isPrimary": False, "nature": "Liabilities"},
    {"name": "Unsecured Loans", "parent": "Loans (Liability)", "isPrimary": False, "nature": "Liabilities"},
    {"name": "Bank OD Accounts", "parent": "Loans (Liability)", "isPrimary": False, "nature": "Liabilities"},
]

# ============================================================================
# LEDGERS (Comprehensive list for each group)
# ============================================================================
LEDGERS: List[Dict[str, Any]] = [
    # ---- Bank Accounts ----
    {"name": "HDFC Bank - Current A/c", "parent_group": "Bank Accounts", "opening_balance": "125000.00",
     "address": "HDFC Bank, MG Road Branch", "state": "Maharashtra", "country": "India", "mobile": "", "gstin": ""},
    {"name": "ICICI Bank - Current A/c", "parent_group": "Bank Accounts", "opening_balance": "87500.00",
     "address": "ICICI Bank, Andheri Branch", "state": "Maharashtra", "country": "India", "mobile": "", "gstin": ""},
    {"name": "State Bank of India", "parent_group": "Bank Accounts", "opening_balance": "250000.00",
     "address": "SBI Main Branch", "state": "Maharashtra", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Axis Bank - Savings A/c", "parent_group": "Bank Accounts", "opening_balance": "45000.00",
     "address": "Axis Bank, Powai Branch", "state": "Maharashtra", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Kotak Mahindra Bank", "parent_group": "Bank Accounts", "opening_balance": "67500.00",
     "address": "Kotak Bank, BKC Branch", "state": "Maharashtra", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Bank of Baroda", "parent_group": "Bank Accounts", "opening_balance": "95000.00",
     "address": "BOB, Dadar Branch", "state": "Maharashtra", "country": "India", "mobile": "", "gstin": ""},

    # ---- Cash ----
    {"name": "Cash", "parent_group": "Cash-in-Hand", "opening_balance": "50000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Petty Cash", "parent_group": "Cash-in-Hand", "opening_balance": "10000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},

    # ---- Sundry Debtors (Customers) ----
    {"name": "Reliance Industries Ltd", "parent_group": "Sundry Debtors", "opening_balance": "450000.00",
     "address": "Maker Chambers, Nariman Point", "state": "Maharashtra", "country": "India",
     "mobile": "9876543210", "gstin": "27AAACR5055K1ZK"},
    {"name": "Tata Consultancy Services", "parent_group": "Sundry Debtors", "opening_balance": "325000.00",
     "address": "TCS House, Ravindra Niwas", "state": "Maharashtra", "country": "India",
     "mobile": "9876543211", "gstin": "27AAACT2727Q1ZW"},
    {"name": "Infosys Ltd", "parent_group": "Sundry Debtors", "opening_balance": "275000.00",
     "address": "Electronics City", "state": "Karnataka", "country": "India",
     "mobile": "9876543212", "gstin": "29AAACI1195G1ZV"},
    {"name": "Wipro Ltd", "parent_group": "Sundry Debtors", "opening_balance": "180000.00",
     "address": "Sarjapur Road", "state": "Karnataka", "country": "India",
     "mobile": "9876543213", "gstin": "29AABCW0611P1ZT"},
    {"name": "HCL Technologies", "parent_group": "Sundry Debtors", "opening_balance": "220000.00",
     "address": "Noida, Sector 126", "state": "Uttar Pradesh", "country": "India",
     "mobile": "9876543214", "gstin": "09AAACH0426B1ZH"},
    {"name": "Mahindra & Mahindra", "parent_group": "Sundry Debtors", "opening_balance": "165000.00",
     "address": "Gateway Building, Apollo Bunder", "state": "Maharashtra", "country": "India",
     "mobile": "9876543215", "gstin": "27AAACM5765G1ZN"},
    {"name": "Bajaj Auto Ltd", "parent_group": "Sundry Debtors", "opening_balance": "145000.00",
     "address": "Akurdi, Pune", "state": "Maharashtra", "country": "India",
     "mobile": "9876543216", "gstin": "27AAACB5042A1ZF"},
    {"name": "Maruti Suzuki India", "parent_group": "Sundry Debtors", "opening_balance": "390000.00",
     "address": "Nelson Mandela Road, Vasant Kunj", "state": "Delhi", "country": "India",
     "mobile": "9876543217", "gstin": "07AAACM4406C1ZB"},
    {"name": "Hindustan Unilever", "parent_group": "Sundry Debtors", "opening_balance": "210000.00",
     "address": "Unilever House, Andheri", "state": "Maharashtra", "country": "India",
     "mobile": "9876543218", "gstin": "27AAACH0549R1ZS"},
    {"name": "ITC Limited", "parent_group": "Sundry Debtors", "opening_balance": "175000.00",
     "address": "Virginia House, Kolkata", "state": "West Bengal", "country": "India",
     "mobile": "9876543219", "gstin": "19AAACI5765A1ZZ"},

    # ---- Sundry Creditors (Suppliers) ----
    {"name": "Larsen & Toubro Ltd", "parent_group": "Sundry Creditors", "opening_balance": "-380000.00",
     "address": "L&T House, Ballard Estate", "state": "Maharashtra", "country": "India",
     "mobile": "9988776655", "gstin": "27AAACL0140N1Z0"},
    {"name": "Bharat Electronics Ltd", "parent_group": "Sundry Creditors", "opening_balance": "-225000.00",
     "address": "Outer Ring Road, Bengaluru", "state": "Karnataka", "country": "India",
     "mobile": "9988776656", "gstin": "29AAACB3412D1ZP"},
    {"name": "Tata Steel Ltd", "parent_group": "Sundry Creditors", "opening_balance": "-450000.00",
     "address": "Bombay House, Mumbai", "state": "Maharashtra", "country": "India",
     "mobile": "9988776657", "gstin": "27AAACT0632C1ZA"},
    {"name": "JSW Steel Ltd", "parent_group": "Sundry Creditors", "opening_balance": "-320000.00",
     "address": "JSW Centre, BKC", "state": "Maharashtra", "country": "India",
     "mobile": "9988776658", "gstin": "27AABCJ3752J1ZV"},
    {"name": "Ambuja Cements", "parent_group": "Sundry Creditors", "opening_balance": "-180000.00",
     "address": "Ambuja Tower, Ahmedabad", "state": "Gujarat", "country": "India",
     "mobile": "9988776659", "gstin": "24AABCA8704Q1ZR"},
    {"name": "ACC Limited", "parent_group": "Sundry Creditors", "opening_balance": "-145000.00",
     "address": "Cement House, Worli", "state": "Maharashtra", "country": "India",
     "mobile": "9988776660", "gstin": "27AAACA8904F1ZI"},
    {"name": "Asian Paints Ltd", "parent_group": "Sundry Creditors", "opening_balance": "-95000.00",
     "address": "Asian Paints House, Santacruz", "state": "Maharashtra", "country": "India",
     "mobile": "9988776661", "gstin": "27AAACA3335B1ZE"},
    {"name": "Pidilite Industries", "parent_group": "Sundry Creditors", "opening_balance": "-78000.00",
     "address": "Regent Chambers, Nariman Point", "state": "Maharashtra", "country": "India",
     "mobile": "9988776662", "gstin": "27AAACP3469Q1ZP"},
    {"name": "Berger Paints India", "parent_group": "Sundry Creditors", "opening_balance": "-62000.00",
     "address": "Berger House, Kolkata", "state": "West Bengal", "country": "India",
     "mobile": "9988776663", "gstin": "19AAACB2404J1Z9"},
    {"name": "Supreme Industries", "parent_group": "Sundry Creditors", "opening_balance": "-85000.00",
     "address": "Supreme House, Andheri", "state": "Maharashtra", "country": "India",
     "mobile": "9988776664", "gstin": "27AAACS7522C1ZJ"},

    # ---- Sales Accounts ----
    {"name": "Sales - Domestic", "parent_group": "Sales Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Sales - Export", "parent_group": "Sales Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Sales - GST 18%", "parent_group": "Sales Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Sales - GST 12%", "parent_group": "Sales Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Sales - GST 5%", "parent_group": "Sales Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Sales - Exempt", "parent_group": "Sales Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Sales Returns", "parent_group": "Sales Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},

    # ---- Purchase Accounts ----
    {"name": "Purchase - Domestic", "parent_group": "Purchase Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Purchase - Import", "parent_group": "Purchase Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Purchase - GST 18%", "parent_group": "Purchase Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Purchase - GST 12%", "parent_group": "Purchase Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Purchase - GST 5%", "parent_group": "Purchase Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Purchase Returns", "parent_group": "Purchase Accounts", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},

    # ---- Indirect Expenses ----
    {"name": "Rent Expense", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Salary & Wages", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Electricity Charges", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Telephone & Internet", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Office Expenses", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Travelling Expenses", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Conveyance Expenses", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Printing & Stationery", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Repairs & Maintenance", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Professional Fees", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Legal Expenses", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Audit Fees", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Bank Charges", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Interest Paid", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Insurance Premium", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Depreciation", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Advertisement Expenses", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Courier & Postage", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Entertainment Expenses", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Miscellaneous Expenses", "parent_group": "Indirect Expenses", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},

    # ---- Indirect Income ----
    {"name": "Interest Received", "parent_group": "Indirect Incomes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Discount Received", "parent_group": "Indirect Incomes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Commission Received", "parent_group": "Indirect Incomes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Rent Received", "parent_group": "Indirect Incomes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Dividend Received", "parent_group": "Indirect Incomes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Miscellaneous Income", "parent_group": "Indirect Incomes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},

    # ---- Duties & Taxes ----
    {"name": "CGST Payable", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "SGST Payable", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "IGST Payable", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "CGST Input", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "SGST Input", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "IGST Input", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "TDS Payable", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "TCS Payable", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Income Tax Payable", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Professional Tax Payable", "parent_group": "Duties & Taxes", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},

    # ---- Capital Account ----
    {"name": "Capital A/c - Proprietor", "parent_group": "Capital Account", "opening_balance": "-1500000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Drawings A/c", "parent_group": "Capital Account", "opening_balance": "0",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Share Capital", "parent_group": "Capital Account", "opening_balance": "-5000000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Reserves & Surplus", "parent_group": "Capital Account", "opening_balance": "-750000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},

    # ---- Fixed Assets ----
    {"name": "Furniture & Fittings", "parent_group": "Furniture & Fixtures", "opening_balance": "250000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Office Equipment", "parent_group": "Furniture & Fixtures", "opening_balance": "180000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Computers & Laptops", "parent_group": "Computer & IT Equipment", "opening_balance": "450000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Servers & Networking", "parent_group": "Computer & IT Equipment", "opening_balance": "320000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Motor Car", "parent_group": "Vehicles", "opening_balance": "850000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Delivery Van", "parent_group": "Vehicles", "opening_balance": "450000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Building", "parent_group": "Land & Building", "opening_balance": "2500000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Plant & Machinery", "parent_group": "Plant & Machinery", "opening_balance": "1800000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},

    # ---- Loans ----
    {"name": "Term Loan - HDFC", "parent_group": "Secured Loans", "opening_balance": "-2500000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Car Loan - ICICI", "parent_group": "Secured Loans", "opening_balance": "-650000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Director's Loan", "parent_group": "Unsecured Loans", "opening_balance": "-500000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
    {"name": "Partner's Loan", "parent_group": "Unsecured Loans", "opening_balance": "-350000.00",
     "address": "", "state": "", "country": "India", "mobile": "", "gstin": ""},
]

# ============================================================================
# INVENTORY / STOCK ITEMS
# ============================================================================
INVENTORY: List[Dict[str, Any]] = [
    # Electronics
    {"name": "Laptop - Dell Inspiron 15", "parent": "Electronics", "category": "Laptops",
     "baseUnits": "Nos", "openingBalance": 25, "openingValue": 875000, "closingBalance": 18, "closingValue": 630000},
    {"name": "Laptop - HP Pavilion", "parent": "Electronics", "category": "Laptops",
     "baseUnits": "Nos", "openingBalance": 30, "openingValue": 1050000, "closingBalance": 22, "closingValue": 770000},
    {"name": "Laptop - Lenovo ThinkPad", "parent": "Electronics", "category": "Laptops",
     "baseUnits": "Nos", "openingBalance": 20, "openingValue": 900000, "closingBalance": 15, "closingValue": 675000},
    {"name": "Desktop Computer - HP", "parent": "Electronics", "category": "Desktops",
     "baseUnits": "Nos", "openingBalance": 15, "openingValue": 450000, "closingBalance": 10, "closingValue": 300000},
    {"name": "Monitor - LG 24 inch", "parent": "Electronics", "category": "Monitors",
     "baseUnits": "Nos", "openingBalance": 40, "openingValue": 480000, "closingBalance": 28, "closingValue": 336000},
    {"name": "Keyboard - Logitech", "parent": "Electronics", "category": "Accessories",
     "baseUnits": "Nos", "openingBalance": 100, "openingValue": 100000, "closingBalance": 75, "closingValue": 75000},
    {"name": "Mouse - Logitech Wireless", "parent": "Electronics", "category": "Accessories",
     "baseUnits": "Nos", "openingBalance": 150, "openingValue": 75000, "closingBalance": 110, "closingValue": 55000},
    {"name": "Printer - HP LaserJet", "parent": "Electronics", "category": "Printers",
     "baseUnits": "Nos", "openingBalance": 10, "openingValue": 250000, "closingBalance": 7, "closingValue": 175000},

    # Raw Materials
    {"name": "Steel Sheet 2mm", "parent": "Raw Materials", "category": "Steel",
     "baseUnits": "Kg", "openingBalance": 5000, "openingValue": 350000, "closingBalance": 3500, "closingValue": 245000},
    {"name": "Aluminium Sheets", "parent": "Raw Materials", "category": "Aluminium",
     "baseUnits": "Kg", "openingBalance": 2000, "openingValue": 180000, "closingBalance": 1200, "closingValue": 108000},
    {"name": "Copper Wire 1mm", "parent": "Raw Materials", "category": "Copper",
     "baseUnits": "Mtr", "openingBalance": 10000, "openingValue": 500000, "closingBalance": 7500, "closingValue": 375000},
    {"name": "Plastic Granules", "parent": "Raw Materials", "category": "Plastics",
     "baseUnits": "Kg", "openingBalance": 3000, "openingValue": 210000, "closingBalance": 2200, "closingValue": 154000},

    # Office Supplies
    {"name": "A4 Paper Ream", "parent": "Office Supplies", "category": "Paper",
     "baseUnits": "Ream", "openingBalance": 200, "openingValue": 60000, "closingBalance": 150, "closingValue": 45000},
    {"name": "Printer Cartridge - HP", "parent": "Office Supplies", "category": "Cartridges",
     "baseUnits": "Nos", "openingBalance": 50, "openingValue": 125000, "closingBalance": 35, "closingValue": 87500},
    {"name": "Pen - Reynolds", "parent": "Office Supplies", "category": "Stationery",
     "baseUnits": "Box", "openingBalance": 100, "openingValue": 15000, "closingBalance": 65, "closingValue": 9750},
    {"name": "Stapler Pins", "parent": "Office Supplies", "category": "Stationery",
     "baseUnits": "Box", "openingBalance": 200, "openingValue": 10000, "closingBalance": 140, "closingValue": 7000},

    # Furniture
    {"name": "Office Chair - Executive", "parent": "Furniture", "category": "Chairs",
     "baseUnits": "Nos", "openingBalance": 20, "openingValue": 200000, "closingBalance": 12, "closingValue": 120000},
    {"name": "Office Desk - L-Shape", "parent": "Furniture", "category": "Tables",
     "baseUnits": "Nos", "openingBalance": 15, "openingValue": 225000, "closingBalance": 10, "closingValue": 150000},
    {"name": "Filing Cabinet - 4 Drawer", "parent": "Furniture", "category": "Storage",
     "baseUnits": "Nos", "openingBalance": 10, "openingValue": 80000, "closingBalance": 7, "closingValue": 56000},
]

# ============================================================================
# GODOWNS / WAREHOUSES
# ============================================================================
GODOWNS: List[Dict[str, Any]] = [
    {"name": "Main Warehouse", "parent": "", "address": "Plot No. 45, MIDC Andheri", "pincode": "400093"},
    {"name": "Mumbai - Goregaon", "parent": "Main Warehouse", "address": "Unit 12, Goregaon Industrial Area", "pincode": "400063"},
    {"name": "Mumbai - Navi Mumbai", "parent": "Main Warehouse", "address": "Sector 15, Vashi", "pincode": "400703"},
    {"name": "Pune Warehouse", "parent": "", "address": "Plot 78, Chakan MIDC", "pincode": "410501"},
    {"name": "Delhi NCR Warehouse", "parent": "", "address": "Sector 63, Noida", "pincode": "201301"},
    {"name": "Bengaluru Warehouse", "parent": "", "address": "Peenya Industrial Area", "pincode": "560058"},
    {"name": "Chennai Warehouse", "parent": "", "address": "Ambattur Industrial Estate", "pincode": "600058"},
    {"name": "Showroom Stock", "parent": "Main Warehouse", "address": "Ground Floor, Main Office", "pincode": "400001"},
    {"name": "Defective Goods", "parent": "Main Warehouse", "address": "Return Section", "pincode": "400093"},
]

# ============================================================================
# VOUCHER TYPES
# ============================================================================
VOUCHER_TYPES: List[str] = [
    "Receipt",
    "Payment",
    "Contra",
    "Journal",
    "Sales",
    "Purchase",
    "Credit Note",
    "Debit Note",
    "Stock Journal",
    "Physical Stock",
    "Delivery Note",
    "Receipt Note",
    "Sales Order",
    "Purchase Order",
]

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_ledgers_by_company(company: str) -> List[Dict[str, Any]]:
    """Get ledgers - in mock mode, returns same ledgers for all companies"""
    return LEDGERS.copy()

def get_ledgers_by_group(group: str) -> List[Dict[str, Any]]:
    """Get ledgers belonging to a specific group"""
    return [l for l in LEDGERS if l["parent_group"] == group]

def get_bank_ledgers() -> List[Dict[str, Any]]:
    """Get only bank account ledgers"""
    return [l for l in LEDGERS if l["parent_group"] == "Bank Accounts"]

def get_party_ledgers() -> List[Dict[str, Any]]:
    """Get only debtor and creditor ledgers"""
    return [l for l in LEDGERS if l["parent_group"] in ["Sundry Debtors", "Sundry Creditors"]]

def find_ledger(name: str) -> Dict[str, Any] | None:
    """Find a ledger by exact or partial name match"""
    # First try exact match
    for ledger in LEDGERS:
        if ledger["name"].lower() == name.lower():
            return ledger
    # Then try partial match
    for ledger in LEDGERS:
        if name.lower() in ledger["name"].lower():
            return ledger
    return None

def add_ledger(ledger_data: Dict[str, Any]) -> bool:
    """Add a new ledger to the mock data.

    Preserves any extra keys passed by the caller so the mock round-trips
    the full TallyPrime ledger schema (PAN, mailing name, bank details, etc.)
    rather than dropping unknown fields.
    """
    if find_ledger(ledger_data.get("name", "")):
        return False
    record = dict(ledger_data)  # shallow copy keeps every key
    record.setdefault("name", "")
    record.setdefault("parent_group", record.get("parent", "Sundry Debtors"))
    record.setdefault("opening_balance", "0")
    record.setdefault("address", "")
    record.setdefault("state", "")
    record.setdefault("country", "India")
    record.setdefault("mobile", "")
    record.setdefault("gstin", "")
    LEDGERS.append(record)
    return True

def update_ledger(current_name: str, ledger_data: Dict[str, Any]) -> bool:
    """Update an existing ledger"""
    for i, ledger in enumerate(LEDGERS):
        if ledger["name"].lower() == current_name.lower():
            LEDGERS[i] = {
                "name": ledger_data.get("name", ledger_data.get("new_name", current_name)),
                "parent_group": ledger_data.get("parent_group", ledger["parent_group"]),
                "opening_balance": ledger_data.get("opening_balance", ledger["opening_balance"]),
                "address": ledger_data.get("address", ledger["address"]),
                "state": ledger_data.get("state", ledger["state"]),
                "country": ledger_data.get("country", ledger["country"]),
                "mobile": ledger_data.get("mobile", ledger["mobile"]),
                "gstin": ledger_data.get("gstin", ledger["gstin"]),
            }
            return True
    return False

def add_group(group_data: Dict[str, Any]) -> bool:
    """Add a new group to the mock data"""
    for group in GROUPS:
        if group["name"].lower() == group_data.get("name", "").lower():
            return False
    GROUPS.append({
        "name": group_data.get("name", ""),
        "parent": group_data.get("parent", group_data.get("parent_group", "")),
        "isPrimary": False,
        "nature": group_data.get("nature", ""),
    })
    return True

# Track imported vouchers for the session
IMPORTED_VOUCHERS: List[Dict[str, Any]] = []
VOUCHER_COUNTER = 1000

def import_voucher(xml_data: str) -> Dict[str, Any]:
    """Simulate importing a voucher and return result"""
    global VOUCHER_COUNTER

    # Count actual voucher elements in the XML
    voucher_count = xml_data.upper().count("<VOUCHER ")
    if voucher_count == 0:
        voucher_count = xml_data.upper().count("<VOUCHER>")

    altered = 0
    deleted = 0
    exceptions = 0

    # Check for common errors
    if not xml_data or len(xml_data) < 100:
        exceptions = voucher_count or 1
        created = 0
    else:
        created = max(voucher_count, 1)
        VOUCHER_COUNTER += created

    return {
        "created": created,
        "altered": altered,
        "deleted": deleted,
        "exceptions": exceptions,
        "lastVoucherNumber": VOUCHER_COUNTER,
        "error": None if exceptions == 0 else "Invalid XML format"
    }
