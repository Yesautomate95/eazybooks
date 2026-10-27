"""
Xero chat agent — ReAct over the Xero Accounting API.

Like Zoho Books, Xero has NO SQL query language (unlike QBO's /query endpoint),
so the QBO qbo_query / qbo_report tools are replaced by three Xero-appropriate
tools:

  list_xero_entities()
      Returns the Xero resources the agent can query, plus a short hint of what
      each one represents and which filters it supports.

  xero_list(resource, filters)
      Lists a single resource (customers/vendors as unified Contacts, items,
      chartofaccounts, invoices, bills, banktransactions) via the matching
      xero_connector list_* method, with optional date/status/name filters.
      Returns normalized cards. There is no JOIN / GROUP BY — Xero filters by
      `where` query params and client-side partial name matches, NOT
      case-sensitive SQL.

  xero_report(name, params)
      Runs a Xero report (ProfitAndLoss / BalanceSheet / TrialBalance) via
      xero_connector.get_report. `params` is forwarded as querystring
      (fromDate, toDate, date, …). Report availability / param names vary, so
      this degrades gracefully: on error it tells the user instead of failing
      the run.

The organisation (tenant) is resolved from the authenticated user's selected
Xero tenant (`tenant_id`); the agent never sees it directly.

The agent iterates up to MAX_TURNS, then streams the final answer.

Stream events (NDJSON) match the TallyChat shape (unchanged from QBO/Zoho so the
frontend stays identical):
    {type: "status",       content}
    {type: "tool_call",    name, arguments}
    {type: "tool_result",  name, content}
    {type: "answer_delta", content}
    {type: "error",        content}
    {type: "done"}
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from applicationca.services.xero_connector import xero_connector
from applicationca.services.xero_brief import get_or_refresh_brief
from config.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Xero date helper
# ---------------------------------------------------------------------------

# Xero RESPONSE dates look like "/Date(1599609600000+0000)/" (epoch ms, optional
# tz). Mirrors xero_duplicate_check._parse_xero_date so chat reads invoice /
# bank-transaction dates consistently.
_XERO_DATE_RE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


def _parse_xero_date(value: Any) -> Optional[str]:
    """Parse a Xero date into YYYY-MM-DD, accepting both the "/Date(ms)/" form
    and a plain ISO prefix."""
    if not value:
        return None
    s = str(value)
    m = _XERO_DATE_RE.search(s)
    if m:
        try:
            dt = datetime.fromtimestamp(int(m.group(1)) / 1000.0, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            return None
    s = s[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

# (resource, hint, supported filters). The `resource` is the literal value the
# agent passes to xero_list; it maps to a xero_connector.list_* method.
XERO_ENTITIES = [
    ("customers", "buyers / customers (Xero Contacts); name, company, email, outstanding AR balance",
     "search_text (partial name)"),
    ("vendors", "suppliers / vendors (Xero Contacts); name, company, outstanding AP balance",
     "search_text (partial name)"),
    ("items", "products and services you sell; name, code, rate",
     "search_text (partial name)"),
    ("chartofaccounts", "chart of accounts; name, code, account_type (BANK/REVENUE/EXPENSE/…)",
     "(no filters — returns the full chart)"),
    ("invoices", "sales invoices (Type ACCREC); contact, date, total, status",
     "date_start, date_end (YYYY-MM-DD), status (DRAFT/SUBMITTED/AUTHORISED/PAID/VOIDED), customer_name"),
    ("bills", "vendor bills (Type ACCPAY); contact, date, total, status",
     "date_start, date_end (YYYY-MM-DD), status (DRAFT/SUBMITTED/AUTHORISED/PAID/VOIDED), vendor_name"),
    ("banktransactions", "bank account transactions (SPEND = money out, RECEIVE = money in)",
     "bank_account_id, date_start, date_end (YYYY-MM-DD)"),
]

# Xero report endpoint names (PascalCase, as the Reports API path segment).
# Availability / param names vary; xero_report degrades gracefully when one is
# missing. # VERIFY exact Xero report names + param names for this org/edition.
XERO_REPORTS = [
    "ProfitAndLoss",
    "BalanceSheet",
    "TrialBalance",
]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_xero_entities",
            "description": (
                "List the Xero resources you can query with xero_list, plus the "
                "report names available via xero_report, with the filters each "
                "one accepts. Call this if you're unsure which resource holds "
                "the data you need."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "xero_list",
            "description": (
                "List a single Xero resource for the user's organisation. "
                "Xero has no SQL — you cannot JOIN, GROUP BY, or SUM here. "
                "Filter via the `filters` object only.\n"
                "Rules:\n"
                "  - One resource per call.\n"
                "  - Name matching is partial and case-insensitive — pass "
                "search_text='hicks' to find 'Hicks & Co'.\n"
                "  - Dates are 'YYYY-MM-DD'. Use date_start / date_end to window "
                "invoices, bills, banktransactions.\n"
                "  - For any total / aggregate (revenue, expenses, net profit), "
                "use xero_report instead — this tool only returns rows.\n"
                "Examples:\n"
                "  xero_list('vendors', {'search_text': 'hicks'})\n"
                "  xero_list('invoices', {'date_start': '2026-01-01', "
                "'status': 'AUTHORISED'})\n"
                "  xero_list('chartofaccounts', {})"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {
                        "type": "string",
                        "description": (
                            "One of: customers, vendors, items, chartofaccounts, "
                            "invoices, bills, banktransactions."
                        ),
                    },
                    "filters": {
                        "type": "object",
                        "description": (
                            "Optional filters: search_text, status, customer_name, "
                            "vendor_name, bank_account_id, date_start, date_end."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["resource"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "xero_report",
            "description": (
                "Run a Xero report. Use this for totals and time-bucket "
                "summaries that xero_list can't compute (revenue, expenses, net "
                "profit, asset/liability balances). Call list_xero_entities first "
                "to see available report names if unsure.\n"
                "Common params: fromDate, toDate (YYYY-MM-DD) for ProfitAndLoss; "
                "date (YYYY-MM-DD) for BalanceSheet / TrialBalance. If a report "
                "is unavailable for this org you'll get an error back — tell the "
                "user rather than guessing the number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Report name. One of: ProfitAndLoss, BalanceSheet, "
                            "TrialBalance."
                        ),
                    },
                    "params": {
                        "type": "object",
                        "description": "Querystring params for the report (fromDate, toDate, date, …).",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["name"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _truncate(obj: Any, max_chars: int = 6000) -> str:
    s = json.dumps(obj, default=str)
    if len(s) > max_chars:
        return s[:max_chars] + f"\n…(truncated, {len(s) - max_chars} more chars)"
    return s


async def _tool_list_entities() -> Dict[str, Any]:
    return {
        "resources": [
            {"name": n, "hint": h, "filters": f} for n, h, f in XERO_ENTITIES
        ],
        "reports": XERO_REPORTS,
    }


# Filter keys we translate into Xero query params for the transaction lists.
# Xero filters invoices / bills / bank transactions with a `where` clause and a
# `Type` discriminator; everything else we leave to the connector defaults and
# post-filter on the normalized cards.
# VERIFY exact Xero `where` syntax / Date format for the date window.
def _build_list_params(resource: str, filters: Dict[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    where: List[str] = []

    if resource == "invoices":
        where.append('Type=="ACCREC"')
    elif resource == "bills":
        where.append('Type=="ACCPAY"')

    ds = filters.get("date_start")
    de = filters.get("date_end")
    # Xero `where` dates use DateTime(yyyy,mm,dd). # VERIFY
    if ds:
        try:
            y, m, d = str(ds).split("-")
            where.append(f"Date>=DateTime({int(y)},{int(m)},{int(d)})")
        except Exception:
            pass
    if de:
        try:
            y, m, d = str(de).split("-")
            where.append(f"Date<=DateTime({int(y)},{int(m)},{int(d)})")
        except Exception:
            pass
    if filters.get("status") and resource in ("invoices", "bills"):
        where.append(f'Status=="{str(filters["status"]).upper()}"')
    if resource == "banktransactions" and filters.get("bank_account_id"):
        where.append(f'BankAccount.AccountID==Guid("{filters["bank_account_id"]}")')

    if where:
        params["where"] = " && ".join(where)
    return params


def _matches_text(card: Dict[str, Any], needle: str, *fields: str) -> bool:
    needle = needle.lower().strip()
    if not needle:
        return True
    for f in fields:
        val = card.get(f)
        if val and needle in str(val).lower():
            return True
    # also probe the raw object for the common Xero name fields
    raw = card.get("raw") or {}
    for f in ("Name", "ContactName", "Contact", "name"):
        val = raw.get(f) if isinstance(raw, dict) else None
        if isinstance(val, dict):
            val = val.get("Name")
        if val and needle in str(val).lower():
            return True
    return False


def _contact_name_of_txn(card: Dict[str, Any]) -> str:
    """Pull the contact name off a raw invoice / bill / bank-transaction card."""
    raw = card.get("raw") or {}
    contact = raw.get("Contact") if isinstance(raw, dict) else None
    if isinstance(contact, dict):
        return str(contact.get("Name") or "")
    return ""


def _summarize_txn(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Compact a raw Xero invoice / bill / bank-transaction down to the fields a
    chat answer needs, parsing the "/Date(ms)/" date into YYYY-MM-DD."""
    contact = raw.get("Contact") or {}
    return {
        "id": raw.get("InvoiceID") or raw.get("BankTransactionID"),
        "number": raw.get("InvoiceNumber"),
        "type": raw.get("Type"),
        "contact": contact.get("Name") if isinstance(contact, dict) else None,
        "date": _parse_xero_date(raw.get("Date")),
        "due_date": _parse_xero_date(raw.get("DueDate")),
        "total": raw.get("Total"),
        "amount_due": raw.get("AmountDue"),
        "amount_paid": raw.get("AmountPaid"),
        "status": raw.get("Status"),
    }


