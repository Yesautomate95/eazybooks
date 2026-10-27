"""ReAct-style agent for TallyChat. LLM iterates with three tools until it
has enough data to answer, then writes a natural-language answer.

Tools: list_tables, describe_table, run_sql.
Every run_sql is validated (SELECT-only, tenant filter enforced).

Streams a sequence of events the frontend renders as an agent trace:
  {type: "status",       content}   progress text
  {type: "tool_call",    name, arguments}
  {type: "tool_result",  name, content}      (non-SQL tools, truncated)
  {type: "sql",          content}            SQL about to execute
  {type: "result",       content, sql}       rows (list of dicts)
  {type: "answer_delta", content}            streaming tokens of final answer
  {type: "error",        content}
  {type: "done"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from openai import AsyncOpenAI
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.tally_db_sync import load_company_brief
from config.settings import settings

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 12
MAX_ROWS_TO_LLM = 50
MAX_ROWS_TO_UI = 500
# Cap prior turns replayed into the prompt. History is re-sent on every
# iteration of the tool loop, so unbounded growth multiplies token cost; keep
# only the most recent exchanges for follow-up context.
MAX_HISTORY_MESSAGES = 16
TALLY_TABLE_PREFIXES = ("mst_", "trn_")

SCHEMA_OVERVIEW = """\
Postgres schema (Tally data, multi-tenant). EVERY row has user_id + company_name —
every query MUST filter on both.

=== HOT TABLES — exact columns ===

mst_ledger (one row per ledger account):
  guid               varchar  — internal PK, DO NOT use in joins
  name               varchar  — ledger name, e.g. 'HDFC Bank', 'Idea International'
  parent             varchar  — PARENT GROUP NAME (e.g. 'Bank Accounts', 'Sundry Debtors'), NOT a guid
  alias              varchar
  is_revenue         smallint
  is_deemedpositive  smallint
  opening_balance    numeric
  closing_balance    numeric  — *** use this for current balance / closing-balance questions ***
  mailing_name/address/state/country/pincode  varchar
  email              varchar
  it_pan, gstn, gst_registration_type, gst_supply_type, gst_duty_head  varchar
  tax_rate           numeric
  bank_account_holder, bank_account_number, bank_ifsc, bank_name, bank_branch  varchar
  bill_credit_period integer
  user_id, company_name  varchar
  >>> mst_ledger has NO column called `amount`, `balance`, `ledger_name`, or `group`.

mst_group (one row per account group, e.g. 'Bank Accounts', 'Sundry Debtors'):
  guid               varchar  — PK, do not join on
  name               varchar  — group name
  parent             varchar  — PARENT GROUP NAME (empty for top-level)
  primary_group      varchar  — top-of-tree classification
  is_revenue, is_deemedpositive, is_reserved, affects_gross_profit  smallint
  sort_position      integer
  user_id, company_name  varchar

mst_stock_item:
  guid, name, parent (stock group NAME), alias, description, notes, part_number  varchar
  uom, alternate_uom  varchar
  conversion          integer
  opening_balance, opening_rate, opening_value     numeric
  closing_balance, closing_rate, closing_value     numeric  — *** use these for stock value ***
  costing_method      varchar
  gst_type_of_supply, gst_hsn_code, gst_hsn_description, gst_taxability  varchar
  gst_rate            numeric
  user_id, company_name

trn_voucher (voucher headers — populated for desktop-loader-synced companies, empty for masters-only ones):
  guid                    varchar  — PK, JOIN KEY to trn_accounting.guid
  date                    date     — voucher date
  voucher_type            varchar  — e.g. 'Sales', 'Purchase', 'Receipt', 'Payment', 'Journal'
  voucher_number          varchar
  reference_number        varchar
  reference_date          date
  narration               varchar
  party_name              varchar
  place_of_supply         varchar
  is_invoice              smallint
  is_accounting_voucher   smallint
  is_inventory_voucher    smallint
  is_order_voucher        smallint
  user_id, company_name

