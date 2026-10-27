"""
Bank Reconciliation core logic.

Responsibilities:
  - Parse bank statement files (PDF / Excel) extracting OB, CB, and transaction rows
    with separate debit / credit columns.
  - Run the automatic matching algorithm (Phase 1: exact date + description + cross-amount).
  - Provide helper utilities used by bank_recon_service.
"""

import re
import difflib
import logging
import io
import asyncio
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes (plain dicts kept for simplicity; typed for documentation)
# ---------------------------------------------------------------------------
# BankStatementRow: {date, description, reference, debit_amount, credit_amount, is_opening_balance, is_closing_balance, raw}
# LedgerRow:        {date, description, reference, debit_amount, credit_amount, voucher_type, voucher_number, tally_guid}
# MatchResult:      {ledger_idx, bank_idx, confidence, reason}


# ---------------------------------------------------------------------------
# Description normalisation
# ---------------------------------------------------------------------------

def normalize_description(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', str(text).lower().strip())


# ---------------------------------------------------------------------------
# Amount helpers
# ---------------------------------------------------------------------------

def _to_decimal(value) -> Optional[Decimal]:
    """Safely convert a value to Decimal, returning None on failure."""
    if value is None:
        return None
    try:
        cleaned = str(value).replace(',', '').replace(' ', '').strip()
        if not cleaned or cleaned in ('-', '—', ''):
            return None
        d = Decimal(cleaned)
        return d if d > 0 else None
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value) -> Optional[date]:
    """Try multiple date formats and return a date object or None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value if not isinstance(value, datetime) else value.date()
    if isinstance(value, datetime):
        return value.date()

    s = str(value).strip()
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y',
                '%Y-%m-%d', '%d %b %Y', '%d %B %Y', '%d %b %y',
                '%d-%b-%Y', '%d-%b-%y', '%b %d, %Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Bank Statement Parser
# ---------------------------------------------------------------------------

def parse_bank_statement_for_recon(
    file_content: bytes,
    file_name: str,
) -> Dict[str, Any]:
    """
    Parse a bank statement (PDF or Excel) for reconciliation purposes.

    Returns a dict with:
      opening_balance: Decimal | None
      closing_balance: Decimal | None
      transactions: List[dict]  — each has date, description, reference,
                                   debit_amount, credit_amount (all as Decimal | None)
    """
    file_name_lower = file_name.lower()
    if file_name_lower.endswith('.pdf'):
        return _parse_pdf_statement(file_content, file_name)
    elif file_name_lower.endswith(('.xlsx', '.xls')):
        return _parse_excel_statement(file_content, file_name)
    else:
        raise ValueError(f"Unsupported file type: {file_name}. Supported: PDF, XLSX, XLS")


def _parse_excel_statement(file_content: bytes, file_name: str) -> Dict[str, Any]:
    """Parse an Excel bank statement."""
    try:
        import openpyxl
        import xlrd
    except ImportError:
        try:
            import openpyxl
        except ImportError:
            raise ImportError("openpyxl is required for Excel parsing. Install with: pip install openpyxl")

    result = {
        "opening_balance": None,
        "closing_balance": None,
        "transactions": [],
    }

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception:
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=file_content)
            ws = wb.sheet_by_index(0)
            rows = [ws.row_values(r) for r in range(ws.nrows)]
        except Exception as e:
            logger.error(f"Failed to parse Excel file {file_name}: {e}")
            return result

    # Detect header row: look for keywords like date, description, debit, credit
    header_row_idx = None
    header_map = {}
    for i, row in enumerate(rows):
        row_lower = [str(c).lower().strip() if c is not None else '' for c in row]
        has_date = any('date' in c for c in row_lower)
        has_amount = any(k in c for c in row_lower for k in ('debit', 'credit', 'withdrawal', 'deposit', 'amount'))
        if has_date and has_amount:
            header_row_idx = i
            for j, cell in enumerate(row_lower):
                if 'date' in cell:
                    header_map['date'] = j
                elif any(k in cell for k in ('narration', 'description', 'particulars', 'details', 'remarks')):
                    header_map['description'] = j
                elif any(k in cell for k in ('ref', 'chq', 'cheque', 'transaction id', 'txn id')):
                    header_map['reference'] = j
                elif any(k in cell for k in ('debit', 'withdrawal', 'dr')):
                    header_map['debit'] = j
                elif any(k in cell for k in ('credit', 'deposit', 'cr')):
                    header_map['credit'] = j
                elif 'balance' in cell:
                    header_map['balance'] = j
            break

    if header_row_idx is None:
        logger.warning(f"Could not detect header row in Excel file {file_name}")
        return result

    opening_bal = None
    closing_bal = None
    transactions = []

    for row in rows[header_row_idx + 1:]:
        if not any(c is not None and str(c).strip() for c in row):
            continue

        raw_date_val = row[header_map['date']] if 'date' in header_map else None
        desc_val = row[header_map['description']] if 'description' in header_map else None
        ref_val = row[header_map.get('reference', -1)] if 'reference' in header_map else None
        debit_val = row[header_map['debit']] if 'debit' in header_map else None
        credit_val = row[header_map['credit']] if 'credit' in header_map else None
        balance_val = row[header_map.get('balance', -1)] if 'balance' in header_map else None

        desc_str = str(desc_val).strip().lower() if desc_val else ''

        # Detect opening/closing balance rows
        is_opening = any(k in desc_str for k in ('opening balance', 'balance b/f', 'b/f', 'brought forward', 'opening bal'))
        is_closing = any(k in desc_str for k in ('closing balance', 'balance c/f', 'c/f', 'carried forward', 'closing bal'))

        if is_opening and balance_val is not None:
            opening_bal = _to_decimal(balance_val)
            continue
        if is_closing and balance_val is not None:
            closing_bal = _to_decimal(balance_val)
            continue

        txn_date = _parse_date(raw_date_val)
        if not txn_date and not (debit_val or credit_val):
            continue

        debit = _to_decimal(debit_val)
        credit = _to_decimal(credit_val)

        if not txn_date and not debit and not credit:
            continue

        transactions.append({
            "date": txn_date,
            "description": str(desc_val).strip() if desc_val else "",
            "reference": str(ref_val).strip() if ref_val else "",
            "debit_amount": debit,
            "credit_amount": credit,
        })

    # If closing balance not found in rows, try to infer from last balance column value
    if closing_bal is None and 'balance' in header_map and rows:
        for row in reversed(rows[header_row_idx + 1:]):
            bal = _to_decimal(row[header_map['balance']]) if header_map['balance'] < len(row) else None
            if bal is not None:
                closing_bal = bal
                break

    result["opening_balance"] = opening_bal
    result["closing_balance"] = closing_bal
    result["transactions"] = transactions
    return result


def _parse_pdf_statement(file_content: bytes, file_name: str) -> Dict[str, Any]:
    """
    Parse a PDF bank statement.
    Uses pdfplumber first (table extraction), falls back to pdfminer text extraction
    and then delegates to the existing AI-based bank_entries extractor.
    """
    result = {
        "opening_balance": None,
        "closing_balance": None,
        "transactions": [],
    }

    extracted_text = ""
    tables_found = False

    # Attempt structured table extraction via pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_content)) as pdf:
            all_rows = []
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if table:
                        all_rows.extend(table)
                        tables_found = True
                # Also gather text for balance detection
                page_text = page.extract_text()
                if page_text:
                    extracted_text += page_text + "\n"

            if tables_found and all_rows:
                parsed = _parse_rows_as_statement(all_rows)
                if parsed["transactions"]:
                    result.update(parsed)
                    # Try to detect OB/CB from raw text if not found in tables
                    if result["opening_balance"] is None or result["closing_balance"] is None:
                        ob, cb = _extract_balances_from_text(extracted_text)
                        if result["opening_balance"] is None:
                            result["opening_balance"] = ob
                        if result["closing_balance"] is None:
                            result["closing_balance"] = cb
                    return result
    except ImportError:
        logger.warning("pdfplumber not installed; falling back to text extraction")
    except Exception as e:
        logger.warning(f"pdfplumber table extraction failed: {e}")

    # Fallback: extract raw text and use AI-based extraction
    if not extracted_text:
        try:
            import pdfminer.high_level
            extracted_text = pdfminer.high_level.extract_text(io.BytesIO(file_content))
        except Exception:
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(io.BytesIO(file_content))
                extracted_text = "\n".join(
                    page.extract_text() or "" for page in reader.pages
                )
            except Exception as e:
                logger.error(f"Could not extract text from PDF {file_name}: {e}")
                return result

    # Detect balances from text
    ob, cb = _extract_balances_from_text(extracted_text)
    result["opening_balance"] = ob
    result["closing_balance"] = cb

    # Parse text line-by-line for transactions
    transactions = _parse_text_transactions(extracted_text)
    result["transactions"] = transactions
    return result


def _parse_rows_as_statement(rows: List[List]) -> Dict[str, Any]:
    """Detect header in raw table rows and extract transactions."""
    result = {"opening_balance": None, "closing_balance": None, "transactions": []}

    header_idx = None
    header_map = {}
    for i, row in enumerate(rows):
        if not row:
            continue
        row_lower = [str(c).lower().strip() if c else '' for c in row]
        if any('date' in c for c in row_lower) and any(
            k in c for c in row_lower for k in ('debit', 'credit', 'withdrawal', 'deposit', 'amount')
        ):
            header_idx = i
            for j, cell in enumerate(row_lower):
                if 'date' in cell and 'date' not in header_map:
                    header_map['date'] = j
                elif any(k in cell for k in ('narration', 'description', 'particulars', 'details')):
                    header_map['description'] = j
                elif any(k in cell for k in ('ref', 'chq', 'cheque', 'txn')):
                    header_map['reference'] = j
                elif any(k in cell for k in ('debit', 'withdrawal', 'dr')):
                    header_map['debit'] = j
                elif any(k in cell for k in ('credit', 'deposit', 'cr')):
                    header_map['credit'] = j
                elif 'balance' in cell:
                    header_map['balance'] = j
            break

    if header_idx is None:
        return result

    for row in rows[header_idx + 1:]:
        if not row or not any(c for c in row):
            continue

        def _get(col):
            idx = header_map.get(col)
            return row[idx] if idx is not None and idx < len(row) else None

        desc = str(_get('description') or '').strip()
        desc_lower = desc.lower()

        is_opening = any(k in desc_lower for k in ('opening balance', 'balance b/f', 'b/f', 'brought forward'))
        is_closing = any(k in desc_lower for k in ('closing balance', 'balance c/f', 'c/f', 'carried forward'))

        balance_val = _to_decimal(_get('balance'))
        if is_opening and balance_val:
            result["opening_balance"] = balance_val
            continue
        if is_closing and balance_val:
            result["closing_balance"] = balance_val
            continue

        txn_date = _parse_date(_get('date'))
        debit = _to_decimal(_get('debit'))
        credit = _to_decimal(_get('credit'))

        if not txn_date and not debit and not credit:
            continue

        result["transactions"].append({
            "date": txn_date,
            "description": desc,
            "reference": str(_get('reference') or '').strip(),
            "debit_amount": debit,
            "credit_amount": credit,
        })

    return result


_BALANCE_PATTERNS = [
    r'opening\s+bal(?:ance)?\s*[:\-]?\s*([\d,]+\.?\d*)',
    r'balance\s+b[/\-]f\s*[:\-]?\s*([\d,]+\.?\d*)',
    r'brought\s+forward\s*[:\-]?\s*([\d,]+\.?\d*)',
]
_CLOSING_PATTERNS = [
    r'closing\s+bal(?:ance)?\s*[:\-]?\s*([\d,]+\.?\d*)',
    r'balance\s+c[/\-]f\s*[:\-]?\s*([\d,]+\.?\d*)',
    r'carried\s+forward\s*[:\-]?\s*([\d,]+\.?\d*)',
]


def _extract_balances_from_text(text: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    ob, cb = None, None
    text_lower = text.lower()
    for pat in _BALANCE_PATTERNS:
        m = re.search(pat, text_lower)
        if m:
            ob = _to_decimal(m.group(1))
            if ob:
                break
    for pat in _CLOSING_PATTERNS:
        m = re.search(pat, text_lower)
        if m:
            cb = _to_decimal(m.group(1))
            if cb:
                break
    return ob, cb


def _parse_text_transactions(text: str) -> List[Dict]:
    """
    Lightweight text-based transaction extractor.
    Looks for lines matching: date  description  amount  amount  balance
    Returns whatever can be extracted; not exhaustive.
    """
    transactions = []
    date_pattern = re.compile(
        r'\b(\d{2}[/\-]\d{2}[/\-]\d{4}|\d{2}[/\-]\d{2}[/\-]\d{2}|\d{2}\s+\w{3}\s+\d{4})\b'
    )
    amount_pattern = re.compile(r'([\d,]+\.\d{2})')

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        line_lower = line.lower()
        if any(k in line_lower for k in ('opening balance', 'closing balance', 'balance b/f', 'balance c/f', 'page', 'statement')):
            continue

        date_match = date_pattern.search(line)
        if not date_match:
            continue

        txn_date = _parse_date(date_match.group(0))
        if not txn_date:
            continue

        amounts = amount_pattern.findall(line)
        if not amounts:
            continue

        # Heuristic: if ≥2 amounts found, first is debit or credit, second (or last) is balance
        debit, credit = None, None
        if len(amounts) >= 2:
            # We can't reliably tell debit vs credit from text alone without column positions
            # Treat as unknown direction; service layer can handle
            debit = _to_decimal(amounts[0])
        elif len(amounts) == 1:
            debit = _to_decimal(amounts[0])

        # Strip amounts and date from line to get description
        desc = date_pattern.sub('', line)
        for amt in amounts:
            desc = desc.replace(amt, '')
        desc = re.sub(r'\s+', ' ', desc).strip(' ,-/')

        transactions.append({
            "date": txn_date,
            "description": desc,
            "reference": "",
            "debit_amount": debit,
            "credit_amount": credit,
        })

    return transactions


# ---------------------------------------------------------------------------
# Automatic Matching Algorithm (Phase 1 — Exact Match)
# ---------------------------------------------------------------------------

def run_automatic_matching(
    ledger_transactions: List[Dict],
    bank_transactions: List[Dict],
) -> List[Dict]:
    """
    Two-phase automatic matching:

    Phase 1 — Exact match: date + normalized description + cross-amount (confidence 1.0)
    Phase 2 — Date + amount match: same date + same amount, descriptions differ (confidence 0.9)

    Matching rule (both phases):
      bank CREDIT ↔ ledger DEBIT  (deposit / receipt)
      bank DEBIT  ↔ ledger CREDIT (withdrawal / payment)
    """
    from collections import defaultdict

    matches = []
    matched_ledger: set = set()
    matched_bank: set = set()

    # ── Phase 1: exact date + description + amount ────────────────────────────
    bank_credit_lookup: Dict[Tuple, List[int]] = defaultdict(list)
    bank_debit_lookup:  Dict[Tuple, List[int]] = defaultdict(list)

    for j, btxn in enumerate(bank_transactions):
        d    = btxn.get("date")
        desc = normalize_description(btxn.get("description", ""))
        if btxn.get("credit_amount"):
            bank_credit_lookup[(d, desc, btxn["credit_amount"])].append(j)
        if btxn.get("debit_amount"):
            bank_debit_lookup[(d, desc, btxn["debit_amount"])].append(j)

    for i, ltxn in enumerate(ledger_transactions):
        d    = ltxn.get("date")
        desc = normalize_description(ltxn.get("description", ""))

        if ltxn.get("debit_amount"):
            candidates = [j for j in bank_credit_lookup.get((d, desc, ltxn["debit_amount"]), []) if j not in matched_bank]
            if candidates:
                j = candidates[0]
                matches.append({"ledger_idx": i, "bank_idx": j, "confidence": 1.000,
                                 "reason": "Exact: date + description + amount (ledger Dr / bank Cr)"})
                matched_ledger.add(i); matched_bank.add(j); continue

        if ltxn.get("credit_amount"):
            candidates = [j for j in bank_debit_lookup.get((d, desc, ltxn["credit_amount"]), []) if j not in matched_bank]
            if candidates:
                j = candidates[0]
                matches.append({"ledger_idx": i, "bank_idx": j, "confidence": 1.000,
                                 "reason": "Exact: date + description + amount (ledger Cr / bank Dr)"})
                matched_ledger.add(i); matched_bank.add(j)

    # ── Phase 2: date + amount only (descriptions differ) ────────────────────
    bank_credit_amt_lookup: Dict[Tuple, List[int]] = defaultdict(list)
    bank_debit_amt_lookup:  Dict[Tuple, List[int]] = defaultdict(list)

    for j, btxn in enumerate(bank_transactions):
        if j in matched_bank:
            continue
        d = btxn.get("date")
        if btxn.get("credit_amount"):
            bank_credit_amt_lookup[(d, btxn["credit_amount"])].append(j)
        if btxn.get("debit_amount"):
            bank_debit_amt_lookup[(d, btxn["debit_amount"])].append(j)

    for i, ltxn in enumerate(ledger_transactions):
        if i in matched_ledger:
            continue
        d = ltxn.get("date")

        if ltxn.get("debit_amount"):
            candidates = [j for j in bank_credit_amt_lookup.get((d, ltxn["debit_amount"]), []) if j not in matched_bank]
            if candidates:
                j = candidates[0]
                matches.append({"ledger_idx": i, "bank_idx": j, "confidence": 0.900,
                                 "reason": "Date + amount match (descriptions differ)"})
                matched_ledger.add(i); matched_bank.add(j); continue

        if ltxn.get("credit_amount"):
            candidates = [j for j in bank_debit_amt_lookup.get((d, ltxn["credit_amount"]), []) if j not in matched_bank]
            if candidates:
                j = candidates[0]
                matches.append({"ledger_idx": i, "bank_idx": j, "confidence": 0.900,
                                 "reason": "Date + amount match (descriptions differ)"})
                matched_ledger.add(i); matched_bank.add(j)

    logger.info(
        f"Matching complete: {len(matches)} automatic matches found "
        f"from {len(ledger_transactions)} ledger + {len(bank_transactions)} bank transactions"
    )
    return matches


# ---------------------------------------------------------------------------
# Group / aggregate matching (QB recon "smart" matcher)
# ---------------------------------------------------------------------------
#
# Unlike run_automatic_matching (1:1, exact date only), run_group_matching adds:
#   * reference / transaction-ID matching (strongest signal)
#   * a date-proximity WINDOW instead of exact-date-only
#   * fuzzy description similarity
#   * ONE-TO-MANY aggregation (1 bank txn  <->  sum of several book txns, & reverse)
#   * contra / offset detection (equal-and-opposite pair on one side, nets to 0)
#   * confidence TIERS: 'auto' (auto-reconciled) vs 'suggest' (needs review)
#
# It returns GROUPS, not pairs:
#   {"book_idxs": [int...], "bank_idxs": [int...], "confidence": float,
#    "tier": "auto"|"suggest", "reason": str}
#
# Sign convention is identical to run_automatic_matching:
#   bank CREDIT  <->  ledger DEBIT   (money in)
#   bank DEBIT   <->  ledger CREDIT  (money out)
#
# This is additive — run_automatic_matching is untouched, so Tally/Zoho/Xero/SAP
# are unaffected. Only the QB recon service is wired to this one.

GROUP_MATCH_CONFIG: Dict[str, Any] = {
    "date_window_days": 4,        # how far apart bank/book dates may be
    "auto_threshold": 0.90,       # confidence >= this -> auto-reconciled (tier 'auto')
    "suggest_floor": 0.60,        # below this -> not matched (left for manual)
    "amount_tolerance": Decimal("0.01"),
    "max_aggregate_subset": 5,    # cap members on the "many" side of an aggregate
    "max_aggregate_pool": 25,     # skip aggregation if the candidate pool is larger
    "subset_node_budget": 20000,  # DFS node cap per aggregation target (safety)
    "contra_min_similarity": 0.50,
}


def _desc_similarity(a: Optional[str], b: Optional[str]) -> float:
    """0..1 fuzzy similarity between two descriptions (normalized)."""
    a = normalize_description(a or "")
    b = normalize_description(b or "")
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _date_gap_days(d1: Optional[date], d2: Optional[date]) -> Optional[int]:
    if not d1 or not d2:
        return None
    return abs((d1 - d2).days)


def _norm_ref(ref: Any) -> str:
    if ref is None:
        return ""
    return re.sub(r'\s+', '', str(ref).lower())


def _subset_sum(
    target: Decimal,
    items: List[Tuple[int, Decimal]],
    tol: Decimal,
    max_size: int,
    budget: int,
) -> List[int]:
    """Find a subset (>= 2 items) of `items` whose amounts sum to `target` within
    `tol`. Bounded DFS with a node budget so a big pool can't blow up. Returns the
    chosen original indices, or [] if none found."""
    items = sorted(items, key=lambda t: t[1], reverse=True)
    n = len(items)
    found: List[int] = []
    nodes = [0]

    def dfs(start: int, remaining: Decimal, chosen: List[int]) -> bool:
        if abs(remaining) <= tol and len(chosen) >= 2:
            found.extend(chosen)
            return True
        if len(chosen) >= max_size:
            return False
        for k in range(start, n):
            nodes[0] += 1
            if nodes[0] > budget:
                return False
            amt = items[k][1]
            if amt - tol > remaining:
                continue  # too big for what's left; try smaller ones
            if dfs(k + 1, remaining - amt, chosen + [items[k][0]]):
                return True
        return False

    dfs(0, target, [])
    return found


def run_group_matching(
    ledger_transactions: List[Dict],
    bank_transactions: List[Dict],
    *,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict]:
    """Smart group matcher. See module comment above for the algorithm. Returns a
    list of group dicts; greedy, each pass consumes what earlier passes matched."""
    from collections import defaultdict

    cfg = {**GROUP_MATCH_CONFIG, **(config or {})}
    window = cfg["date_window_days"]
    tol: Decimal = cfg["amount_tolerance"]
    auto_t = cfg["auto_threshold"]
    floor = cfg["suggest_floor"]

    L = ledger_transactions or []
    B = bank_transactions or []
    if not L or not B:
        return []

    groups: List[Dict] = []
    matched_l: set = set()
    matched_b: set = set()

    def q(d: Optional[Decimal]) -> Optional[Decimal]:
        return d.quantize(Decimal("0.01")) if d is not None else None

    def ld(i):
        return L[i].get("debit_amount")

    def lc(i):
        return L[i].get("credit_amount")

    def bd(j):
        return B[j].get("debit_amount")

    def bc(j):
        return B[j].get("credit_amount")

    def cross_amount_equal(i: int, j: int) -> bool:
        if ld(i) and bc(j) and abs(ld(i) - bc(j)) <= tol:
            return True
        if lc(i) and bd(j) and abs(lc(i) - bd(j)) <= tol:
            return True
        return False

    def commit(book_idxs, bank_idxs, confidence, reason):
        confidence = min(1.0, max(0.0, float(confidence)))
        groups.append({
            "book_idxs": list(book_idxs),
            "bank_idxs": list(bank_idxs),
            "confidence": round(confidence, 3),
            "tier": "auto" if confidence >= auto_t else "suggest",
            "reason": reason,
        })
        for i in book_idxs:
            matched_l.add(i)
        for j in bank_idxs:
            matched_b.add(j)

    # ── Pass A: reference / transaction-ID exact (+ amount) → confidence 1.0 ──
    bank_by_ref: Dict[str, List[int]] = defaultdict(list)
    for j, b in enumerate(B):
        r = _norm_ref(b.get("reference"))
        if r:
            bank_by_ref[r].append(j)

    for i, l in enumerate(L):
        if i in matched_l:
            continue
        r = _norm_ref(l.get("reference"))
        if not r:
            continue
        for j in bank_by_ref.get(r, []):
            if j in matched_b:
                continue
            if cross_amount_equal(i, j):
                commit([i], [j], 1.0, "Reference / transaction-ID match")
                break

    # ── Pass B: scored 1:1 (cross-amount + date window + fuzzy description) ──
    bank_credit_by_amt: Dict[Decimal, List[int]] = defaultdict(list)
    bank_debit_by_amt: Dict[Decimal, List[int]] = defaultdict(list)
    for j, b in enumerate(B):
        if bc(j):
            bank_credit_by_amt[q(bc(j))].append(j)
        if bd(j):
            bank_debit_by_amt[q(bd(j))].append(j)

    def score_pair(i: int, j: int) -> Optional[float]:
        # Exact cross-amount equality is a precondition and the strongest signal,
        # so it anchors the score; date proximity is the next-strongest evidence;
        # fuzzy description is only a tie-breaking bonus. This lets a same-date,
        # exact-amount pair clear the auto threshold even when QBO's memo doesn't
        # resemble the bank narration (which is the common case).
        if not cross_amount_equal(i, j):
            return None
        gap = _date_gap_days(L[i].get("date"), B[j].get("date"))
        if gap is None or gap > window:
            return None
        date_score = 1.0 - (gap / (window + 1))
        sim = _desc_similarity(L[i].get("description"), B[j].get("description"))
        return 0.70 + 0.20 * date_score + 0.10 * sim

    def reason_pair(i: int, j: int) -> str:
        gap = _date_gap_days(L[i].get("date"), B[j].get("date")) or 0
        sim = _desc_similarity(L[i].get("description"), B[j].get("description"))
        bits = ["amount", "same date" if gap == 0 else f"date ±{gap}d"]
        if sim >= 0.8:
            bits.append("description")
        elif sim >= 0.4:
            bits.append("similar description")
        return "Matched on " + ", ".join(bits)

    candidates: List[Tuple[float, int, int]] = []
    for i, l in enumerate(L):
        cand_js: List[int] = []
        if ld(i):
            cand_js += bank_credit_by_amt.get(q(ld(i)), [])
        if lc(i):
            cand_js += bank_debit_by_amt.get(q(lc(i)), [])
        for j in cand_js:
            sc = score_pair(i, j)
            if sc is not None and sc >= floor:
                candidates.append((sc, i, j))

    candidates.sort(key=lambda x: x[0], reverse=True)
    for sc, i, j in candidates:
        if i in matched_l or j in matched_b:
            continue
        commit([i], [j], sc, reason_pair(i, j))

    # ── Pass C: one-to-many aggregation (sum match), both directions ──
    max_subset = cfg["max_aggregate_subset"]
    max_pool = cfg["max_aggregate_pool"]
    budget = cfg["subset_node_budget"]

    # C1: one BANK txn  <->  many BOOK txns
    for j in range(len(B)):
        if j in matched_b:
            continue
        bdate = B[j].get("date")
        # bank credit pairs with book debits; bank debit pairs with book credits
        if bc(j):
            target, want = bc(j), "debit"
        elif bd(j):
            target, want = bd(j), "credit"
        else:
            continue
        pool = []
        for i in range(len(L)):
            if i in matched_l:
                continue
            amt = ld(i) if want == "debit" else lc(i)
            if not amt:
                continue
            gap = _date_gap_days(L[i].get("date"), bdate)
            if gap is None or gap > window:
                continue
            if amt - tol > target:
                continue
            pool.append((i, amt))
        if len(pool) < 2 or len(pool) > max_pool:
            continue
        subset = _subset_sum(target, pool, tol, max_subset, budget)
        if subset:
            commit(subset, [j], 0.72, f"Aggregate: 1 bank ↔ {len(subset)} book (sum match)")

    # C2: one BOOK txn  <->  many BANK txns
    for i in range(len(L)):
        if i in matched_l:
            continue
        ldate = L[i].get("date")
        if ld(i):
            target, want = ld(i), "credit"   # book debit pairs with bank credits
        elif lc(i):
            target, want = lc(i), "debit"    # book credit pairs with bank debits
        else:
            continue
        pool = []
        for j in range(len(B)):
            if j in matched_b:
                continue
            amt = bc(j) if want == "credit" else bd(j)
            if not amt:
                continue
            gap = _date_gap_days(B[j].get("date"), ldate)
            if gap is None or gap > window:
                continue
            if amt - tol > target:
                continue
            pool.append((j, amt))
        if len(pool) < 2 or len(pool) > max_pool:
            continue
        subset = _subset_sum(target, pool, tol, max_subset, budget)
        if subset:
            commit([i], subset, 0.72, f"Aggregate: 1 book ↔ {len(subset)} bank (sum match)")

    # ── Pass D: contra / offset pairs within one side (net to zero) ──
    contra_sim = cfg["contra_min_similarity"]

    def find_contras(side: str):
        idxs = range(len(L)) if side == "book" else range(len(B))
        get_d = ld if side == "book" else bd
        get_c = lc if side == "book" else bc
        rows = L if side == "book" else B
        matched = matched_l if side == "book" else matched_b
        debits = [k for k in idxs if k not in matched and get_d(k)]
        credits = [k for k in idxs if k not in matched and get_c(k)]
        for di in debits:
            if di in matched:
                continue
            for ci in credits:
                if ci in matched or ci == di:
                    continue
                if abs(get_d(di) - get_c(ci)) > tol:
                    continue
                gap = _date_gap_days(rows[di].get("date"), rows[ci].get("date"))
                if gap is None or gap > window:
                    continue
                if _desc_similarity(rows[di].get("description"), rows[ci].get("description")) < contra_sim:
                    continue
                if side == "book":
                    commit([di, ci], [], 0.85, "Contra / offset pair (book, nets to zero)")
                else:
                    commit([], [di, ci], 0.85, "Contra / offset pair (bank, nets to zero)")
                break

    find_contras("book")
    find_contras("bank")

    auto_n = sum(1 for g in groups if g["tier"] == "auto")
    logger.info(
        f"Group matching complete: {len(groups)} groups ({auto_n} auto, "
        f"{len(groups) - auto_n} suggested) from {len(L)} book + {len(B)} bank txns"
    )
    return groups


# ---------------------------------------------------------------------------
# Reconciliation Summary Calculator
# ---------------------------------------------------------------------------

def compute_reconciliation_summary(
    ledger_closing_balance: Optional[Decimal],
    bank_closing_balance: Optional[Decimal],
    unmatched_ledger_txns: List[Dict],
    unmatched_bank_txns: List[Dict],
) -> Dict[str, Any]:
    """
    Compute the four unreconciled categories and the reconciliation difference.

    Formula:
      Reconciled Amount = Ledger CB
                        + Cat1 (ledger credits not in bank)
                        - Cat2 (ledger debits not in bank)
                        + Cat3 (bank credits not in ledger)
                        - Cat4 (bank debits not in ledger)

      Difference = Reconciled Amount - Bank CB   (should be 0)
    """
    def _sum(txns, field):
        return sum(Decimal(str(t.get(field) or 0)) for t in txns)

    cat1 = _sum(unmatched_ledger_txns, "credit_amount")   # ledger credits not in bank
    cat2 = _sum(unmatched_ledger_txns, "debit_amount")    # ledger debits not in bank
    cat3 = _sum(unmatched_bank_txns, "credit_amount")     # bank credits not in ledger
    cat4 = _sum(unmatched_bank_txns, "debit_amount")      # bank debits not in ledger

    lcb = ledger_closing_balance or Decimal("0")
    bcb = bank_closing_balance or Decimal("0")

    reconciled = lcb + cat1 - cat2 + cat3 - cat4
    difference = reconciled - bcb

    return {
        "ledger_credits_not_in_bank": float(cat1),
        "ledger_debits_not_in_bank": float(cat2),
        "bank_credits_not_in_ledger": float(cat3),
        "bank_debits_not_in_ledger": float(cat4),
        "reconciled_amount": float(reconciled),
        "reconciliation_difference": float(difference),
    }


# ---------------------------------------------------------------------------
# LLM-based bank statement parser
# ---------------------------------------------------------------------------

# Below this page count, process-pool startup costs more than it saves.
_PARALLEL_PDF_MIN_PAGES = 8


def _pdfplumber_pages_text(file_content: bytes, start: int, end: int) -> str:
    """Extract text from pages [start, end) with pdfplumber.

    Module-level so it is picklable as a ProcessPoolExecutor worker. Mirrors
    the serial loop in _extract_text_from_file exactly (same skip-empty-page
    and newline behavior) so parallel output is byte-identical to serial.
    """
    import pdfplumber
    text = ""
    with pdfplumber.open(io.BytesIO(file_content)) as pdf:
        for page in pdf.pages[start:end]:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


_PDF_POOL = None
_PDF_POOL_LOCK = None


def _get_pdf_pool():
    """Lazy singleton ProcessPoolExecutor for PDF text extraction.

    Uses the fork start method only: forked workers inherit loaded modules,
    so startup is milliseconds and the parent module's globals are NOT
    re-imported. With spawn (Windows), worker startup re-imports the whole
    app and costs more than it saves — return None and extract serially.
    """
    global _PDF_POOL, _PDF_POOL_LOCK
    import os
    import threading
    import multiprocessing as mp

    if "fork" not in mp.get_all_start_methods():
        return None
    if _PDF_POOL_LOCK is None:
        _PDF_POOL_LOCK = threading.Lock()
    with _PDF_POOL_LOCK:
        if _PDF_POOL is None:
            from concurrent.futures import ProcessPoolExecutor
            _PDF_POOL = ProcessPoolExecutor(
                max_workers=min(os.cpu_count() or 2, 8),
                mp_context=mp.get_context("fork"),
            )
    return _PDF_POOL


def _extract_pdf_text_parallel(file_content: bytes, page_count: int) -> str:
    """Fan contiguous page ranges out to a process pool; join in page order.

    Raises on any pool problem — the caller falls back to the serial path,
    which produces the same text.
    """
    global _PDF_POOL
    import os

    pool = _get_pdf_pool()
    if pool is None:
        raise RuntimeError("fork start method unavailable; use serial extraction")

    workers = min(os.cpu_count() or 2, 8, max(2, page_count // 4))
    per_worker = -(-page_count // workers)  # ceil division
    ranges = [
        (start, min(start + per_worker, page_count))
        for start in range(0, page_count, per_worker)
    ]
    try:
        parts = list(
            pool.map(
                _pdfplumber_pages_text,
                [file_content] * len(ranges),
                [r[0] for r in ranges],
                [r[1] for r in ranges],
                timeout=max(120, 5 * page_count),
            )
        )
    except Exception:
        # A broken/hung pool stays broken — drop it so the next request
        # builds a fresh one instead of failing forever.
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _PDF_POOL = None
        raise
    return "".join(parts)


def _extract_text_from_file(file_content: bytes, file_name: str) -> str:
    """Extract raw text from PDF or Excel for LLM parsing."""
    file_name_lower = file_name.lower()
    text = ""

    if file_name_lower.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                page_count = len(pdf.pages)

            if page_count >= _PARALLEL_PDF_MIN_PAGES:
                try:
                    text = _extract_pdf_text_parallel(file_content, page_count)
                except Exception:
                    logger.warning(
                        "Parallel PDF text extraction failed; retrying serially",
                        exc_info=True,
                    )
                    text = ""

            if not text.strip():
                with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
        except Exception:
            pass

        if not text.strip():
            try:
                import pdfminer.high_level
                text = pdfminer.high_level.extract_text(io.BytesIO(file_content))
            except Exception:
                pass

        if not text.strip():
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(io.BytesIO(file_content))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception:
                pass

        if not text.strip():
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(stream=file_content, filetype="pdf")
                text = "\n".join(page.get_text() for page in doc)
            except Exception:
                pass

    elif file_name_lower.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
            ws = wb.active
            rows = []
            for row in ws.iter_rows(values_only=True):
                row_str = "\t".join(str(c) if c is not None else "" for c in row)
                if row_str.strip():
                    rows.append(row_str)
            text = "\n".join(rows)
        except Exception:
            pass

    return text.strip()


async def parse_bank_statement_with_llm(
    file_content: bytes,
    file_name: str,
    period_from: Optional[date] = None,
    period_to: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Parse a bank statement using OpenAI to extract transactions with separate
    debit/credit columns plus opening and closing balances.

    Falls back to rule-based parsing if LLM fails or is unavailable.
    Filters transactions to the given period if provided.
    """
    from config.settings import settings

    result = {"opening_balance": None, "closing_balance": None, "transactions": []}

    # to_thread: text extraction is CPU-bound and would otherwise block the
    # event loop (freezing every other request) for the whole parse.
    raw_text = await asyncio.to_thread(_extract_text_from_file, file_content, file_name)
    if not raw_text:
        logger.warning("Could not extract text from statement; falling back to rule-based parser")
        fallback = await asyncio.to_thread(parse_bank_statement_for_recon, file_content, file_name)
        return _filter_by_period(fallback, period_from, period_to)

    openai_api_key = settings.OPENAI_API_KEY
    anthropic_api_key = settings.ANTHROPIC_API_KEY
    extraction_provider = settings.EXTRACTION_PROVIDER

    # Require at least one LLM key
    if extraction_provider == "claude" and not anthropic_api_key:
        logger.warning("EXTRACTION_PROVIDER=claude but no ANTHROPIC_API_KEY; using rule-based parser")
        fallback = await asyncio.to_thread(parse_bank_statement_for_recon, file_content, file_name)
        return _filter_by_period(fallback, period_from, period_to)

    if extraction_provider != "claude" and not openai_api_key:
        logger.warning("No OpenAI key; using rule-based parser")
        fallback = await asyncio.to_thread(parse_bank_statement_for_recon, file_content, file_name)
        return _filter_by_period(fallback, period_from, period_to)

    try:
        import json as _json

        # Chunks split on line boundaries with NO overlap, so a transaction whose
        # text spans the boundary (multi-line wire/EDI rows) is cut in half and
        # silently lost. Keep chunks large enough that a typical statement fits
        # in ONE chunk — boundaries should be the exception, not the rule.
        chunk_size = 24000
        lines = raw_text.split("\n")
        chunks, cur = [], ""
        for line in lines:
            if len(cur) + len(line) + 1 > chunk_size:
                if cur:
                    chunks.append(cur)
                cur = line
            else:
                cur = cur + "\n" + line if cur else line
        if cur:
            chunks.append(cur)

        period_hint = ""
        if period_from and period_to:
            period_hint = (
                f"\nOnly extract transactions between {period_from} and {period_to} (inclusive)."
                " Ignore any rows outside this date range."
            )

        if extraction_provider == "claude":
            from applicationca.agents.document_specific_functions.claude_extractor import call_claude_async
            claude_model = settings.CLAUDE_EXTRACTION_MODEL

            async def _extract_chunk(chunk_text: str, chunk_idx: int):
                prompt = f"""You are parsing a bank statement for accounting reconciliation.
Extract from the text below:
1. opening_balance: the opening/brought-forward balance (number, or null)
2. closing_balance: the closing/carried-forward balance (number, or null)
3. transactions: array of transaction rows (exclude opening/closing balance rows){period_hint}

For each transaction include:
- date: ISO format YYYY-MM-DD (convert from any format)
- description: full narration/description text
- reference: cheque no / UTR / transaction ID if present, else ""
- debit_amount: amount debited/withdrawn (number or null)
- credit_amount: amount credited/deposited (number or null)

Rules:
- Debit = money going OUT of the account (withdrawal, payment)
- Credit = money coming IN to the account (deposit, receipt)
- Numbers should be plain floats, no commas or currency symbols
- If a row has only a single "amount" column, determine debit vs credit from context clues (DR/CR labels, +/- signs, etc.)

Return only valid JSON:
{{"opening_balance": 12345.67, "closing_balance": 54321.00, "transactions": [{{"date":"YYYY-MM-DD","description":"...","reference":"...","debit_amount":null,"credit_amount":500.00}}]}}

BANK STATEMENT TEXT:
{chunk_text}"""

                raw = await call_claude_async(
                    file_path=None,
                    prompt=prompt,
                    api_key=anthropic_api_key,
                    model=claude_model,
                    system_prompt="You are a precise bank statement parser. Return only valid JSON.",
                    max_tokens=16384,
                )
                content = raw.strip()
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                return _json.loads(content)

        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=openai_api_key)

            async def _extract_chunk(chunk_text: str, chunk_idx: int):
                prompt = f"""You are parsing a bank statement for accounting reconciliation.
Extract from the text below:
1. opening_balance: the opening/brought-forward balance (number, or null)
2. closing_balance: the closing/carried-forward balance (number, or null)
3. transactions: array of transaction rows (exclude opening/closing balance rows){period_hint}

For each transaction include:
- date: ISO format YYYY-MM-DD (convert from any format)
- description: full narration/description text
- reference: cheque no / UTR / transaction ID if present, else ""
- debit_amount: amount debited/withdrawn (number or null)
- credit_amount: amount credited/deposited (number or null)

Rules:
- Debit = money going OUT of the account (withdrawal, payment)
- Credit = money coming IN to the account (deposit, receipt)
- Numbers should be plain floats, no commas or currency symbols
- If a row has only a single "amount" column, determine debit vs credit from context clues (DR/CR labels, +/- signs, etc.)

Return only valid JSON:
{{"opening_balance": 12345.67, "closing_balance": 54321.00, "transactions": [{{"date":"YYYY-MM-DD","description":"...","reference":"...","debit_amount":null,"credit_amount":500.00}}]}}

BANK STATEMENT TEXT:
{chunk_text}"""

                response = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a precise bank statement parser. Return only valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=16384,
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content.strip()
                return _json.loads(content)

        tasks = [_extract_chunk(c, i) for i, c in enumerate(chunks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_txns = []
        ob, cb = None, None
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"LLM chunk error: {r}")
                continue
            # Opening balance lives at the START of the statement -> first chunk
            # that reports one wins. Closing balance lives at the END -> the LAST
            # chunk that reports one wins (taking the first would freeze a page-1
            # running balance as the closing figure on multi-chunk statements).
            if ob is None and r.get("opening_balance") is not None:
                try:
                    ob = Decimal(str(r["opening_balance"]))
                except Exception:
                    pass
            if r.get("closing_balance") is not None:
                try:
                    cb = Decimal(str(r["closing_balance"]))
                except Exception:
                    pass
            for t in r.get("transactions", []):
                txn_date = _parse_date(t.get("date"))
                debit = _to_decimal(t.get("debit_amount"))
                credit = _to_decimal(t.get("credit_amount"))
                # A row with no money on either side can't be reconciled — it's a
                # header/marker the LLM misread, and it would sit forever in the
                # unmatched list as a zero row.
                if not debit and not credit:
                    continue
                all_txns.append({
                    "date": txn_date,
                    "description": str(t.get("description", "")).strip(),
                    "reference": str(t.get("reference", "")).strip(),
                    "debit_amount": debit,
                    "credit_amount": credit,
                })

        result["opening_balance"] = ob
        result["closing_balance"] = cb
        result["transactions"] = all_txns
        logger.info(f"LLM parsed {len(all_txns)} transactions, OB={ob}, CB={cb}")

        # Integrity check: rows must explain the balance movement. A drift means
        # the parse lost or misread rows (chunk boundary, merged duplicates,
        # wrong sign) — surface it loudly instead of reconciling against a
        # silently incomplete statement.
        if ob is not None and cb is not None and all_txns:
            total_cr = sum((t["credit_amount"] or Decimal("0")) for t in all_txns)
            total_dr = sum((t["debit_amount"] or Decimal("0")) for t in all_txns)
            drift = (ob + total_cr - total_dr) - cb
            if abs(drift) > Decimal("0.01"):
                logger.warning(
                    f"Statement parse integrity FAILED: OB {ob} + credits {total_cr} "
                    f"- debits {total_dr} = {ob + total_cr - total_dr} but CB is {cb} "
                    f"(drift {drift}). The parse likely lost or misread rows."
                )

    except Exception as e:
        logger.error(f"LLM parsing failed ({e}); falling back to rule-based parser")
        result = await asyncio.to_thread(parse_bank_statement_for_recon, file_content, file_name)

    return _filter_by_period(result, period_from, period_to)


def _filter_by_period(
    parsed: Dict[str, Any],
    period_from: Optional[date],
    period_to: Optional[date],
) -> Dict[str, Any]:
    """Remove transactions outside [period_from, period_to]."""
    if not period_from and not period_to:
        return parsed
    filtered = []
    for t in parsed.get("transactions", []):
        txn_date = t.get("date")
        if txn_date is None:
            filtered.append(t)
            continue
        if isinstance(txn_date, datetime):
            txn_date = txn_date.date()
        if period_from and txn_date < period_from:
            continue
        if period_to and txn_date > period_to:
            continue
        filtered.append(t)
    before = len(parsed.get("transactions", []))
    logger.info(f"Period filter [{period_from} – {period_to}]: {before} → {len(filtered)} transactions")
    return {**parsed, "transactions": filtered}


# ---------------------------------------------------------------------------
# Financial Year helper
# ---------------------------------------------------------------------------

def get_financial_year(period_from: date) -> str:
    """Return Indian FY string e.g. '2024-25' from a date."""
    if period_from.month >= 4:
        return f"{period_from.year}-{str(period_from.year + 1)[2:]}"
    return f"{period_from.year - 1}-{str(period_from.year)[2:]}"
