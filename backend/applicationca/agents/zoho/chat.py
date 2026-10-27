"""
Zoho Books chat agent — ReAct over the Zoho Books REST API.

Zoho has NO SQL query language (unlike QBO's /query endpoint), so the QBO
qbo_query / qbo_report tools are replaced by three Zoho-appropriate tools:

  list_zoho_entities()
      Returns the Zoho resources the agent can query, plus a short hint of what
      each one represents and which filters it supports.

  zoho_list(resource, filters)
      Lists a single resource (contacts/customers, vendors, items,
      chartofaccounts, invoices, bills, banktransactions) via the matching
      zoho_connector list_* method, with optional date/status/name filters.
      Returns normalized rows. There is no JOIN / GROUP BY — Zoho filters by
      query params and partial name matches, NOT case-sensitive SQL.

  zoho_report(name, params)
      Runs a Zoho Books report (profitandloss / balancesheet / trialbalance)
      via zoho_connector.get_report. `params` is forwarded as querystring
      (from_date, to_date, …). Report availability varies by edition, so this
      degrades gracefully: on error it tells the user instead of failing the run.

The organization is resolved from the authenticated user's selected Zoho org
(`organization_id`); the agent never sees it directly.

The agent iterates up to MAX_TURNS, then streams the final answer.

Stream events (NDJSON) match the TallyChat shape (unchanged from QBO so the
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
from datetime import date
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from applicationca.services.zoho_bank import list_bank_txns
from applicationca.services.zoho_connector import zoho_connector
from applicationca.services.zoho_brief import get_or_refresh_brief
from config.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

# (resource, hint, supported filters). The `resource` is the literal value the
# agent passes to zoho_list; it maps to a zoho_connector.list_* method.
ZOHO_ENTITIES = [
    ("customers", "buyers / customers; contact_name, company, email, outstanding AR balance",
     "search_text (partial name), status"),
    ("vendors", "suppliers / vendors; contact_name, company, outstanding AP balance",
     "search_text (partial name), status"),
    ("items", "products and services you sell; name, rate",
     "search_text (partial name)"),
    ("chartofaccounts", "chart of accounts; account_name, account_type, balance",
     "(no filters — returns the full chart)"),
    ("invoices", "sales invoices; customer_name, date, total, balance, status",
     "date_start, date_end (YYYY-MM-DD), status (sent/draft/overdue/paid/unpaid), customer_name"),
    ("bills", "vendor bills; vendor_name, date, total, balance, status",
     "date_start, date_end (YYYY-MM-DD), status (open/overdue/paid/partially_paid), vendor_name"),
    ("banktransactions", "bank account transactions (deposits / withdrawals / transfers)",
     "account_id, date_start, date_end (YYYY-MM-DD)"),
]

# Zoho Books report endpoint names. Availability varies by edition; zoho_report
# degrades gracefully when one is missing.
ZOHO_REPORTS = [
    "profitandloss",
    "balancesheet",
    "trialbalance",
    "cashflow",
    "movementofequity",
]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_zoho_entities",
            "description": (
                "List the Zoho Books resources you can query with zoho_list, "
                "plus the report names available via zoho_report, with the "
                "filters each one accepts. Call this if you're unsure which "
                "resource holds the data you need."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "zoho_list",
            "description": (
                "List a single Zoho Books resource for the user's organization. "
                "Zoho has no SQL — you cannot JOIN, GROUP BY, or SUM here. "
                "Filter via the `filters` object only.\n"
                "Rules:\n"
                "  - One resource per call.\n"
                "  - Name matching is partial and case-insensitive — pass "
                "search_text='hicks' to find 'Hicks & Co'.\n"
                "  - Dates are 'YYYY-MM-DD'. Use date_start / date_end to window "
                "invoices, bills, banktransactions.\n"
                "  - For any total / aggregate (revenue, expenses, net profit), "
                "use zoho_report instead — this tool only returns rows.\n"
                "Examples:\n"
                "  zoho_list('vendors', {'search_text': 'hicks'})\n"
                "  zoho_list('invoices', {'date_start': '2026-01-01', "
                "'status': 'unpaid'})\n"
                "  zoho_list('chartofaccounts', {})"
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
                            "vendor_name, account_id, date_start, date_end."
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
            "name": "zoho_report",
            "description": (
                "Run a Zoho Books report. Use this for totals and time-bucket "
                "summaries that zoho_list can't compute (revenue, expenses, net "
                "profit, asset/liability balances). Call list_zoho_entities first "
                "to see available report names if unsure.\n"
                "Common params: from_date, to_date (YYYY-MM-DD). If a report is "
                "unavailable for this org you'll get an error back — tell the user "
                "rather than guessing the number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Report name. One of: profitandloss, balancesheet, "
                            "trialbalance, cashflow, movementofequity."
                        ),
                    },
                    "params": {
                        "type": "object",
                        "description": "Querystring params for the report (from_date, to_date, …).",
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
            {"name": n, "hint": h, "filters": f} for n, h, f in ZOHO_ENTITIES
        ],
        "reports": ZOHO_REPORTS,
    }


# Filter keys we translate into Zoho query params for the transaction lists.
# Zoho Books uses date_start / date_end on /invoices and /bills; everything else
# we leave to the connector defaults and post-filter on the normalized cards.
def _build_list_params(resource: str, filters: Dict[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if filters.get("date_start"):
        params["date_start"] = str(filters["date_start"])  # VERIFY Zoho date filter param name
    if filters.get("date_end"):
        params["date_end"] = str(filters["date_end"])      # VERIFY Zoho date filter param name
    if filters.get("status"):
        status = str(filters["status"]).strip().lower()
        if resource == "bills" and status == "unpaid":
            status = "open"
        params["status"] = status
    if resource == "banktransactions" and filters.get("account_id"):
        params["account_id"] = str(filters["account_id"])
    return params


def _matches_text(card: Dict[str, Any], needle: str, *fields: str) -> bool:
    needle = needle.lower().strip()
    if not needle:
        return True
    for f in fields:
        val = card.get(f)
        if val and needle in str(val).lower():
            return True
    # also probe the raw object for the common name fields
    raw = card.get("raw") or {}
    for f in ("contact_name", "company_name", "customer_name", "vendor_name", "name"):
        val = raw.get(f) if isinstance(raw, dict) else None
        if val and needle in str(val).lower():
            return True
    return False


async def _tool_zoho_list(
    user_id: str,
    organization_id: Optional[str],
    resource: str,
    filters: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    resource = (resource or "").strip().lower()
    filters = dict(filters or {})
    valid = {n for n, _h, _f in ZOHO_ENTITIES}
    if resource not in valid:
        return {"error": f"unknown resource {resource!r}; valid: {sorted(valid)}"}
    if not organization_id:
        return {"error": "no Zoho organization selected for this user"}

    params = _build_list_params(resource, filters)
    try:
        if resource == "customers":
            rows = await zoho_connector.list_customers(user_id, organization_id)
        elif resource == "vendors":
            rows = await zoho_connector.list_vendors(user_id, organization_id)
        elif resource == "items":
            rows = await zoho_connector.list_items(user_id, organization_id)
        elif resource == "chartofaccounts":
            rows = await zoho_connector.list_accounts(user_id, organization_id)
        elif resource == "invoices":
            rows = await zoho_connector.list_invoices(user_id, organization_id, params=params or None)
        elif resource == "bills":
            rows = await zoho_connector.list_bills(user_id, organization_id, params=params or None)
        elif resource == "banktransactions":
            account_id = filters.get("account_id")
            if not account_id:
                return {"error": "banktransactions requires an account_id filter"}
            today = date.today()
            rows = await list_bank_txns(
                user_id,
                account_id=str(account_id),
                start_date=str(filters.get("date_start") or date(today.year, 1, 1).isoformat()),
                end_date=str(filters.get("date_end") or today.isoformat()),
                organization_id=organization_id,
            )
        else:  # unreachable given the valid-set guard above
            return {"error": f"unhandled resource {resource!r}"}
    except Exception as exc:
        logger.warning(f"zoho_list({resource}) failed: {exc}")
        return {"error": f"Zoho list {resource} failed: {exc}", "resource": resource}

    # Client-side filtering for the bits Zoho's list params don't cover:
    # partial name matching on customers/vendors/items, and customer_name /
    # vendor_name narrowing on invoices/bills.
    search_text = filters.get("search_text") or ""
    if search_text and resource in ("customers", "vendors", "items"):
        rows = [r for r in rows if _matches_text(r, search_text, "name", "company", "email")]

    if resource == "invoices" and filters.get("customer_name"):
        cn = str(filters["customer_name"])
        rows = [
            r for r in rows
            if _matches_text(r, cn, "customer_name")
            or cn.lower() in str((r.get("raw") or {}).get("customer_name", "")).lower()
        ]
    if resource == "bills" and filters.get("vendor_name"):
        vn = str(filters["vendor_name"])
        rows = [
            r for r in rows
            if _matches_text(r, vn, "vendor_name")
            or vn.lower() in str((r.get("raw") or {}).get("vendor_name", "")).lower()
        ]

    # Cap rows so tool output stays small (mirrors QBO's MAXRESULTS discipline).
    capped = rows[:100]
    return {
        "resource": resource,
        "row_count": len(rows),
        "returned": len(capped),
        "rows": capped,
    }


def _flatten_report_rows(node: Any, depth: int = 0, out: Optional[List[str]] = None) -> List[str]:
    """Walk Zoho's nested report tree into flat, readable 'Label: value' lines.

    Zoho Books reports are returned as nested rows; the totals an answer needs
    (Gross Profit, Net Profit, Total Assets …) live in summary/total nodes that
    are easy for the model to miss inside raw nested JSON. We flatten so each
    meaningful label + amount is one line. The exact key names differ a little
    between report endpoints, so we probe the common ones defensively.
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

    # A row's display name lives under one of these keys depending on endpoint.
    label = (
        node.get("name")
        or node.get("label")
        or node.get("account_name")
        or node.get("particulars")
    )
    # Its value under one of these.
    value = None
    for k in ("total", "amount", "balance", "value", "net_total", "debit", "credit"):
        if node.get(k) not in (None, ""):
            value = node.get(k)
            break

    if label and value not in (None, ""):
        out.append(f"{indent}{label}: {value}")
    elif label:
        out.append(f"{indent}{label}")

    # Recurse into any nested collections.
    for k in ("account_transactions", "rows", "child", "children", "sub_sections",
              "sections", "transactions", "items"):
        if isinstance(node.get(k), list):
            _flatten_report_rows(node[k], depth + 1, out)

    return out


