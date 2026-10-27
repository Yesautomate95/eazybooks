"""
SAP S/4HANA Cloud bank-side helpers used by the bank-reconciliation flow.

  * `list_bank_accounts` — Bank / cash GL accounts the user can reconcile.
  * `list_bank_txns` — line items that hit a chosen bank GL account inside a date
    window, normalized to the same flat shape the QBO path produces so the
    matcher and frontend code is shared.

Unlike Zoho/Xero (which expose a dedicated bank-transactions endpoint), S/4HANA
Cloud Public Edition has no per-bank-account transaction feed. The connector
contract exposes the operational accounting-document item cube
(`API_OPLACCTGDOCITEMCUBE_SRV`, via `sap_connector.list_open_items`), which
carries the posted GL / sub-ledger line items. We pull the open A/P (K) and
A/R (D) items for the company code, keep only the lines that posted to the
chosen bank GL account, filter to the date window client-side, and normalize.

VERIFY: the operational-cube field names (GLAccount, PostingDate,
AmountInCompanyCodeCurrency, DebitCreditCode, AccountingDocument, …) and the
amount sign convention against your tenant's $metadata before go-live — the
connector returns the raw cube rows untouched, so this module is the only place
those field names are pinned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from applicationca.services.sap_connector import sap_connector


# DebitCreditCode values: S = debit, H = credit. On a bank GL account a debit
# (S) is money flowing INTO the bank (matching the QBO "inflow positive"
# convention); a credit (H) is money out.
_INFLOW_DC_CODE = "S"

# Field-name candidates on the operational accounting-document item cube
# (A_OperationalAcctgDocItemCube). The connector returns the raw rows, so we
# probe a small set of known aliases for resilience across releases.
_F_GL_ACCOUNT = ("GLAccount", "GLAccountInChartOfAccounts")
_F_DATE = ("PostingDate", "DocumentDate")
_F_AMOUNT = (
    "AmountInCompanyCodeCurrency",
    "AmtInBalanceTransacCrcy",
    "AmountInTransactionCurrency",
)
_F_DC_CODE = ("DebitCreditCode", "DebitCreditIndicator")
_F_DOC = ("AccountingDocument", "OriginalReferenceDocument")
_F_REF = ("DocumentReferenceID", "AccountingDocument")
_F_TEXT = ("DocumentItemText", "AccountingDocumentHeaderText", "DocumentHeaderText")
_F_CURRENCY = ("CompanyCodeCurrency", "TransactionCurrency", "Currency")


def _pick(row: Dict[str, Any], keys: tuple) -> Any:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


async def list_bank_accounts(user_id: str, company_code: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return Bank + cash GL accounts, normalized for the picker UI.

    `sap_connector.list_bank_accounts` already narrows the chart of accounts to
    likely bank/cash GL accounts (by GLAccountType / name), so unlike the Zoho
    path we do not re-filter by account_type here — the connector card's
    `account_type` is a GLAccountType code, not a "bank"/"credit_card" enum.
    """
    cards = await sap_connector.list_bank_accounts(user_id, company_code)
    out: List[Dict[str, Any]] = []
    for c in cards:
        raw = c.get("raw") or {}
        out.append({
            "id": c.get("list_id"),
            "name": c.get("name"),
            "account_type": c.get("account_type"),
            "account_sub_type": c.get("account_group") or raw.get("GLAccountGroup"),
            "currency": raw.get("CompanyCodeCurrency") or raw.get("Currency"),
        })
    return out


def _to_float(s: Any) -> float:
    if s is None or s == "":
        return 0.0
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _iso_date(value: Any) -> Optional[str]:
    """Normalize a cube date to YYYY-MM-DD, handling the OData V2 /Date(ms)/ form."""
    if not value:
        return None
    s = str(value)
    if s.startswith("/Date("):
        try:
            ms = int(s[len("/Date("):].split(")")[0].split("+")[0])
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, IndexError, OSError):
            return None
    return s[:10]


def _normalize_txn(t: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a SAP operational-cube line item to the flat shape the matcher
    expects.

    `amount` is signed positive = inflow into the bank account (matching the QBO
    convention) so the shared reconciliation matcher behaves identically. On a
    bank GL account a debit (DebitCreditCode 'S') is money in, a credit ('H') is
    money out. SAP cube amounts are typically unsigned, so we apply the sign from
    the DebitCreditCode.
    """
    dc = (_pick(t, _F_DC_CODE) or "").upper()
    amount = abs(_to_float(_pick(t, _F_AMOUNT)))
    if amount:
        amount = amount if dc == _INFLOW_DC_CODE else -amount
    doc = _pick(t, _F_DOC)
    return {
        "id": doc,
        "type": t.get("FinancialAccountType"),
        "type_id": doc,
        "date": _iso_date(_pick(t, _F_DATE)),
        "doc_number": _pick(t, _F_REF) or doc,
        "payee": t.get("Supplier") or t.get("Customer") or t.get("BusinessPartner"),
        "payee_id": t.get("Supplier") or t.get("Customer") or t.get("BusinessPartner"),
        "memo": _pick(t, _F_TEXT) or _pick(t, _F_REF),
        "split_account": _pick(t, _F_GL_ACCOUNT),
        "amount": amount,
    }


async def list_bank_txns(
    user_id: str,
    *,
    account_id: str,
    start_date: str,
    end_date: str,
    company_code: str,
) -> List[Dict[str, Any]]:
    """Fetch every GL line item that hit the bank GL account `account_id` between
    start_date and end_date (YYYY-MM-DD inclusive). Returns a flat list of
    normalized rows.

    SAP has no dedicated bank-transactions endpoint, so we read the GL line items
    (FinancialAccountType 'S') for the bank GL account from the operational
    accounting-document item cube via sap_connector.list_gl_line_items, which
    filters by GLAccount + posting-date window server-side (and, unlike the
    open-item read, is not gated on clearing status).
    """
    rows = await sap_connector.list_gl_line_items(
        user_id, company_code,
        gl_account=str(account_id), date_from=start_date, date_to=end_date,
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        # Connector already filtered by GLAccount + date; defensive client-side
        # guard for the /Date(ms)/ vs datetime-literal edge.
        d = _iso_date(_pick(r, _F_DATE))
        if d and not (start_date <= d <= end_date):
            continue
        out.append(_normalize_txn(r))
    return out
