"""
Mock QuickBooks Desktop Server
==============================
Mock implementation of QBXML over HTTP. Real QB Desktop does not expose HTTP
directly - production uses QB Web Connector (SOAP) or COM via qbXMLRP2.dll
on the user's machine, bridged through the Electron desktop app.

This mock lets the backend speak QBXML in dev without QB Desktop installed.
Listens on port 9001 (Tally mock uses 9000).

Usage:
    python -m mock_qb
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .sample_data import (
    COMPANIES,
    add_account,
    add_customer,
    add_transaction,
    add_vendor,
    find_company,
    get_accounts,
    get_customers,
    get_items,
    get_vendors,
)
from .qbxml_generators import (
    account_query_response,
    add_response,
    bill_add_response,
    company_query_response,
    customer_query_response,
    error_response,
    host_query_response,
    invoice_add_response,
    item_query_response,
    vendor_query_response,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mock_qb")

app = FastAPI(title="Mock QuickBooks Desktop", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

stats = {
    "requests": 0,
    "errors": 0,
    "start_time": datetime.now().isoformat(),
}

# --- Selected company state (mocks the "currently open company file" in QB) ---
_active_company: Optional[str] = COMPANIES[0]["name"] if COMPANIES else None


# ---------------------------------------------------------------------------
# QBXML PARSING
# ---------------------------------------------------------------------------

_OP_PATTERN = re.compile(r"<(\w+Rq)\b", re.IGNORECASE)
_REQUEST_ID_PATTERN = re.compile(r'requestID="([^"]+)"', re.IGNORECASE)


def parse_qbxml_request(body: str) -> dict:
    op_match = _OP_PATTERN.search(body)
    rid_match = _REQUEST_ID_PATTERN.search(body)
    return {
        "operation": op_match.group(1) if op_match else "",
        "request_id": rid_match.group(1) if rid_match else "0",
        "raw": body,
    }


def _extract_tag(body: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}>([^<]+)</{tag}>", body, re.IGNORECASE)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {
        "service": "mock_qb",
        "active_company": _active_company,
        "companies": [c["name"] for c in COMPANIES],
        "stats": stats,
    }


@app.get("/stats")
async def get_stats():
    return stats


@app.post("/select-company")
async def select_company(payload: dict):
    """Switch the active company (mocks opening a different .QBW file)."""
    global _active_company
    name = payload.get("name")
    if not find_company(name):
        return Response(status_code=404, content=f"Company not found: {name}")
    _active_company = name
    return {"active_company": _active_company}


@app.post("/", response_class=Response)
async def qbxml_endpoint(request: Request):
    stats["requests"] += 1
    body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qbxml_request(body)
    op = parsed["operation"]
    rid = parsed["request_id"]
    company = _active_company or (COMPANIES[0]["name"] if COMPANIES else "")

    logger.info(f"QBXML op={op} request_id={rid} company={company}")

    try:
        xml = _dispatch(op, rid, company, body)
    except Exception as exc:  # noqa: BLE001
        stats["errors"] += 1
        logger.exception("Error handling QBXML request")
        xml = error_response(op or "Unknown", rid, str(exc))

    return Response(content=xml, media_type="text/xml")


def _dispatch(op: str, rid: str, company: str, body: str) -> str:
    op_lower = op.lower()

    if op_lower == "hostqueryrq":
        return host_query_response(rid, COMPANIES)

    if op_lower == "companyqueryrq":
        comp = find_company(company)
        if not comp:
            return error_response("CompanyQuery", rid, f"No company open: {company}")
        return company_query_response(rid, comp)

    if op_lower == "accountqueryrq":
        return account_query_response(rid, get_accounts(company))

    if op_lower == "customerqueryrq":
        return customer_query_response(rid, get_customers(company))

    if op_lower == "vendorqueryrq":
        return vendor_query_response(rid, get_vendors(company))

    if op_lower == "itemqueryrq":
        return item_query_response(rid, get_items(company))

    if op_lower == "customeraddrq":
        name = _extract_tag(body, "Name") or "Unnamed"
        rec = add_customer(company, {"name": name, "company": _extract_tag(body, "CompanyName") or name})
        return add_response("CustomerAddRs", rid, rec["list_id"], rec["name"])

    if op_lower == "vendoraddrq":
        name = _extract_tag(body, "Name") or "Unnamed"
        rec = add_vendor(company, {"name": name, "company": _extract_tag(body, "CompanyName") or name})
        return add_response("VendorAddRs", rid, rec["list_id"], rec["name"])

    if op_lower == "accountaddrq":
        name = _extract_tag(body, "Name") or "Unnamed"
        atype = _extract_tag(body, "AccountType") or "Expense"
        rec = add_account(company, {"name": name, "account_type": atype})
        return add_response("AccountAddRs", rid, rec["list_id"], rec["name"])

    if op_lower == "invoiceaddrq":
        ref = _extract_tag(body, "RefNumber") or f"INV-{uuid.uuid4().hex[:6].upper()}"
        txn = add_transaction({"type": "Invoice", "company": company, "ref": ref, "raw": body})
        return invoice_add_response(rid, txn["txn_id"], ref)

    if op_lower == "billaddrq":
        ref = _extract_tag(body, "RefNumber") or f"BILL-{uuid.uuid4().hex[:6].upper()}"
        txn = add_transaction({"type": "Bill", "company": company, "ref": ref, "raw": body})
        return bill_add_response(rid, txn["txn_id"], ref)

    return error_response(op or "Unknown", rid, f"Unsupported QBXML operation: {op}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    import uvicorn

    uvicorn.run(
        "mock_qb.server:app",
        host="0.0.0.0",
        port=9001,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