# Period-style reports accept a date window. Default them to fiscal-year-to-date
# when the user gave no period so they return data instead of an empty range.
_PERIOD_REPORTS = {"profitandloss", "cashflow", "movementofequity"}


async def _tool_zoho_report(
    user_id: str,
    organization_id: Optional[str],
    name: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    name = (name or "").strip().lower()
    if name not in ZOHO_REPORTS:
        return {"error": f"unknown report {name!r}; valid: {ZOHO_REPORTS}"}
    if not organization_id:
        return {"error": "no Zoho organization selected for this user"}

    params = dict(params or {})
    # Bare "what's my revenue?" gives no window. Default period reports to the
    # current fiscal year-to-date. VERIFY: confirm Zoho accepts these macro/date
    # param names; if not, the report still runs against its own defaults.
    if name in _PERIOD_REPORTS and not any(
        k in params for k in ("from_date", "to_date", "date_macro")
    ):
        today = date.today()
        params["from_date"] = date(today.year, 1, 1).isoformat()  # VERIFY fiscal-year start
        params["to_date"] = today.isoformat()

    try:
        params_str = {k: str(v) for k, v in params.items()}
        raw = await zoho_connector.get_report(
            user_id, name, organization_id=organization_id, params=params_str or None,
        )
    except Exception as exc:
        # Degrade gracefully — reports may not be available on every edition.
        logger.warning(f"zoho_report({name}) failed: {exc}")
        return {
            "error": (
                f"The {name} report isn't available for this organization "
                f"({exc}). Tell the user this report couldn't be fetched and, if "
                "possible, answer from list data instead."
            ),
            "report": name,
        }

    # Zoho wraps reports differently per endpoint; the payload usually lives
    # under a key named after the report (or a generic 'report'). Probe defensively.
    body = raw
    if isinstance(raw, dict):
        for key in (name, "report", "profit_and_loss", "balance_sheet",
                    "trial_balance", "cash_flow"):
            if isinstance(raw.get(key), (dict, list)):
                body = raw[key]
                break

    return {
        "report": name,
        "lines": _flatten_report_rows(body),
    }


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

MAX_TURNS = 8


# Maps a Zoho resource → human phrase, used by `_humanize_tool` to translate
# raw tool calls into the friendly status text streamed to the chat UI
# ("Looking up your customer list…" instead of "zoho_list customers"). Falls
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
    "profitandloss":    "your P&L",
    "balancesheet":     "your balance sheet",
    "trialbalance":     "the trial balance",
    "cashflow":         "the cash flow report",
    "movementofequity": "the movement of equity",
}