trn_accounting (one row per debit/credit line inside a voucher — populated when trn_voucher is):
  guid        varchar  — matches trn_voucher.guid (NOT a PK here)
  ledger      varchar  — *** column is `ledger`, NOT `ledger_name` ***
  amount      numeric  — debit positive, credit negative
  amount_forex numeric
  currency    varchar
  user_id, company_name

=== OTHER TABLES (use describe_table if you need them) ===
mst_vouchertype, mst_stock_group, mst_godown, mst_uom,
mst_cost_category, mst_cost_centre, mst_attendance_type, mst_employee,
mst_payhead, mst_gst_effective_rate,
mst_opening_batch_allocation, mst_opening_bill_allocation,
mst_stockitem_standard_cost, mst_stockitem_standard_price,
trn_inventory, trn_bank, trn_bill, trn_batch,
trn_cost_centre, trn_cost_category_centre, trn_cost_inventory_category_centre,
trn_inventory_accounting, trn_employee, trn_payhead, trn_attendance,
trn_closingstock_ledger.

=== JOIN RULES ===
  trn_voucher.guid  = trn_accounting.guid       (voucher → its accounting lines)
  trn_voucher.guid  = trn_inventory.guid        (voucher → its stock movements)
  mst_ledger.parent = mst_group.name            (ledger → its group; JOIN ON NAME, not guid)
  mst_stock_item.parent = mst_stock_group.name  (stock item → its group; JOIN ON NAME)
  trn_accounting.ledger = mst_ledger.name       (accounting line → its ledger; JOIN ON NAME)

NEVER join masters on `guid` — `parent` columns store the parent's NAME, and guid
schemes differ by sync path (real Tally guids vs synthetic), so guids won't reliably
match across tables. Always join masters on NAME.

=== DATA STATE (varies BY COMPANY) ===
- Master tables (mst_*) are populated for any synced company.
- Transaction tables (trn_*) depend on HOW the company was synced:
    * Desktop-loader-synced companies HAVE transactions (vouchers, accounting lines,
      bank/bill/inventory rows) — answer period questions ("last month's sales",
      "monthly salary expense") from them normally.
    * Masters-only ("Sync from Tally" button) companies have EMPTY trn_* tables.
  Do NOT assume empty. Run a quick COUNT (or check the company brief below) first.
  Only if trn_* is genuinely empty for THIS company, say transactions aren't synced
  for it (the user may have done a masters-only sync) — never invent zeros.
- `closing_balance` and `closing_value` reflect the moment of the last sync —
  treat them as "current" balances. Do NOT chase voucher dates to answer
  closing-balance questions.
"""

SYSTEM_PROMPT_TMPL = """\
You are a Tally data analyst. You answer the user's question about their accounting
data by querying Postgres via tools and then explaining the result clearly.

Tenant scoping (MANDATORY for every query):
  user_id      = '{user_id}'
  company_name = '{company_name}'
Every run_sql MUST include:  WHERE ... user_id = '{user_id}' AND company_name = '{company_name}'
(or the equivalent filter applied to each table in a subquery / JOIN).

Schema you may query:
{schema}

=== NAME-MATCHING STRATEGY (important — most failures come from here) ===

User-given names (ledgers, groups, stock items, parties) are rarely exact matches
for what's in the DB. They may differ by case, trailing spaces, punctuation,
abbreviations ('Pvt Ltd' vs 'Pvt. Ltd.'), group suffixes, or typos.

Rules:
  1. When the user names a specific entity, search with ILIKE and wildcards FIRST:
       WHERE name ILIKE '%idea%inter%'             ← matches 'Idea International'
       WHERE name ILIKE '%expert%facility%'        ← matches any variation
       WHERE parent ILIKE '%bank%od%'              ← matches 'Bank OD A/c', 'Bank Overdraft'
  2. Do NOT combine a name filter with a parent filter unless the user explicitly
     specified both. Example BAD query:
       WHERE name = 'Idea Interational' AND parent = 'Sundry Debtors'   ← over-restrictive
     GOOD:
       WHERE name ILIKE '%idea%inter%'                                   ← finds it
  3. If 0 rows: widen the pattern (fewer words) and try again. If still 0, run a
     small diagnostic SELECT to list candidates, e.g.
       SELECT name, parent, closing_balance FROM mst_ledger
        WHERE name ILIKE '%idea%' OR name ILIKE '%inter%'
     and report the candidates to the user rather than saying "not found".
  4. For "bank", "sundry debtors", "creditors" etc. (group-style questions),
     match the GROUP by ILIKE on mst_group.name OR on mst_ledger.parent.

