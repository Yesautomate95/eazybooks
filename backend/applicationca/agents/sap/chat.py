"""
SAP S/4HANA Cloud chat agent — ReAct over the SAP OData reads.

Mirrors agents/zoho/chat.py. SAP (like Zoho) has NO SQL query language, so the
QBO qbo_query / qbo_report tools are replaced by SAP-appropriate tools:

  list_sap_entities()
      Returns the SAP resources the agent can query, plus a short hint of what
      each one represents and which filters it supports.

  sap_list(resource, filters)
      Lists a single resource (customers, vendors, accounts [GL chart],
      openitems [open A/P or A/R items], companies) via the matching
      sap_connector list_* method, with optional name / type filters. Returns
      the connector's normalized cards. There is no JOIN / GROUP BY — SAP reads
      are OData $filter queries, and most narrowing here is client-side partial
      name matching on the normalized cards.

  sap_trial_balance(from_date, to_date)
      Runs the SAP trial balance (sap_connector.get_trial_balance) for the
      user's company code over a posting-date window. This is SAP FI's source of
      truth for GL-account period balances — there is no separate P&L /
      balance-sheet report endpoint, so totals are read off the trial balance.

  sap_brief()
      Returns the cached company brief (chart of accounts with balances,
      customers, vendors) so the agent can ground names before querying.

The company is resolved from the authenticated user's selected SAP company code
(`company_code`); the agent never sees it directly.

The agent iterates up to MAX_TURNS, then streams the final answer.

Stream events (NDJSON) match the TallyChat shape (unchanged from QBO / Zoho so
the frontend stays identical):
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

from applicationca.services.sap_connector import sap_connector
from applicationca.services.sap_brief import get_or_refresh_brief
from config.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

# (resource, hint, supported filters). The `resource` is the literal value the
# agent passes to sap_list; it maps to a sap_connector.list_* method.
SAP_ENTITIES = [
    ("customers", "business partners with a customer role; name, A/R counterpart",
     "search_text (partial name)"),
    ("vendors", "business partners with a supplier role; name, A/P counterpart",
     "search_text (partial name)"),
    ("accounts", "GL chart of accounts (revenue / expense / bank / cash); name, account_type. "
     "In SAP FI these ARE the ledgers — there is no separate item master.",
     "search_text (partial name), account_type (partial match)"),
    ("openitems", "open (uncleared) sub-ledger items; A/P = supplier, A/R = customer",
     "kind ('payable'|'ap'|'k' for A/P, 'receivable'|'ar'|'d' for A/R; default payable)"),
    ("companies", "company codes you can query / post to",
     "(no filters — returns the configured company codes)"),
]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_sap_entities",
            "description": (
                "List the SAP resources you can query with sap_list, plus a note "
                "that sap_trial_balance gives GL-account period balances and "
                "sap_brief gives a cached company summary, with the filters each "
                "one accepts. Call this if you're unsure which resource holds the "
                "data you need."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sap_list",
            "description": (
                "List a single SAP resource for the user's company code. "
                "SAP has no SQL — you cannot JOIN, GROUP BY, or SUM here. "
                "Filter via the `filters` object only.\n"
                "Rules:\n"
                "  - One resource per call.\n"
                "  - Name matching is partial and case-insensitive — pass "
                "search_text='hicks' to find 'Hicks & Co'.\n"
                "  - In SAP FI, GL 'accounts' are the line ledgers — there is no "
                "item/product master. To find a revenue or expense ledger, list "
                "'accounts' and filter by account_type or search_text.\n"
                "  - For GL-account balances / totals (revenue, expenses, asset "
                "balances) use sap_trial_balance instead — this tool only returns "
                "rows / master records.\n"
                "Examples:\n"
                "  sap_list('vendors', {'search_text': 'hicks'})\n"
                "  sap_list('accounts', {'account_type': 'revenue'})\n"
                "  sap_list('openitems', {'kind': 'receivable'})"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {
                        "type": "string",
                        "description": (
                            "One of: customers, vendors, accounts, openitems, companies."
                        ),
                    },
                    "filters": {
                        "type": "object",
                        "description": (
                            "Optional filters: search_text (partial name), "
                            "account_type (for accounts), kind (for openitems: "
                            "payable/ap/k or receivable/ar/d)."
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
            "name": "sap_trial_balance",
            "description": (
                "Run the SAP trial balance for the user's company code over a "
                "posting-date window. Use this for GL-account period balances and "
                "any total / aggregate (revenue, expenses, asset / liability "
                "balances) — SAP FI has no separate P&L or balance-sheet report "
                "endpoint, so these numbers come off the trial balance.\n"
                "Params: from_date, to_date (YYYY-MM-DD). If omitted, defaults to "
                "this calendar year-to-date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_date": {
                        "type": "string",
                        "description": "Posting-date window start, YYYY-MM-DD.",
                    },
                    "to_date": {
                        "type": "string",
                        "description": "Posting-date window end, YYYY-MM-DD.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sap_brief",
            "description": (
                "Return the cached company brief — chart of accounts with period "
                "balances, plus customer and vendor names. Call this to ground the "
                "real account / customer / vendor spellings before querying, or "
                "for a quick high-level summary."
            ),
            "parameters": {"type": "object", "properties": {}},
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
            {"name": n, "hint": h, "filters": f} for n, h, f in SAP_ENTITIES
        ],
        "balances_via": "sap_trial_balance(from_date, to_date)",
        "summary_via": "sap_brief()",
    }


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
    for f in ("name", "OrganizationBPName1", "GLAccountName", "BusinessPartnerName"):
        val = raw.get(f) if isinstance(raw, dict) else None
        if val and needle in str(val).lower():
            return True
    return False


# Map the openitems `kind` filter to SAP's FinancialAccountType code.
# K = A/P (supplier / payable), D = A/R (customer / receivable).
def _resolve_open_item_type(kind: str) -> str:
    kind = (kind or "").strip().lower()
    if kind in ("receivable", "receivables", "ar", "a/r", "d", "customer", "customers"):
        return "D"
    # default to payables (A/P) — mirrors zoho list_bills being the default.
    return "K"


async def _tool_sap_list(
    user_id: str,
    company_code: Optional[str],
    resource: str,
    filters: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    resource = (resource or "").strip().lower()
    filters = dict(filters or {})
    valid = {n for n, _h, _f in SAP_ENTITIES}
    if resource not in valid:
        return {"error": f"unknown resource {resource!r}; valid: {sorted(valid)}"}
    if resource != "companies" and not company_code:
        return {"error": "no SAP company code selected for this user"}

    try:
        if resource == "customers":
            rows = await sap_connector.list_customers(user_id, company_code)
        elif resource == "vendors":
            rows = await sap_connector.list_vendors(user_id, company_code)
        elif resource == "accounts":
            rows = await sap_connector.list_accounts(user_id, company_code)
        elif resource == "openitems":
            fat = _resolve_open_item_type(filters.get("kind") or "")
            rows = await sap_connector.list_open_items(
                user_id, company_code, financial_account_type=fat,
            )
        elif resource == "companies":
            rows = await sap_connector.list_companies(user_id)
        else:  # unreachable given the valid-set guard above
            return {"error": f"unhandled resource {resource!r}"}
    except Exception as exc:
        logger.warning(f"sap_list({resource}) failed: {exc}")
        return {"error": f"SAP list {resource} failed: {exc}", "resource": resource}

    # Client-side filtering for the bits SAP's OData params don't cover here:
    # partial name matching on customers/vendors/accounts, and account_type
    # narrowing on accounts.
    search_text = filters.get("search_text") or ""
    if search_text and resource in ("customers", "vendors", "accounts"):
        rows = [r for r in rows if _matches_text(r, search_text, "name")]

    if resource == "accounts" and filters.get("account_type"):
        at = str(filters["account_type"]).lower()
        rows = [r for r in rows if at in str(r.get("account_type") or "").lower()]

    # Cap rows so tool output stays small (mirrors QBO's MAXRESULTS discipline).
    capped = rows[:100]
    return {
        "resource": resource,
        "row_count": len(rows),
        "returned": len(capped),
        "rows": capped,
    }


def _tb_lines(rows: List[Dict[str, Any]], n: int = 80) -> List[str]:
    """Flatten trial-balance rows into readable 'GLAccount Name: balance' lines.

    SAP's C_TRIALBALANCE rows carry the GL account, an optional name, and the
    ending balance under one of a few common field names; probe defensively so
    the model sees the number regardless of the tenant's exact field naming.
    """
    out: List[str] = []
    for r in rows[:n]:
        gl = r.get("GLAccount") or r.get("GLAccount_Text") or "?"
        name = (r.get("GLAccountName") or r.get("GLAccountLongName")
                or r.get("AccountName") or "")
        bal = None
        for k in ("EndingBalanceAmtInCoCodeCrcy", "EndingBalanceAmount",
                  "BalanceAmtInCoCodeCrcy", "AmountInCompanyCodeCurrency"):
            if r.get(k) not in (None, ""):
                bal = r.get(k)
                break
        label = f"{gl}" + (f" {name}" if name else "")
        out.append(f"{label}: {bal if bal is not None else 0}")
    return out


async def _tool_sap_trial_balance(
    user_id: str,
    company_code: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
) -> Dict[str, Any]:
    if not company_code:
        return {"error": "no SAP company code selected for this user"}

    # Bare "what's my revenue?" gives no window. Default to the current calendar
    # year-to-date so the trial balance returns data instead of an empty range.
    today = date.today()
    fd = (from_date or "").strip() or date(today.year, 1, 1).isoformat()
    td = (to_date or "").strip() or today.isoformat()

    try:
        rows = await sap_connector.get_trial_balance(user_id, company_code, fd, td)
    except Exception as exc:
        logger.warning(f"sap_trial_balance failed: {exc}")
        return {
            "error": (
                f"The trial balance couldn't be fetched for this company "
                f"({exc}). Tell the user this and, if possible, answer from list "
                "data instead."
            ),
            "from_date": fd,
            "to_date": td,
        }

    return {
        "report": "trialbalance",
        "from_date": fd,
        "to_date": td,
        "row_count": len(rows),
        "lines": _tb_lines(rows),
    }


async def _tool_sap_brief(user_id: str, company_code: Optional[str]) -> Dict[str, Any]:
    if not company_code:
        return {"error": "no SAP company code selected for this user"}
    try:
        brief = await get_or_refresh_brief(user_id, company_code)
    except Exception as exc:
        logger.warning(f"sap_brief tool failed: {exc}")
        return {"error": f"company brief unavailable: {exc}"}
    return {"brief": brief or "(no brief available)"}


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

MAX_TURNS = 8


# Maps a SAP resource → human phrase, used by `_humanize_tool` to translate
# raw tool calls into the friendly status text streamed to the chat UI
# ("Looking up your customer list…" instead of "sap_list customers"). Falls
# back to a generic phrasing so non-tech users still get something readable.
_RESOURCE_PHRASES = {
    "customers":  "your customer list",
    "vendors":    "your vendor list",
    "accounts":   "your chart of accounts",
    "openitems":  "your open items",
    "companies":  "your company codes",
}


def _humanize_tool(name: str, args: Dict[str, Any]) -> str:
    """Map a tool invocation to a friendly status line. Reads the relevant
    args (resource) and picks a non-jargon phrase."""
    if name == "list_sap_entities":
        return "Figuring out where this data lives in SAP…"
    if name == "sap_list":
        resource = (args.get("resource") or "").lower()
        phrase = _RESOURCE_PHRASES.get(resource)
        if phrase:
            return f"Looking up {phrase}…"
        return "Running a quick lookup in SAP…"
    if name == "sap_trial_balance":
        return "Crunching the trial balance…"
    if name == "sap_brief":
        return "Reading your company summary…"
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
        f"You are an analyst assistant for an SAP S/4HANA company"
        + (f" — {company_name}." if company_name else ".")
        + " Use the sap_list and sap_trial_balance tools to look up real data "
        "before answering. "
        f"Today's date is {date.today().isoformat()}. The fiscal year may differ "
        "from the calendar year.\n\n"
        "How to think:\n"
        " - Break complex questions into smaller steps. Don't try to answer in one tool call.\n"
        " - First identify what data you need; then pick the right tool; then verify the result.\n"
        " - If the user's question implies a comparison (this period vs last period, top N, etc.), "
        "run separate lookups and reason over them.\n\n"
        "SAP is NOT SQL (IMPORTANT):\n"
        " - There is no query language, no JOIN, no GROUP BY, no SUM. You list a "
        "single resource at a time and filter it with the `filters` object.\n"
        " - Name matching is partial and case-insensitive — to find a vendor "
        "'Hicks & Co', pass search_text='hicks'. Do not assume exact casing.\n"
        " - In SAP FI, GL accounts ARE the line ledgers — there is no separate "
        "item / product master. Where you'd look up an item elsewhere, list "
        "'accounts' here.\n\n"
        "Where the numbers live (IMPORTANT):\n"
        " - Revenue / income / sales, expenses, COGS, asset / liability / equity "
        "balances, and any GL total: ALWAYS use sap_trial_balance. Do NOT add up "
        "open items or master records — that isn't a real total. The trial "
        "balance totals come back in the report's `lines`.\n"
        " - For a specific window pass from_date and to_date (YYYY-MM-DD). If the "
        "user gave no period, omit them — it defaults to this calendar "
        "year-to-date.\n"
        " - Open (uncleared) A/P and A/R items: sap_list('openitems', "
        "{'kind': 'payable'|'receivable'}).\n"
        " - Chart-of-accounts names and types: sap_list('accounts'); for cached "
        "balances and a quick summary, sap_brief().\n\n"
        "Tool-use rules:\n"
        " - Prefer sap_list for entity lookups and detail rows.\n"
        " - Prefer sap_trial_balance for balances / totals.\n"
        " - sap_list cannot SUM or GROUP BY — for any total or aggregate, use sap_trial_balance.\n"
        " - Don't invent rows you didn't see in a tool result.\n"
        " - Keep tool outputs small — narrow with search_text where you can.\n"
        " - If a list returns no rows, try widening: drop filters, broaden "
        "search_text, call list_sap_entities, or use sap_brief / sap_trial_balance instead.\n"
        " - If the trial balance comes back with an error, say so plainly and "
        "answer from list data if you can.\n\n"
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


async def _company_name(user_id: str, company_code: Optional[str]) -> Optional[str]:
    # SAP has no get_company_info; the company name is surfaced via list_companies
    # (falling back to the company code itself).
    if not company_code:
        return None
    try:
        companies = await sap_connector.list_companies(user_id)
        match = next((c for c in companies if c.get("company_code") == company_code), None)
        if match and match.get("name"):
            return match["name"]
    except Exception:
        pass
    return company_code


async def _dispatch_tool(
    user_id: str,
    company_code: Optional[str],
    name: str,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    if name == "list_sap_entities":
        return await _tool_list_entities()
    if name == "sap_list":
        return await _tool_sap_list(
            user_id, company_code, args.get("resource", ""), args.get("filters") or {},
        )
    if name == "sap_trial_balance":
        return await _tool_sap_trial_balance(
            user_id, company_code, args.get("from_date"), args.get("to_date"),
        )
    if name == "sap_brief":
        return await _tool_sap_brief(user_id, company_code)
    return {"error": f"unknown tool {name!r}"}


async def run_sap_chat(
    *,
    user_id: str,
    company_code: Optional[str],
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """ReAct loop. Streams status + tool events + final answer tokens."""
    yield {"type": "status", "content": "Reading your question…"}

    company_name = await _company_name(user_id, company_code)

    brief: Optional[str] = None
    if company_code:
        try:
            brief = await get_or_refresh_brief(user_id, company_code)
        except Exception as exc:
            logger.warning(f"sap-chat brief load failed: {exc}")

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
            logger.exception("sap-chat planner LLM failed")
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
                logger.exception("sap-chat answer stream failed")
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

        # Run each tool sequentially. (SAP enforces per-tenant rate limits —
        # parallel would only help marginally.)
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            # Friendly status before the technical tool_call event so non-tech
            # users see "Looking up your customer list…" while the trace still
            # carries the raw arguments for developers / power users.
            yield {"type": "status", "content": _humanize_tool(name, args)}
            yield {"type": "tool_call", "name": name, "arguments": args}
            result = await _dispatch_tool(user_id, company_code, name, args)
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
