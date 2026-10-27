"""
QBO chat agent — ReAct over QBO REST.

Three tools (function calling):

  list_qbo_entities()
      Returns the QBO entity types the agent can query, plus a short hint of
      what each one represents.

  qbo_query(sql)
      Runs a QBO SQL-like SELECT against /v3/company/{realm}/query. SELECT
      only — anything else is rejected. The realm is resolved from the
      authenticated user's connected QBO realm; the agent never sees it.

  qbo_report(name, params)
      Runs a QBO Report (ProfitAndLoss / BalanceSheet / TransactionList /
      AgedReceivables / AgedPayables / TrialBalance / GeneralLedger / …).
      `params` is forwarded as querystring (start_date, end_date, account, …).

The agent iterates up to MAX_TURNS, then streams the final answer.

Stream events (NDJSON) match the TallyChat shape:
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
from datetime import date
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from applicationca.services.qbo_connector import qbo_connector
from applicationca.services.qbo_brief import get_or_refresh_brief
from config.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

QBO_ENTITIES = [
    ("Customer",     "buyers; AR balances; DisplayName, CompanyName, Balance"),
    ("Vendor",       "suppliers; AP balances"),
    ("Account",      "chart of accounts; AccountType, AccountSubType, CurrentBalance"),
    ("Item",         "products and services sold; UnitPrice, Type"),
    ("Invoice",      "sales invoices; CustomerRef, TxnDate, TotalAmt, Balance"),
    ("Bill",         "vendor bills; VendorRef, TxnDate, TotalAmt, Balance"),
    ("Payment",      "customer payments received; depositToAccount"),
    ("BillPayment",  "payments made to vendors"),
    ("Deposit",      "money in to a bank account"),
    ("Purchase",     "cash/check/cc expenses; PaymentType in (Cash,Check,CreditCard)"),
    ("Transfer",     "transfers between accounts"),
    ("JournalEntry", "manual journal entries"),
    ("CreditMemo",   "customer credit memos"),
    ("RefundReceipt", "refunds issued"),
    ("SalesReceipt", "cash sales (no AR)"),
    ("Estimate",     "quotes / estimates"),
    ("PurchaseOrder","purchase orders to vendors"),
    ("CompanyInfo",  "single-row company metadata"),
]

QBO_REPORTS = [
    "ProfitAndLoss",
    "ProfitAndLossDetail",
    "BalanceSheet",
    "BalanceSheetDetail",
    "TrialBalance",
    "GeneralLedger",
    "TransactionList",
    "AgedReceivables",
    "AgedReceivableDetail",
    "AgedPayables",
    "AgedPayableDetail",
    "CustomerSales",
    "VendorExpenses",
    "CashFlow",
    "InventoryValuationSummary",
]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_qbo_entities",
            "description": (
                "List the QBO entity types you can SELECT from with qbo_query, "
                "plus the report names available via qbo_report. Call this if "
                "you're unsure which entity holds the data you need."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qbo_query",
            "description": (
                "Run a QBO SQL-like SELECT against the user's company. "
                "QBO query rules:\n"
                "  - SELECT only (no JOINs, no GROUP BY).\n"
                "  - One entity at a time: 'SELECT * FROM Invoice WHERE ...'.\n"
                "  - WHERE supports = != > >= < <= LIKE IN AND.\n"
                "  - Use MAXRESULTS to cap rows (default 100, max 1000).\n"
                "  - Date format: 'YYYY-MM-DD'.\n"
                "Examples:\n"
                "  SELECT * FROM Vendor WHERE DisplayName LIKE '%Hicks%' MAXRESULTS 10\n"
                "  SELECT * FROM Invoice WHERE TxnDate >= '2026-01-01' "
                "AND Balance > '0' MAXRESULTS 50\n"
                "  SELECT COUNT(*) FROM Bill"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "QBO SELECT statement"},
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qbo_report",
            "description": (
                "Run a QBO Report. Use this for aggregations and time-bucket "
                "summaries that the query API can't do. Call list_qbo_entities "
                "first to see available report names if unsure.\n"
                "Common params: start_date, end_date (YYYY-MM-DD), account, "
                "customer, vendor, summarize_column_by, accounting_method."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Report name. One of: ProfitAndLoss, BalanceSheet, "
                            "TrialBalance, GeneralLedger, TransactionList, "
                            "AgedReceivables, AgedPayables, CustomerSales, "
                            "VendorExpenses, CashFlow, ProfitAndLossDetail, "
                            "BalanceSheetDetail, AgedReceivableDetail, "
                            "AgedPayableDetail, InventoryValuationSummary."
                        ),
                    },
                    "params": {
                        "type": "object",
                        "description": "Querystring params for the report.",
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

_SQL_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|EXEC|MERGE)\b",
    re.IGNORECASE,
)


def _validate_sql(sql: str) -> Optional[str]:
    if not sql or not sql.strip():
        return "empty SQL"
    if not re.match(r"\s*SELECT\b", sql, re.IGNORECASE):
        return "only SELECT statements are allowed"
    if _SQL_FORBIDDEN.search(sql):
        return "forbidden keyword in SQL"
    return None


def _truncate(obj: Any, max_chars: int = 6000) -> str:
    s = json.dumps(obj, default=str)
    if len(s) > max_chars:
        return s[:max_chars] + f"\n…(truncated, {len(s) - max_chars} more chars)"
    return s


async def _tool_list_entities() -> Dict[str, Any]:
    return {
        "entities": [{"name": n, "hint": h} for n, h in QBO_ENTITIES],
        "reports": QBO_REPORTS,
    }


async def _tool_qbo_query(user_id: str, realm_id: Optional[str], sql: str) -> Dict[str, Any]:
    err = _validate_sql(sql)
    if err:
        return {"error": err, "sql": sql}
    try:
        raw = await qbo_connector.query(user_id, sql, realm_id)
    except Exception as exc:
        return {"error": f"QBO query failed: {exc}", "sql": sql}
    qr = raw.get("QueryResponse") or {}
    # QueryResponse has the rows under whichever entity name the query asked
    # for, e.g. {"Customer": [...]}. Surface the keys + counts so the agent
    # doesn't have to guess.
    entity_keys = [k for k in qr.keys() if k not in ("startPosition", "maxResults", "totalCount")]
    rows: Any = qr
    if len(entity_keys) == 1:
        rows = qr[entity_keys[0]]
    return {
        "row_count": (len(rows) if isinstance(rows, list) else None),
        "entity": (entity_keys[0] if len(entity_keys) == 1 else None),
        "rows": rows,
    }


# Period-style reports (P&L, balance, trial balance, …) accept a date window.
# Aging / transaction-list reports use their own date params, so we don't
# inject a default window for those.
_PERIOD_REPORTS = {
    "ProfitAndLoss", "ProfitAndLossDetail", "BalanceSheet", "BalanceSheetDetail",
    "TrialBalance", "GeneralLedger", "CashFlow", "CustomerSales", "VendorExpenses",
}


def _flatten_report_rows(rows: Any, depth: int = 0) -> List[str]:
    """Walk QBO's nested Header/Rows/Summary report tree into flat, readable
    'Label: value' lines. The totals a P&L answer needs (Total Income, Net
    Income, Gross Profit) live in section Summary nodes — keeping them as raw
    nested JSON made them easy for the model to miss or get truncated away."""
    if isinstance(rows, dict):
        rows = rows.get("Row") or []
    if not isinstance(rows, list):
        return []

    out: List[str] = []
    indent = "  " * depth
    for row in rows:
        if not isinstance(row, dict):
            continue
        header = row.get("Header") or {}
        nested = row.get("Rows")
        col_data = row.get("ColData") or []
        summary = row.get("Summary") or {}

        hcols = header.get("ColData") or []
        if hcols and hcols[0].get("value"):
            out.append(f"{indent}{hcols[0]['value']}")

        if col_data and not header and not nested:
            label = col_data[0].get("value") or "(row)"
            vals = [c.get("value", "") for c in col_data[1:] if isinstance(c, dict) and c.get("value")]
            out.append(f"{indent}{label}: {' | '.join(vals)}" if vals else f"{indent}{label}")

        if nested:
            out.extend(_flatten_report_rows(nested, depth + 1))

        scols = summary.get("ColData") or []
        if scols and scols[0].get("value"):
            label = scols[0]["value"]
            vals = [c.get("value", "") for c in scols[1:] if isinstance(c, dict) and c.get("value")]
            out.append(f"{indent}{label}: {' | '.join(vals)}" if vals else f"{indent}{label}")

    return out


async def _tool_qbo_report(
    user_id: str,
    realm_id: Optional[str],
    name: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if name not in QBO_REPORTS:
        return {"error": f"unknown report {name!r}; valid: {QBO_REPORTS}"}
    params = dict(params or {})
    # Bare "what's my revenue?" gives no date window. Default period reports to
    # fiscal-year-to-date so they return data instead of an empty current period.
    if name in _PERIOD_REPORTS and not any(
        k in params for k in ("start_date", "end_date", "date_macro")
    ):
        params["date_macro"] = "This Fiscal Year-to-date"
    try:
        params_str = {k: str(v) for k, v in params.items()}
        raw = await qbo_connector.report(user_id, name, realm_id=realm_id, params=params_str)
    except Exception as exc:
        return {"error": f"QBO report {name} failed: {exc}"}
    header = raw.get("Header") or {}
    cols = (raw.get("Columns") or {}).get("Column") or []
    return {
        "report": name,
        "period": {
            "start": header.get("StartPeriod"),
            "end": header.get("EndPeriod"),
            "basis": header.get("ReportBasis"),
        },
        "columns": [c.get("ColTitle") for c in cols],
        "lines": _flatten_report_rows(raw.get("Rows") or {}),
    }


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

MAX_TURNS = 8


# Maps a QBO entity name (case-insensitive substring) → human phrase. Used by
# `_humanize_tool` to translate raw tool calls into the friendly status text
# we stream to the chat UI ("Looking up your sales data…" instead of
# "qbo_query SELECT * FROM Invoice WHERE …"). Conservative — falls back to
# a generic phrasing when no mapping fits, so non-tech users still get something
# readable.
_ENTITY_PHRASES = {
    "invoice":      "your sales invoices",
    "bill":         "your vendor bills",
    "customer":     "your customer list",
    "vendor":       "your vendor list",
    "account":      "your chart of accounts",
    "item":         "your items",
    "payment":      "customer payments received",
    "billpayment":  "vendor payments you've made",
    "deposit":      "your deposits",
    "purchase":     "your cash/check expenses",
    "transfer":     "transfers between accounts",
    "journalentry": "your journal entries",
    "creditmemo":   "credit memos",
    "estimate":     "your estimates",
    "salesreceipt": "cash sales",
    "purchaseorder": "your purchase orders",
}

_REPORT_PHRASES = {
    "profitandloss":         "your P&L",
    "profitandlossdetail":   "your detailed P&L",
    "balancesheet":          "your balance sheet",
    "balancesheetdetail":    "your detailed balance sheet",
    "trialbalance":          "the trial balance",
    "generalledger":         "the general ledger",
    "transactionlist":       "the transactions list",
    "agedreceivables":       "the AR aging",
    "agedreceivabledetail":  "the detailed AR aging",
    "agedpayables":          "the AP aging",
    "agedpayabledetail":     "the detailed AP aging",
    "customersales":         "customer sales totals",
    "vendorexpenses":        "vendor spend totals",
    "cashflow":              "the cash flow report",
    "inventoryvaluationsummary": "the inventory valuation",
}


def _humanize_tool(name: str, args: Dict[str, Any]) -> str:
    """Map a tool invocation to a friendly status line. Reads the relevant
    args (entity in the SQL, report name) and picks a non-jargon phrase."""
    if name == "list_qbo_entities":
        return "Figuring out where this data lives in QuickBooks…"
    if name == "qbo_query":
        sql = (args.get("sql") or "").lower()
        for key, phrase in _ENTITY_PHRASES.items():
            if f"from {key}" in sql:
                return f"Looking up {phrase}…"
        return "Running a quick lookup in QuickBooks…"
    if name == "qbo_report":
        rep = (args.get("name") or "").lower()
        if rep in _REPORT_PHRASES:
            return f"Crunching {_REPORT_PHRASES[rep]}…"
        return "Pulling a report from QuickBooks…"
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
        f"You are an analyst assistant for a QuickBooks Online company"
        + (f" — {company_name}." if company_name else ".")
        + " Use the qbo_query and qbo_report tools to look up real data before answering. "
        f"Today's date is {date.today().isoformat()}. The fiscal year may differ from the "
        "calendar year.\n\n"
        "How to think:\n"
        " - Break complex questions into smaller steps. Don't try to answer in one tool call.\n"
        " - First identify what data you need; then pick the right tool; then verify the result.\n"
        " - If the user's question implies a comparison (this month vs last month, top N, etc.), "
        "run separate lookups and reason over them.\n\n"
        "Where the numbers live (IMPORTANT):\n"
        " - Revenue / income / sales / turnover, expenses, COGS, gross profit, net "
        "profit/income: ALWAYS use qbo_report('ProfitAndLoss'). Do NOT read these off "
        "Account.CurrentBalance — income and expense accounts do not carry a usable running "
        "balance in QBO (it is typically 0). The P&L report is the only correct source, and "
        "its totals (Total Income, Gross Profit, Net Income) come back in the report's `lines`.\n"
        " - For a specific window pass start_date and end_date (YYYY-MM-DD). If the user gave "
        "no period, omit them — the report defaults to this fiscal year-to-date.\n"
        " - Assets, liabilities, equity, bank/cash balances: use qbo_report('BalanceSheet'), or "
        "Account.CurrentBalance for balance-sheet accounts (those DO carry a balance).\n"
        " - Outstanding receivables/payables: AgedReceivables / AgedPayables reports.\n\n"
        "Tool-use rules:\n"
        " - Treat the company-specific brief as your directory/map: it contains company "
        "metadata, account IDs/names, customers, vendors, items, and transaction volume hints.\n"
        " - Do not call list_qbo_entities when the brief already tells you which entity/report "
        "to use; go straight to qbo_query or qbo_report with the relevant names/IDs.\n"
        " - The brief is allowed for entity selection and spelling only. For live balances, "
        "open amounts, period totals, and transaction lists, verify with qbo_query/qbo_report.\n"
        " - Prefer qbo_query for entity lookups and detail rows.\n"
        " - Prefer qbo_report for P&L/Balance/AR-aging/AP-aging/cashflow/totals.\n"
        " - qbo_query cannot SUM or GROUP BY — for any total or aggregate, use qbo_report.\n"
        " - Don't invent rows you didn't see in a tool result.\n"
        " - Keep tool outputs small — use MAXRESULTS, narrow date ranges.\n"
        " - If a query returns no rows, try widening: drop filters, list_qbo_entities, "
        "or use a report instead.\n\n"
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


async def _company_name(user_id: str, realm_id: Optional[str]) -> Optional[str]:
    try:
        info = await qbo_connector.get_company_info(user_id, realm_id)
        return info.get("CompanyName") or info.get("LegalName")
    except Exception:
        return None


async def _dispatch_tool(
    user_id: str,
    realm_id: Optional[str],
    name: str,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    if name == "list_qbo_entities":
        return await _tool_list_entities()
    if name == "qbo_query":
        return await _tool_qbo_query(user_id, realm_id, args.get("sql", ""))
    if name == "qbo_report":
        return await _tool_qbo_report(
            user_id, realm_id, args.get("name", ""), args.get("params") or {},
        )
    return {"error": f"unknown tool {name!r}"}


async def run_qb_chat(
    user_id: str,
    realm_id: Optional[str],
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """ReAct loop. Streams status + tool events + final answer tokens."""
    yield {"type": "status", "content": "Reading your question…"}

    company_name = await _company_name(user_id, realm_id)

    brief: Optional[str] = None
    if realm_id:
        try:
            brief = await get_or_refresh_brief(user_id, realm_id)
        except Exception as exc:
            logger.warning(f"qb-chat brief load failed: {exc}")

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
            logger.exception("qb-chat planner LLM failed")
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
                logger.exception("qb-chat answer stream failed")
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

        # Run each tool sequentially. (QBO has rate limits — parallel would
        # only help marginally.)
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            # Friendly status before the technical tool_call event so non-tech
            # users see "Looking up your sales invoices…" while the trace
            # still carries the raw SQL for developers / power users.
            yield {"type": "status", "content": _humanize_tool(name, args)}
            yield {"type": "tool_call", "name": name, "arguments": args}
            result = await _dispatch_tool(user_id, realm_id, name, args)
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