=== ON SQL ERROR ===

If run_sql returns an error like 'column X does not exist' or 'relation Y does not exist':
  - Do NOT retry the same query.
  - Call describe_table(table_name) on the table you got wrong, read the actual columns,
    then rewrite the query.
  - If the same query fails again with a related error, widen: try describe_table on
    the sibling table too.
Only give up and tell the user the data isn't available after you've genuinely tried
describe_table + a rewrite.

=== BREAKDOWN PREFERENCE ===

When the user asks about a GROUP or category ("bank balance", "sundry debtors",
"outstanding to creditors", "stock value"), return a PER-ROW BREAKDOWN, not just a
rolled-up SUM. Example:
  - For "closing balance of bank":
      SELECT name, closing_balance FROM mst_ledger
       WHERE parent ILIKE '%bank%' AND closing_balance <> 0 ...
       ORDER BY ABS(closing_balance) DESC
    Then in your answer include each ledger line AND the total.
  - Only SUM alone when the user explicitly asks for the total ("total bank balance").

=== OTHER RULES ===

  - Prefer aggregates/ORDER BY/LIMIT when the answer needs many rows; don't pull everything.
  - Show money values rounded to 2 decimals in the final answer (use Indian lakh/crore
    formatting only if the user used it first).
  - Never INSERT / UPDATE / DELETE / DROP / ALTER — you have read-only access.
  - For questions that depend on voucher data (period P&L, monthly expenses, cash flow),
    query trn_voucher / trn_accounting directly — many companies HAVE this data. Only if
    they come back empty for this company, say transaction data isn't synced for it (a
    masters-only sync) — do not make up zeros.
  - Today's date: use CURRENT_DATE. Indian FY runs Apr 1 – Mar 31.
  - After enough data is gathered, STOP calling tools and write the final markdown answer.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List all Tally-related tables (mst_* and trn_*) in the database. Use this if you need to discover what's available.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "Get the column names and types for a given table. Use before writing a SQL query if you're unsure of column names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "description": "The table name, e.g. 'mst_ledger'."}
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Execute a read-only SELECT query against Postgres and return rows. "
                "Must include the tenant filter (user_id + company_name). "
                "Results are capped at 50 rows; use aggregates for large sets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A single SELECT statement."},
                    "purpose": {
                        "type": "string",
                        "description": "One short sentence explaining what this query is meant to retrieve.",
                    },
                },
                "required": ["sql", "purpose"],
            },
        },
    },
]


_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|replace|merge|copy|vacuum|analyze)\b",
    re.IGNORECASE,
)

# Match SQL string literals ('...', with '' as an escaped quote) and
# double-quoted identifiers ("...", with "" escaped). We blank these out
# before scanning for forbidden keywords so a legitimate value like
# `name ILIKE '%update%'` or a ledger literally named 'Delete A/c' doesn't
# trip the write/DDL guard.
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_QUOTED_IDENT_RE = re.compile(r'"(?:[^"]|"")*"')


def _strip_literals(sql: str) -> str:
    """Replace quoted string literals / identifiers with a space placeholder."""
    no_strings = _STRING_LITERAL_RE.sub(" ", sql)
    return _QUOTED_IDENT_RE.sub(" ", no_strings)