async def _tool_xero_list(
    user_id: str,
    tenant_id: Optional[str],
    resource: str,
    filters: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    resource = (resource or "").strip().lower()
    filters = dict(filters or {})
    valid = {n for n, _h, _f in XERO_ENTITIES}
    if resource not in valid:
        return {"error": f"unknown resource {resource!r}; valid: {sorted(valid)}"}
    if not tenant_id:
        return {"error": "no Xero organisation selected for this user"}

    params = _build_list_params(resource, filters)
    try:
        if resource == "customers":
            rows = await xero_connector.list_customers(user_id, tenant_id)
        elif resource == "vendors":
            rows = await xero_connector.list_vendors(user_id, tenant_id)
        elif resource == "items":
            rows = await xero_connector.list_items(user_id, tenant_id)
        elif resource == "chartofaccounts":
            rows = await xero_connector.list_accounts(user_id, tenant_id)
        elif resource == "invoices":
            rows = await xero_connector.list_invoices(user_id, tenant_id, params=params or None)
        elif resource == "bills":
            # Bills are invoices with Type=ACCPAY — same endpoint, different filter.
            rows = await xero_connector.list_invoices(user_id, tenant_id, params=params or None)
        elif resource == "banktransactions":
            rows = await xero_connector.list_bank_transactions(user_id, tenant_id, params=params or None)
        else:  # unreachable given the valid-set guard above
            return {"error": f"unhandled resource {resource!r}"}
    except Exception as exc:
        logger.warning(f"xero_list({resource}) failed: {exc}")
        return {"error": f"Xero list {resource} failed: {exc}", "resource": resource}

    # Client-side filtering for the bits Xero's list params don't cover:
    # partial name matching on customers/vendors/items, and customer_name /
    # vendor_name narrowing on invoices/bills.
    search_text = filters.get("search_text") or ""
    if search_text and resource in ("customers", "vendors", "items"):
        rows = [r for r in rows if _matches_text(r, search_text, "name", "company", "email", "code")]

    if resource == "invoices" and filters.get("customer_name"):
        cn = str(filters["customer_name"]).lower()
        rows = [r for r in rows if cn in _contact_name_of_txn(r).lower()]
    if resource == "bills" and filters.get("vendor_name"):
        vn = str(filters["vendor_name"]).lower()
        rows = [r for r in rows if vn in _contact_name_of_txn(r).lower()]

    # invoices/bills/banktransactions come back as RAW Xero rows from the
    # connector — summarize them (and parse the "/Date(ms)/" dates) so the model
    # sees clean YYYY-MM-DD values instead of epoch blobs.
    if resource in ("invoices", "bills", "banktransactions"):
        rows = [_summarize_txn(r if isinstance(r, dict) else {}) for r in rows]

    # Cap rows so tool output stays small (mirrors QBO's MAXRESULTS discipline).
    capped = rows[:100]
    return {
        "resource": resource,
        "row_count": len(rows),
        "returned": len(capped),
        "rows": capped,
    }


def _flatten_report_rows(node: Any, depth: int = 0, out: Optional[List[str]] = None) -> List[str]:
    """Walk Xero's nested report tree into flat, readable 'Label: value' lines.

    Xero reports are returned as Reports[].Rows[], where each Row has a
    RowType (Header/Section/Row/SummaryRow) and a Cells[] array; the first cell
    is the label and later cells carry the amounts. Section rows nest their own
    Rows[]. The totals an answer needs (Gross Profit, Net Profit, Total Assets …)
    live in SummaryRow cells that are easy to miss in raw nested JSON, so we
    flatten so each meaningful label + amount is one line.
    """
    if out is None:
        out = []
    indent = "  " * depth

    if isinstance(node, list):
        for child in node:
            _flatten_report_rows(child, depth, out)
        return out

    if not isinstance(node, dict):
        return out

    # Xero Row shape: {RowType, Title?, Cells:[{Value}, …], Rows:[…]}.
    cells = node.get("Cells")
    if isinstance(cells, list) and cells:
        values = [str((c or {}).get("Value", "")).strip() for c in cells if isinstance(c, dict)]
        values = [v for v in values if v != ""]
        if values:
            label = values[0]
            rest = values[1:]
            if rest:
                out.append(f"{indent}{label}: {' | '.join(rest)}")
            else:
                out.append(f"{indent}{label}")
    else:
        # Header / Section title without cells.
        title = node.get("Title") or node.get("Name") or node.get("label")
        if title:
            out.append(f"{indent}{title}")

    # Recurse into nested collections (Xero uses Rows; probe generic ones too).
    for k in ("Rows", "rows", "child", "children", "sections", "items"):
        if isinstance(node.get(k), list):
            _flatten_report_rows(node[k], depth + 1, out)

    return out


# Period-style reports accept a date window. Default them to fiscal-year-to-date
# when the user gave no period so they return data instead of an empty range.
_PERIOD_REPORTS = {"profitandloss"}


async def _tool_xero_report(
    user_id: str,
    tenant_id: Optional[str],
    name: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    # Match the requested name case-insensitively against the canonical
    # PascalCase report names.
    raw_name = (name or "").strip()
    canon = {r.lower(): r for r in XERO_REPORTS}
    report_name = canon.get(raw_name.lower())
    if not report_name:
        return {"error": f"unknown report {name!r}; valid: {XERO_REPORTS}"}
    if not tenant_id:
        return {"error": "no Xero organisation selected for this user"}

    params = dict(params or {})
    # Bare "what's my revenue?" gives no window. Default period reports to the
    # current fiscal year-to-date. VERIFY: confirm Xero accepts fromDate/toDate
    # for ProfitAndLoss; if not, the report still runs against its own defaults.
    if report_name.lower() in _PERIOD_REPORTS and not any(
        k in params for k in ("fromDate", "toDate", "periods")
    ):
        today = date.today()
        params["fromDate"] = date(today.year, 1, 1).isoformat()  # VERIFY fiscal-year start
        params["toDate"] = today.isoformat()

    try:
        params_str = {k: str(v) for k, v in params.items()}
        raw = await xero_connector.get_report(
            user_id, report_name, tenant_id, params=params_str or None,
        )
    except Exception as exc:
        # Degrade gracefully — reports may not be available / params may differ.
        logger.warning(f"xero_report({report_name}) failed: {exc}")
        return {
            "error": (
                f"The {report_name} report isn't available for this organisation "
                f"({exc}). Tell the user this report couldn't be fetched and, if "
                "possible, answer from list data instead."
            ),
            "report": report_name,
        }

    # Xero wraps reports as {"Reports": [{"ReportName", "Rows": [...]}, …]}.
    body: Any = raw
    if isinstance(raw, dict):
        reports = raw.get("Reports")
        if isinstance(reports, list) and reports:
            body = reports[0].get("Rows", reports[0])
        elif isinstance(raw.get("Rows"), list):
            body = raw["Rows"]

    return {
        "report": report_name,
        "lines": _flatten_report_rows(body),
    }


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

MAX_TURNS = 8


# Maps a Xero resource → human phrase, used by `_humanize_tool` to translate
# raw tool calls into the friendly status text streamed to the chat UI
# ("Looking up your customer list…" instead of "xero_list customers"). Falls
# back to a generic phrasing so non-tech users still get something readable.
_RESOURCE_PHRASES = {
    "customers":        "your customer list",
    "vendors":          "your vendor list",
    "items":            "your items",
    "chartofaccounts":  "your chart of accounts",
    "invoices":         "your sales invoices",
    "bills":            "your vendor bills",
    "banktransactions": "your bank transactions",
}

_REPORT_PHRASES = {
    "profitandloss": "your P&L",
    "balancesheet":  "your balance sheet",
    "trialbalance":  "the trial balance",
}


def _humanize_tool(name: str, args: Dict[str, Any]) -> str:
    """Map a tool invocation to a friendly status line. Reads the relevant
    args (resource, report name) and picks a non-jargon phrase."""
    if name == "list_xero_entities":
        return "Figuring out where this data lives in Xero…"
    if name == "xero_list":
        resource = (args.get("resource") or "").lower()
        phrase = _RESOURCE_PHRASES.get(resource)
        if phrase:
            return f"Looking up {phrase}…"
        return "Running a quick lookup in Xero…"
    if name == "xero_report":
        rep = (args.get("name") or "").lower()
        if rep in _REPORT_PHRASES:
            return f"Crunching {_REPORT_PHRASES[rep]}…"
        return "Pulling a report from Xero…"
    return "Working on it…"


def _humanize_turn(turn: int) -> str:
    """Status phrase for the start of a reasoning turn. Avoids "turn N" speak."""
    if turn == 0:
        return "Thinking about your question…"
    if turn == 1:
        return "Checking the data I just pulled…"
    if turn == 2:
        return "Comparing across what I've found so far…"
    return "Pulling everything together…"


def _system_prompt(company_name: Optional[str], brief: Optional[str] = None) -> str:
    base = (
        f"You are an analyst assistant for a Xero organisation"
        + (f" — {company_name}." if company_name else ".")
        + " Use the xero_list and xero_report tools to look up real data before "
        "answering. "
        f"Today's date is {date.today().isoformat()}. The fiscal year may differ "
        "from the calendar year.\n\n"
        "How to think:\n"
        " - Break complex questions into smaller steps. Don't try to answer in one tool call.\n"
        " - First identify what data you need; then pick the right tool; then verify the result.\n"
        " - If the user's question implies a comparison (this month vs last month, top N, etc.), "
        "run separate lookups and reason over them.\n\n"
        "Xero is NOT SQL (IMPORTANT):\n"
        " - There is no query language, no JOIN, no GROUP BY, no SUM. You list a "
        "single resource at a time and filter it with the `filters` object.\n"
        " - Filtering happens via Xero `where` clauses (handled for you) and "
        "client-side partial name matches. Name matching is partial and "
        "case-insensitive — to find a vendor 'Hicks & Co', pass "
        "search_text='hicks'. Do not assume exact casing.\n"
        " - To narrow invoices/bills, use date_start / date_end (YYYY-MM-DD), "
        "status, and customer_name / vendor_name filters.\n"
        " - Customers and vendors are the SAME Contacts in Xero — a contact can be "
        "both. Use the customers / vendors resources for AR / AP context.\n\n"
        "Where the numbers live (IMPORTANT):\n"
        " - Revenue / income / sales / turnover, expenses, COGS, gross profit, net "
        "profit/income: ALWAYS use xero_report('ProfitAndLoss'). Do NOT add these "
        "up by listing invoices — that misses adjustments and isn't a real total. "
        "The P&L report totals come back in the report's `lines`.\n"
        " - For a specific window pass fromDate and toDate (YYYY-MM-DD). If the "
        "user gave no period, omit them — the report defaults to this fiscal "
        "year-to-date.\n"
        " - Assets, liabilities, equity, bank/cash balances: use "
        "xero_report('BalanceSheet') (optionally pass `date`=YYYY-MM-DD).\n"
        " - A full trial balance: xero_report('TrialBalance').\n\n"
        "Tool-use rules:\n"
        " - Prefer xero_list for entity lookups and detail rows.\n"
        " - Prefer xero_report for P&L / balance / trial-balance / totals.\n"
        " - xero_list cannot SUM or GROUP BY — for any total or aggregate, use xero_report.\n"
        " - Don't invent rows you didn't see in a tool result.\n"
        " - Keep tool outputs small — narrow with filters and date ranges.\n"
        " - If a list returns no rows, try widening: drop filters, broaden "
        "search_text, call list_xero_entities, or use a report instead.\n"
        " - If a report comes back with an error (some reports / params vary by "
        "Xero edition), say so plainly and answer from list data if you can.\n\n"
        "Answer style:\n"
        " - Lead with the headline number / answer in one sentence.\n"
        " - Then a short explanation using the data you pulled.\n"
        " - Use markdown tables when listing rows. Format money with currency symbol "
        "and two decimals."
    )
    if brief:
        base += (
            "\n\n=== COMPANY-SPECIFIC BRIEF (cached snapshot — prefer these real "
            "names over guesses) ===\n"
            + brief
        )
    return base


async def _company_name(user_id: str, tenant_id: Optional[str]) -> Optional[str]:
    if not tenant_id:
        return None
    try:
        info = await xero_connector.get_company_info(user_id, tenant_id)
        # Xero Organisation dict uses PascalCase Name; fall back defensively.
        return info.get("Name") or info.get("name") or info.get("company_name")
    except Exception:
        return None


async def _dispatch_tool(
    user_id: str,
    tenant_id: Optional[str],
    name: str,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    if name == "list_xero_entities":
        return await _tool_list_entities()
    if name == "xero_list":
        return await _tool_xero_list(
            user_id, tenant_id, args.get("resource", ""), args.get("filters") or {},
        )
    if name == "xero_report":
        return await _tool_xero_report(
            user_id, tenant_id, args.get("name", ""), args.get("params") or {},
        )
    return {"error": f"unknown tool {name!r}"}


async def run_xero_chat(
    *,
    user_id: str,
    tenant_id: Optional[str],
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """ReAct loop. Streams status + tool events + final answer tokens."""
    yield {"type": "status", "content": "Reading your question…"}

    company_name = await _company_name(user_id, tenant_id)

    brief: Optional[str] = None
    if tenant_id:
        try:
            brief = await get_or_refresh_brief(user_id, tenant_id)
        except Exception as exc:
            logger.warning(f"xero-chat brief load failed: {exc}")

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(company_name, brief)},
    ]
    for m in (history or []):
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    model = settings.OPENAI_MODEL or "gpt-4o-mini"

    for turn in range(MAX_TURNS):
        yield {"type": "status", "content": _humanize_turn(turn)}

        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.1,
            )
        except Exception as exc:
            logger.exception("xero-chat planner LLM failed")
            yield {"type": "error", "content": str(exc)}
            yield {"type": "done"}
            return

        if not resp.choices:
            yield {"type": "error", "content": "Empty LLM response"}
            yield {"type": "done"}
            return

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        # No tool calls -> final answer turn. Stream a fresh completion so the
        # frontend gets token-by-token output.
        if not tool_calls:
            yield {"type": "status", "content": "Putting your answer together…"}
            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    stream=True,
                )
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield {"type": "answer_delta", "content": delta.content}
            except Exception as exc:
                logger.exception("xero-chat answer stream failed")
                yield {"type": "error", "content": str(exc)}
            yield {"type": "done"}
            return

        # Persist the assistant message with tool calls (required by OpenAI
        # to feed tool results back).
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        # Run each tool sequentially. (Xero has per-tenant rate limits — parallel
        # would only help marginally.)
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            # Friendly status before the technical tool_call event so non-tech
            # users see "Looking up your sales invoices…" while the trace still
            # carries the raw arguments for developers / power users.
            yield {"type": "status", "content": _humanize_tool(name, args)}
            yield {"type": "tool_call", "name": name, "arguments": args}
            result = await _dispatch_tool(user_id, tenant_id, name, args)
            yield {"type": "tool_result", "name": name, "content": _truncate(result, 2000)}
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": name,
                "content": _truncate(result),
            })

    # Hit MAX_TURNS without a final answer.
    yield {
        "type": "error",
        "content": f"Reached the {MAX_TURNS}-turn tool limit without a final answer.",
    }
    yield {"type": "done"}