def _humanize_tool(name: str, args: Dict[str, Any]) -> str:
    """Map a tool invocation to a friendly status line. Reads the relevant
    args (resource, report name) and picks a non-jargon phrase."""
    if name == "list_zoho_entities":
        return "Figuring out where this data lives in Zoho Books…"
    if name == "zoho_list":
        resource = (args.get("resource") or "").lower()
        phrase = _RESOURCE_PHRASES.get(resource)
        if phrase:
            return f"Looking up {phrase}…"
        return "Running a quick lookup in Zoho Books…"
    if name == "zoho_report":
        rep = (args.get("name") or "").lower()
        if rep in _REPORT_PHRASES:
            return f"Crunching {_REPORT_PHRASES[rep]}…"
        return "Pulling a report from Zoho Books…"
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
        f"You are an analyst assistant for a Zoho Books organization"
        + (f" — {company_name}." if company_name else ".")
        + " Use the zoho_list and zoho_report tools to look up real data before "
        "answering. "
        f"Today's date is {date.today().isoformat()}. The fiscal year may differ "
        "from the calendar year.\n\n"
        "How to think:\n"
        " - Break complex questions into smaller steps. Don't try to answer in one tool call.\n"
        " - First identify what data you need; then pick the right tool; then verify the result.\n"
        " - If the user's question implies a comparison (this month vs last month, top N, etc.), "
        "run separate lookups and reason over them.\n\n"
        "Zoho is NOT SQL (IMPORTANT):\n"
        " - There is no query language, no JOIN, no GROUP BY, no SUM. You list a "
        "single resource at a time and filter it with the `filters` object.\n"
        " - Name matching is partial and case-insensitive — to find a vendor "
        "'Hicks & Co', pass search_text='hicks'. Do not assume exact casing.\n"
        " - To narrow invoices/bills, use date_start / date_end (YYYY-MM-DD), "
        "status, and customer_name / vendor_name filters.\n\n"
        "Where the numbers live (IMPORTANT):\n"
        " - Revenue / income / sales / turnover, expenses, COGS, gross profit, net "
        "profit/income: ALWAYS use zoho_report('profitandloss'). Do NOT add these "
        "up by listing invoices — that misses adjustments and isn't a real total. "
        "The P&L report totals come back in the report's `lines`.\n"
        " - For a specific window pass from_date and to_date (YYYY-MM-DD). If the "
        "user gave no period, omit them — the report defaults to this fiscal "
        "year-to-date.\n"
        " - Assets, liabilities, equity, bank/cash balances: use "
        "zoho_report('balancesheet'); chart-of-accounts balances are also on "
        "zoho_list('chartofaccounts').\n"
        " - A full trial balance: zoho_report('trialbalance').\n\n"
        "Tool-use rules:\n"
        " - Prefer zoho_list for entity lookups and detail rows.\n"
        " - Prefer zoho_report for P&L / balance / trial-balance / cash-flow / totals.\n"
        " - zoho_list cannot SUM or GROUP BY — for any total or aggregate, use zoho_report.\n"
        " - Don't invent rows you didn't see in a tool result.\n"
        " - Keep tool outputs small — narrow with filters and date ranges.\n"
        " - If a list returns no rows, try widening: drop filters, broaden "
        "search_text, call list_zoho_entities, or use a report instead.\n"
        " - If a report comes back with an error (some reports aren't available "
        "on every Zoho edition), say so plainly and answer from list data if you can.\n\n"
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