def _validate_sql(sql: str, user_id_lit: str, company_lit: str) -> Optional[str]:
    """Return an error string if the SQL is unsafe, else None.

    `user_id_lit` / `company_lit` are the SQL-escaped tenant values (single
    quotes doubled) that the agent is told to embed; the tenant check looks
    for those literals in the raw SQL.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return "Empty SQL."
    head = stripped.split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH"):
        return "Only SELECT / WITH queries are allowed."
    # Scan for write/DDL keywords only OUTSIDE quoted literals, so values that
    # happen to contain words like 'update' or 'delete' aren't rejected.
    if _FORBIDDEN_RE.search(_strip_literals(stripped)):
        return "SQL contains a forbidden keyword (write/DDL)."
    # Soft tenant check — both tenant literals must appear in the query text.
    if user_id_lit not in sql:
        return f"Query must include the tenant filter user_id = '{user_id_lit}'."
    if company_lit not in sql:
        return f"Query must include the tenant filter company_name = '{company_lit}'."
    return None


def _run_sql_blocking(sql: str, user_id: str, company: str) -> Dict[str, Any]:
    """Execute SELECT and return {columns, rows (all), truncated rows}. Blocking — call via executor."""
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return {"error": "DB connection unavailable"}
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Structural backstop: a READ ONLY transaction makes any write/DDL
            # fail at the engine even if it slips past the keyword guard, and
            # statement_timeout caps runaway queries. SET TRANSACTION READ ONLY
            # must precede the first real query in the transaction.
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '15s'")
            # Tenant isolation enforced by Postgres RLS (migration 032): assume
            # the least-privilege tally_chat_ro role and publish the tenant via
            # GUCs. RLS policies then restrict every Tally table to this
            # (user_id, company_name) regardless of what the LLM's SQL asks for.
            # set_config(..., true) and SET LOCAL are transaction-scoped, so the
            # pooled connection resets cleanly on commit/rollback. Fail-closed:
            # if these are ever unset, the policies match zero rows.
            cursor.execute("SET LOCAL ROLE tally_chat_ro")
            cursor.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            cursor.execute("SELECT set_config('app.company_name', %s, true)", (company,))
            cursor.execute(sql)
            rows = cursor.fetchall()
        conn.commit()
        full = [dict(r) for r in rows]
        for row in full:
            for k, v in list(row.items()):
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, (bytes, bytearray)):
                    row[k] = v.decode("utf-8", errors="replace")
                else:
                    try:
                        json.dumps(v)
                    except TypeError:
                        row[k] = str(v)
        return {
            "columns": list(full[0].keys()) if full else [],
            "row_count": len(full),
            "rows_full": full[:MAX_ROWS_TO_UI],
            "rows_for_llm": full[:MAX_ROWS_TO_LLM],
            "truncated": len(full) > MAX_ROWS_TO_LLM,
        }
    except Exception as exc:
        if conn:
            conn.rollback()
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conn:
            return_connection(conn)


def _list_tables_blocking() -> List[str]:
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return []
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND (table_name LIKE 'mst_%%' OR table_name LIKE 'trn_%%')
                ORDER BY table_name
                """
            )
            return [row[0] for row in cursor.fetchall()]
    except Exception as exc:
        logger.error(f"list_tables failed: {exc}")
        return []
    finally:
        if conn:
            return_connection(conn)


def _describe_table_blocking(table_name: str) -> Dict[str, Any]:
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
        return {"error": "Invalid table name."}
    if not table_name.startswith(TALLY_TABLE_PREFIXES):
        return {"error": f"Only {TALLY_TABLE_PREFIXES} tables are accessible."}
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return {"error": "DB connection unavailable"}
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            cols = [
                {"column": r[0], "type": r[1], "nullable": r[2] == "YES"}
                for r in cursor.fetchall()
            ]
        if not cols:
            return {"error": f"Table '{table_name}' not found."}
        return {"table": table_name, "columns": cols}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conn:
            return_connection(conn)


