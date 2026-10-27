"""Extract bank transactions from structured tabular data (Excel/CSV) using LLM column mapping."""

import json
import logging
from typing import Any, Dict, List

import pandas as pd
from openai import AsyncOpenAI

from utils.tabular_extractor import dataframe_preview

logger = logging.getLogger(__name__)

MAX_ROWS_PER_LLM_CALL = 200


async def extract_bank_transactions_from_dataframe(
    df: pd.DataFrame,
    openai_api_key: str,
    openai_model: str = "gpt-4.1",
) -> List[Dict[str, Any]]:
    """
    Parse a DataFrame of bank statement rows into transaction dicts.

    Returns list of dicts matching extract_bank_transactions() output:
    [{"date", "description", "type", "amount"}, ...]
    """
    if df.empty:
        return []

    client = AsyncOpenAI(api_key=openai_api_key)
    preview = dataframe_preview(df, max_rows=8)

    column_prompt = f"""You are a bank statement parser. Analyze this spreadsheet and identify which columns map to transaction fields.

SPREADSHEET PREVIEW:
{preview}

TOTAL ROWS: {len(df)}

Identify the column names (exactly as they appear) that map to:
1. "date_col": transaction date column
2. "description_col": narration/description/particulars column
3. "debit_col": debit/withdrawal amount column (money going out) — may be null if combined
4. "credit_col": credit/deposit amount column (money coming in) — may be null if combined
5. "amount_col": single combined amount column (if no separate debit/credit columns) — may be null
6. "type_col": transaction type column (if it explicitly says debit/credit) — may be null
7. "header_rows_to_skip": number of rows at the top that are headers/metadata (not transactions), default 0

If there are separate Debit and Credit columns, set amount_col to null.
If there is only one amount column, set debit_col and credit_col to null.

Return ONLY a JSON object:
{{"date_col": "...", "description_col": "...", "debit_col": "..." or null, "credit_col": "..." or null, "amount_col": "..." or null, "type_col": "..." or null, "header_rows_to_skip": 0}}"""

    try:
        mapping_resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a bank statement parser. Return only valid JSON."},
                {"role": "user", "content": column_prompt},
            ],
            temperature=0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )

        mapping = json.loads(mapping_resp.choices[0].message.content.strip())
        logger.info(f"Column mapping: {mapping}")

        skip = int(mapping.get("header_rows_to_skip", 0) or 0)
        if skip > 0:
            df = df.iloc[skip:].reset_index(drop=True)

        date_col = mapping.get("date_col")
        desc_col = mapping.get("description_col")
        debit_col = mapping.get("debit_col")
        credit_col = mapping.get("credit_col")
        amount_col = mapping.get("amount_col")
        type_col = mapping.get("type_col")

        transactions = []
        for _, row in df.iterrows():
            date_val = str(row.get(date_col, "")).strip() if date_col else ""
            desc_val = str(row.get(desc_col, "")).strip() if desc_col else ""

            if not date_val or date_val in ("", "nan", "NaT", "None"):
                continue

            txn_type = "payment"
            amount = 0.0

            if debit_col and credit_col:
                debit = _parse_amount(row.get(debit_col))
                credit = _parse_amount(row.get(credit_col))
                if credit > 0:
                    txn_type = "receipt"
                    amount = credit
                elif debit > 0:
                    txn_type = "payment"
                    amount = debit
                else:
                    continue
            elif amount_col:
                raw_amount = _parse_amount(row.get(amount_col))
                if type_col:
                    type_val = str(row.get(type_col, "")).strip().lower()
                    if type_val in ("cr", "credit", "receipt", "c", "by transfer", "deposit"):
                        txn_type = "receipt"
                    else:
                        txn_type = "payment"
                elif raw_amount < 0:
                    txn_type = "payment"
                    raw_amount = abs(raw_amount)
                else:
                    txn_type = "receipt"
                amount = abs(raw_amount)
            else:
                continue

            if amount == 0:
                continue

            transactions.append({
                "date": _normalize_date(date_val),
                "description": desc_val,
                "type": txn_type,
                "amount": round(amount, 2),
            })

        logger.info(f"Extracted {len(transactions)} bank transactions from spreadsheet")
        return transactions

    except Exception as e:
        logger.error(f"Excel bank extraction failed: {e}")
        return await _fallback_text_extraction(df, client, openai_model)


async def _fallback_text_extraction(
    df: pd.DataFrame,
    client: AsyncOpenAI,
    model: str,
) -> List[Dict[str, Any]]:
    """If column mapping fails, serialize to text and use the standard LLM extraction."""
    logger.info("Falling back to text-based extraction for bank spreadsheet")
    text = df.to_string(index=False)
    if len(text) > 30000:
        text = text[:30000]

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a bank statement parser. Extract all transactions accurately. Return only valid JSON."},
                {"role": "user", "content": f"""Extract ALL bank transactions from this spreadsheet data.

DATA:
{text}

For each transaction extract:
- date: DD/MM/YYYY
- description: full narration
- type: "receipt" (money in) or "payment" (money out)
- amount: the transaction amount (positive number)

Return: {{"transactions": [{{"date": "...", "description": "...", "type": "...", "amount": 0}}]}}"""},
            ],
            temperature=0.1,
            max_tokens=16384,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content.strip())
        txs = parsed.get("transactions", [])
        return txs if isinstance(txs, list) else []
    except Exception as e:
        logger.error(f"Fallback text extraction also failed: {e}")
        return []


def _parse_amount(val) -> float:
    if val is None or str(val).strip() in ("", "nan", "None", "NaT", "-"):
        return 0.0
    s = str(val).strip().replace(",", "").replace("(", "-").replace(")", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _normalize_date(date_str: str) -> str:
    """Best-effort normalization to DD/MM/YYYY."""
    import re
    date_str = date_str.strip()

    m = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", date_str)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{d.zfill(2)}/{mo.zfill(2)}/{y}"

    m = re.match(r"(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", date_str)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{d.zfill(2)}/{mo.zfill(2)}/{y}"

    return date_str