async def _company_name(user_id: str, organization_id: Optional[str]) -> Optional[str]:
    if not organization_id:
        return None
    try:
        info = await zoho_connector.get_company_info(user_id, organization_id)
        return info.get("name") or info.get("company_name")
    except Exception:
        return None


async def _dispatch_tool(
    user_id: str,
    organization_id: Optional[str],
    name: str,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    if name == "list_zoho_entities":
        return await _tool_list_entities()
    if name == "zoho_list":
        return await _tool_zoho_list(
            user_id, organization_id, args.get("resource", ""), args.get("filters") or {},
        )
    if name == "zoho_report":
        return await _tool_zoho_report(
            user_id, organization_id, args.get("name", ""), args.get("params") or {},
        )
    return {"error": f"unknown tool {name!r}"}


async def run_zoho_chat(
    *,
    user_id: str,
    organization_id: Optional[str],
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """ReAct loop. Streams status + tool events + final answer tokens."""
    yield {"type": "status", "content": "Reading your question…"}

    company_name = await _company_name(user_id, organization_id)

    brief: Optional[str] = None
    if organization_id:
        try:
            brief = await get_or_refresh_brief(user_id, organization_id)
        except Exception as exc:
            logger.warning(f"zoho-chat brief load failed: {exc}")

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
            logger.exception("zoho-chat planner LLM failed")
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
                logger.exception("zoho-chat answer stream failed")
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

        # Run each tool sequentially. (Zoho has per-org rate limits — parallel
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
            result = await _dispatch_tool(user_id, organization_id, name, args)
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