async def run_agent(
    user_id: str,
    company: str,
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    if not settings.OPENAI_API_KEY:
        yield {"type": "error", "content": "OPENAI_API_KEY not configured."}
        yield {"type": "done"}
        return

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    model = settings.OPENAI_MODEL or "gpt-4.1-mini"
    loop = asyncio.get_running_loop()

    # SQL-escape the tenant values (double any single quote) so the agent can
    # embed them as valid string literals even when a company name contains an
    # apostrophe (e.g. "M/s O'Brien" -> "M/s O''Brien"). The escaped forms feed
    # both the prompt and the tenant validation.
    safe_user_id = user_id.replace("'", "''")
    safe_company = (company or "").replace("'", "''")

    system_prompt = SYSTEM_PROMPT_TMPL.format(
        user_id=safe_user_id, company_name=safe_company, schema=SCHEMA_OVERVIEW
    )

    brief = await loop.run_in_executor(None, load_company_brief, user_id, company)
    if brief:
        system_prompt = (
            f"{system_prompt}\n\n"
            "=== COMPANY-SPECIFIC BRIEF (from last sync — prefer these real names "
            "over guesses) ===\n"
            f"{brief}\n"
        )

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    # Seed prior turns (plain user/assistant text only — tool traces aren't
    # replayed) so follow-ups like "and what about last year?" have context.
    # Keep only the most recent turns to bound prompt size.
    for turn in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    yield {"type": "status", "content": "Thinking..."}

    for iteration in range(MAX_ITERATIONS):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as exc:
            logger.error(f"LLM call failed: {exc}")
            yield {"type": "error", "content": f"LLM call failed: {exc}"}
            yield {"type": "done"}
            return

        msg = response.choices[0].message
        tool_calls = msg.tool_calls or []

        if not tool_calls:
            # Final answer. The model has already written the complete answer in
            # this turn, so emit it directly instead of making a second LLM call
            # to regenerate it — that round-trip only added latency/cost and
            # risked diverging from what was decided here. Chunk it so the UI
            # still renders progressively.
            final = msg.content or ""
            for i in range(0, len(final), 24):
                yield {"type": "answer_delta", "content": final[i:i + 24]}
            yield {"type": "done"}
            return

        # Record the assistant turn that asked for tools.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "list_tables":
                yield {"type": "tool_call", "name": name, "arguments": args}
                tables = await loop.run_in_executor(None, _list_tables_blocking)
                payload = {"tables": tables}
                yield {"type": "tool_result", "name": name, "content": payload}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(payload),
                })

            elif name == "describe_table":
                yield {"type": "tool_call", "name": name, "arguments": args}
                table_name = args.get("table_name", "")
                result = await loop.run_in_executor(
                    None, _describe_table_blocking, table_name
                )
                yield {"type": "tool_result", "name": name, "content": result}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

            elif name == "run_sql":
                sql = args.get("sql", "")
                purpose = args.get("purpose", "")
                err = _validate_sql(sql, safe_user_id, safe_company)
                if err:
                    yield {"type": "sql", "content": sql, "purpose": purpose, "rejected": err}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": err}),
                    })
                    continue

                yield {"type": "sql", "content": sql, "purpose": purpose}
                result = await loop.run_in_executor(
                    None, _run_sql_blocking, sql, user_id, company
                )

                if "error" in result:
                    yield {"type": "result", "sql": sql, "content": [], "error": result["error"]}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": result["error"]}),
                    })
                    continue

                yield {
                    "type": "result",
                    "sql": sql,
                    "content": result["rows_full"],
                    "row_count": result["row_count"],
                    "columns": result["columns"],
                    "truncated": result["truncated"],
                }
                llm_payload = {
                    "row_count": result["row_count"],
                    "columns": result["columns"],
                    "rows": result["rows_for_llm"],
                    "truncated": result["truncated"],
                }
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(llm_payload),
                })

            else:
                err_payload = {"error": f"Unknown tool: {name}"}
                yield {"type": "tool_result", "name": name, "content": err_payload}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(err_payload),
                })

    # Hit iteration ceiling — force a final answer with no more tools.
    yield {"type": "status", "content": "Wrapping up..."}
    try:
        forced = await client.chat.completions.create(
            model=model,
            messages=messages + [{
                "role": "system",
                "content": (
                    f"You have used the maximum of {MAX_ITERATIONS} tool-calling iterations. "
                    "Do NOT request any more tools. Based on what you have already learned, "
                    "write the best possible final answer now in markdown. If the data is "
                    "insufficient, say so clearly and tell the user what's missing."
                ),
            }],
            temperature=0.3,
            tool_choice="none",
            stream=True,
        )
        async for chunk in forced:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield {"type": "answer_delta", "content": delta}
    except Exception as exc:
        yield {"type": "error", "content": f"Could not produce a final answer: {exc}"}
    yield {"type": "done"}
