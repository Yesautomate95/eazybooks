"""
Hardcoded demo Q&A responses for Ambika Security Service.
Used to return instant answers for known financial queries without calling the AI.

Matching is keyword-based (case-insensitive). The first entry whose keywords
all appear in the user's question wins.
"""

DEMO_QA = [
    # ── Bank balances ─────────────────────────────────────────────────────────
    {
        "keywords": ["closing", "balance", "bank", "december"],
        "answer": (
            "**Closing Bank Balances as on 31st December:**\n\n"
            "| Bank Account | Balance (₹) |\n"
            "|---|---|\n"
            "| HDFC Bank | 11,80,771.75 |\n"
            "| Induslnd Bank Saving A/c | 14,209.07 |\n"
            "| UCO Bank Ankit 3876 Saving A/c | 37,674.93 |\n"
            "| UPI | 9,007.05 |\n\n"
            "**Total: ₹ 12,41,662.80**"
        ),
    },
    {
        "keywords": ["opening", "balance"],
        "answer": (
            "**Opening Balance (01 April 2024):**\n\n"
            "HDFC Bank 74267 — ₹ 7,87,104.87"
        ),
    },
    # ── Receivables / Payables ────────────────────────────────────────────────
    {
        "keywords": ["receivable", "idea", "international"],
        "answer": (
            "**Receivable from Idea International Pvt. Ltd.:**\n\n"
            "₹ 18,26,709.29"
        ),
    },
    {
        "keywords": ["payable", "expert", "facility"],
        "answer": (
            "**Amount Payable to Expert Facility Management:**\n\n"
            "₹ 20,40,229.00"
        ),
    },
    # ── Loans / OD ───────────────────────────────────────────────────────────
    {
        "keywords": ["outstanding", "bank", "od"],
        "answer": (
            "**Outstanding Amount of Bank OD (Overdraft):**\n\n"
            "₹ 69,71,673.23"
        ),
    },
    {
        "keywords": ["outstanding", "overdraft"],
        "answer": (
            "**HDFC Bank OD Account Outstanding:**\n\n"
            "₹ 69,71,673.23\n\n"
            "The overdraft facility is linked to HDFC Bank and is used for short-term operational funding."
        ),
    },
    # ── Salary expense ───────────────────────────────────────────────────────
    {
        "keywords": ["monthly", "salary"],
        "answer": (
            "**Monthly Salary Expense (FY 2024-25):**\n\n"
            "| Month | Cumulative Salary (₹) |\n"
            "|---|---|\n"
            "| April | 71,86,114.42 |\n"
            "| May | 1,24,04,060.42 |\n"
            "| June | 1,80,18,041.42 |\n"
            "| July | 2,38,80,492.42 |\n"
            "| August | 2,98,49,431.42 |\n"
            "| September | 3,58,31,253.42 |\n"
            "| October | 4,47,14,933.42 |\n"
            "| November | 4,88,39,608.42 |\n"
            "| December | 5,61,90,304.42 |\n"
            "| January | 6,06,92,410.42 |\n"
            "| February | 6,65,03,130.42 |\n"
            "| March | 7,21,12,334.42 |\n\n"
            "**Total Annual Salary Expense: ₹ 7,21,12,334.42**"
        ),
    },
    {
        "keywords": ["salary", "april"],
        "answer": (
            "**Salary Expense — April 2024:**\n\n"
            "Total salary paid in April: ₹ 71,86,114.42\n\n"
            "This includes multiple payment vouchers across the month for all staff categories."
        ),
    },
    # ── Basic company / account information ──────────────────────────────────
    {
        "keywords": ["company", "name"],
        "answer": (
            "**Company Name:** Ambika Security Service\n\n"
            "This is the entity for which the books of accounts and bank reconciliation are being maintained."
        ),
    },
    {
        "keywords": ["bank", "account", "number"],
        "answer": (
            "**Bank Account Details:**\n\n"
            "| Bank | Account | Type |\n"
            "|---|---|---|\n"
            "| HDFC Bank | 74267 | Current Account |\n"
            "| Induslnd Bank | 9933 | Saving Account |\n"
            "| UCO Bank (Ankit) | 3876 | Saving Account |"
        ),
    },
    {
        "keywords": ["financial", "year"],
        "answer": (
            "**Current Financial Year:** FY 2024-25 (April 2024 – March 2025)\n\n"
            "Books maintained under Indian GAAP / Tally ERP for Ambika Security Service."
        ),
    },
    {
        "keywords": ["total", "transactions", "april"],
        "answer": (
            "**Total Transactions — April 2024:**\n\n"
            "- Bank statement entries: **142**\n"
            "- Tally ledger entries: **142**\n"
            "- All entries fully reconciled\n\n"
            "Opening balance: ₹ 7,87,104.87 | Closing balance: ₹ 4,54,583.67"
        ),
    },
    {
        "keywords": ["epf", "provident", "fund"],
        "answer": (
            "**EPF (Employee Provident Fund) Payments — April 2024:**\n\n"
            "| Date | Amount (₹) |\n"
            "|---|---|\n"
            "| 02 April | 70,476.00 |\n"
            "| 05 April | 1,16,657.00 |\n"
            "| 06 April | 4,08,767.00 |\n"
            "| 18 April | 1,65,133.00 |\n"
            "| 26 April | 4,02,015.00 |\n"
            "| 29 April | 4,09,343.00 |\n\n"
            "**Total EPF paid in April: ₹ 15,72,391.00**"
        ),
    },
    {
        "keywords": ["gst", "paid"],
        "answer": (
            "**GST Payment — April 2024:**\n\n"
            "GST paid on 01 April 2024: ₹ 3,48,144.00 (Payment Voucher No. 3)\n\n"
            "This was processed via HDFC Bank NEFT."
        ),
    },
    {
        "keywords": ["sprng", "akshaya"],
        "answer": (
            "**Receipts from SPRNG AKSHAYA URJA PVT LTD — April 2024:**\n\n"
            "| Date | Voucher No. | Amount (₹) |\n"
            "|---|---|---|\n"
            "| 04 April | Receipt 2 | 1,56,069.00 |\n"
            "| 16 April | Receipt 30 | 3,38,251.00 |\n"
            "| 16 April | Receipt 31 | 1,53,482.00 |\n"
            "| 16 April | Receipt 32 | 85,534.00 |\n\n"
            "**Total received: ₹ 7,33,336.00**"
        ),
    },
    {
        "keywords": ["param", "renewable"],
        "answer": (
            "**Receipts from Param Renewable Energy Pvt. Ltd. — April 2024:**\n\n"
            "| Date | Party | Amount (₹) |\n"
            "|---|---|---|\n"
            "| 05 April | Param Renewable Energy | 20,00,000.00 |\n"
            "| 26 April | Param Renewable Energy | 20,51,921.00 |\n"
            "| 29 April | Param Renewable Energy (Hero Plant) | 9,00,000.00 |\n\n"
            "**Total received: ₹ 49,51,921.00**"
        ),
    },
    {
        "keywords": ["credit", "card", "payment"],
        "answer": (
            "**CREDIT CARD Payments — April 2024:**\n\n"
            "| Date | Voucher No. | Amount (₹) |\n"
            "|---|---|---|\n"
            "| 01 April | Payment 4 | 11,000.00 |\n"
            "| 03 April | Payment 8 | 9,997.00 |\n"
            "| 06 April | Payment 12 | 5,471.18 |\n"
            "| 17 April | Payment 40 | 7,097.00 |\n"
            "| 23 April | Payment 63 | 5,489.00 |\n"
            "| 27 April | Payment 84 | 3,495.00 |\n\n"
            "**Total credit card payments: ₹ 42,549.18**"
        ),
    },
    {
        "keywords": ["induslnd", "contra"],
        "answer": (
            "**Induslnd Bank Saving A/c 9933 — Inter-bank Transfers (April 2024):**\n\n"
            "| Date | Type | Voucher | Amount (₹) |\n"
            "|---|---|---|---|\n"
            "| 03 April | By (Payment out) | Contra 11 | 45,000.00 |\n"
            "| 13 April | To (Receipt in) | Contra 47 | 35,000.00 |\n"
            "| 17 April | By (Payment out) | Contra 12 | 45,000.00 |\n"
            "| 18 April | By (Payment out) | Contra 13 | 65,000.00 |\n"
            "| 20 April | By (Payment out) | Contra 14 | 40,000.00 |\n"
            "| 27 April | By (Payment out) | Contra 15 | 45,000.00 |\n"
            "| 29 April | By (Payment out) | Contra 16 | 40,000.00 |"
        ),
    },
    {
        "keywords": ["reconciliation", "status"],
        "answer": (
            "**Bank Reconciliation Status — April 2024:**\n\n"
            "- Period: 01 April 2024 to 30 April 2024\n"
            "- Bank: HDFC Bank 74267\n"
            "- Total entries matched: **142 / 142** ✓\n"
            "- Unmatched entries: **0**\n"
            "- Opening balance: ₹ 7,87,104.87\n"
            "- Closing balance: ₹ 4,54,583.67\n\n"
            "Reconciliation is fully complete with no outstanding differences."
        ),
    },
    {
        "keywords": ["closing", "balance", "april"],
        "answer": (
            "**Closing Balance — April 2024:**\n\n"
            "HDFC Bank 74267: ₹ 4,54,583.67 (as on 30 April 2024)"
        ),
    },
    {
        "keywords": ["total", "receipt", "april"],
        "answer": (
            "**Total Receipts — April 2024:**\n\n"
            "All 'To' entries (deposits into HDFC Bank 74267) in April 2024 represent client payments, "
            "contra transfers, and other inflows. Key receipts include Param Renewable Energy "
            "(₹ 49,51,921), Idea International (₹ 18,26,073), SPRNG AKSHAYA URJA (₹ 7,33,336), "
            "and others."
        ),
    },
    {
        "keywords": ["bank", "charge"],
        "answer": (
            "**Bank Charges — April 2024:**\n\n"
            "| Date | Voucher No. | Amount (₹) |\n"
            "|---|---|---|\n"
            "| 18 April | Payment 45 | 5.90 |\n"
            "| 29 April | Payment 88 | 5.90 |\n"
            "| 29 April | Payment 90 | 5.90 |\n"
            "| 29 April | Payment 91 | 5.90 |\n"
            "| 29 April | Payment 92 | 5.90 |\n\n"
            "**Total bank charges: ₹ 29.50**"
        ),
    },
    {
        "keywords": ["annual", "salary"],
        "answer": (
            "**Total Annual Salary Expense (FY 2024-25):**\n\n"
            "₹ 7,21,12,334.42\n\n"
            "This covers all salary disbursements from April 2024 to March 2025 "
            "across all staff categories."
        ),
    },
    {
        "keywords": ["salary", "may"],
        "answer": (
            "**Salary Expense — May 2024:**\n\n"
            "Monthly salary for May: ₹ 52,17,946.00\n\n"
            "Cumulative salary (Apr–May): ₹ 1,24,04,060.42"
        ),
    },
    {
        "keywords": ["hdfc", "bank", "balance"],
        "answer": (
            "**HDFC Bank 74267 — Balance Summary:**\n\n"
            "| Period | Balance (₹) |\n"
            "|---|---|\n"
            "| Opening (01 April 2024) | 7,87,104.87 |\n"
            "| Closing (30 April 2024) | 4,54,543.67 |\n"
            "| Closing (31 December 2024) | 11,80,771.75 |"
        ),
    },
    {
        "keywords": ["total", "expenses", "april"],
        "answer": (
            "**Total Expenses — April 2024 (HDFC Bank 74267):**\n\n"
            "- Salary disbursements: ₹ 71,86,114.42\n"
            "- EPF payments: ₹ 15,72,391.00\n"
            "- GST paid: ₹ 3,48,144.00\n"
            "- HDFC OD repayments: ₹ 1,62,125.00\n"
            "- Expert Facility Management: ₹ 3,40,000.00\n"
            "- Other operational expenses: various\n\n"
            "Opening balance: ₹ 7,87,104.87 | Closing balance: ₹ 4,54,543.67"
        ),
    },
    {
        "keywords": ["salary", "december"],
        "answer": (
            "**Salary Expense — December 2024:**\n\n"
            "Monthly salary for December: ₹ 73,50,696.00\n\n"
            "Cumulative salary (Apr–Dec): ₹ 5,61,90,304.42"
        ),
    },
    {
        "keywords": ["ledger", "balance", "outstanding"],
        "answer": (
            "**Key Ledger Balances (as on 31 December 2024):**\n\n"
            "| Party | Outstanding (₹) |\n"
            "|---|---|\n"
            "| Idea International Pvt. Ltd. (Receivable) | 18,26,709.29 |\n"
            "| Expert Facility Management (Payable) | 20,40,229.00 |\n"
            "| HDFC Bank OD A/c (Payable) | 69,71,673.23 |"
        ),
    },
]


def find_demo_answer(question: str) -> str | None:
    """
    Returns a hardcoded answer if the question matches a known Q&A entry,
    otherwise returns None (caller should proceed with AI).
    """
    q = question.lower()
    for entry in DEMO_QA:
        if all(kw in q for kw in entry["keywords"]):
            return entry["answer"]
    return None
