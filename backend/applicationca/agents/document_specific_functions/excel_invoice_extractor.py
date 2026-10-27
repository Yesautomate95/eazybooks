"""Extract invoices from structured tabular data (Excel/CSV) using LLM column mapping."""

import json
import logging
from typing import Any, Dict, List, Optional

import pandas as pd
from openai import AsyncOpenAI

from utils.tabular_extractor import dataframe_preview
from applicationca.agents.document_specific_functions.line_item_utils import derive_discount

logger = logging.getLogger(__name__)


async def extract_invoices_from_dataframe(
    df: pd.DataFrame,
    voucher_type: str,
    openai_api_key: str,
    openai_model: str = "gpt-4o-mini",
    inventory_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Parse a DataFrame of invoice rows into structured invoice data.

    Returns a dict matching extract_sales_invoice_structured() output:
    {header, items, taxes, summary}.
    """
    if df.empty:
        return _empty_invoice(voucher_type)

    client = AsyncOpenAI(api_key=openai_api_key)
    preview = dataframe_preview(df, max_rows=8)

    party_label = "buyer/customer" if voucher_type == "sales" else "vendor/supplier"

    mapping_prompt = f"""You are an expert Indian GST accountant. Analyze this spreadsheet data and extract invoice information.

SPREADSHEET DATA:
{preview}

TOTAL ROWS: {len(df)}

This is a {voucher_type.upper()} invoice spreadsheet. The party is the {party_label}.

Analyze the columns and ALL rows, then extract the complete invoice data.

Return a JSON object with this exact structure:
{{
  "header": {{
    "invoice_type": "{voucher_type.title()}",
    "voucher_date": "DD/MM/YYYY or empty",
    "invoice_number": "invoice/bill number or empty",
    "party_name": "the {party_label} name",
    "party_gstin": "15-char GSTIN or empty",
    "place_of_supply": "full state name or empty"
  }},
  "items": [
    {{
      "item_name": "product/service name",
      "quantity": 0,
      "rate": 0,
      "amount": 0,
      "unit": "Nos",
      "discount_amount": 0,
      "discount_percentage": 0,
      "godown": "Main Location",
      "batch": ""
    }}
  ],
  "taxes": [
    {{
      "ledger_name": "CGST/SGST/IGST",
      "description": "",
      "amount": 0,
      "percentage": 0
    }}
  ],
  "summary": {{
    "subtotal": 0,
    "tax_amount": 0,
    "total_amount": 0,
    "narration": ""
  }}
}}

RULES:
- Extract ALL line items from ALL rows, not just the sample
- If the spreadsheet has separate CGST, SGST, IGST columns or rows, extract each as a separate tax entry
- Dates must be DD/MM/YYYY format
- Use 0 for missing numbers, "" for missing strings
- If multiple invoices exist (different invoice numbers or parties), extract ONLY the first invoice
- Calculate subtotal = sum of item amounts, tax_amount = sum of tax amounts, total_amount = subtotal + tax_amount"""

    if len(df) > 8:
        full_data = df.to_csv(index=False)
        if len(full_data) > 30000:
            full_data = full_data[:30000] + "\n... (truncated)"
        mapping_prompt += f"\n\nFULL DATA (CSV format):\n{full_data}"

    try:
        response = await client.chat.completions.create(
            model=openai_model,
            messages=[
                {"role": "system", "content": "You are an expert accountant. Extract structured invoice data from spreadsheets. Return only valid JSON."},
                {"role": "user", "content": mapping_prompt},
            ],
            temperature=0.1,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content.strip()
        result = json.loads(content)

        header = result.get("header", {})
        items = result.get("items", [])
        taxes = result.get("taxes", [])
        summary = result.get("summary", {})

        clean_items = []
        for it in items:
            qty = _safe_float(it.get("quantity", 0))
            rate = _safe_float(it.get("rate", 0))
            discount_amount, discount_percentage, _disc_warnings = derive_discount(it, qty, rate)
            extracted_amount = _safe_float(it.get("amount", 0))
            # Spreadsheet "amount" column is usually GROSS — re-derive NET if
            # discount applies. If no discount, trust whatever the sheet had.
            if discount_amount > 0:
                base = qty * rate if (qty * rate) > 0 else extracted_amount
                net_amount = round(max(base - discount_amount, 0.0), 2)
            else:
                net_amount = extracted_amount
            clean_items.append({
                "item_name": str(it.get("item_name", "")),
                "quantity": qty,
                "rate": rate,
                "amount": net_amount,
                "unit": str(it.get("unit", "Nos")),
                "discount_amount": discount_amount,
                "discount_percentage": discount_percentage,
                "godown": str(it.get("godown", "Main Location")),
                "batch": str(it.get("batch", "")),
            })

        clean_taxes = []
        for t in taxes:
            clean_taxes.append({
                "ledger_name": str(t.get("ledger_name", "")),
                "description": str(t.get("description", "")),
                "amount": _safe_float(t.get("amount", 0)),
                "percentage": _safe_float(t.get("percentage", 0)),
            })

        subtotal = _safe_float(summary.get("subtotal", 0))
        tax_amount = _safe_float(summary.get("tax_amount", 0))
        total_amount = _safe_float(summary.get("total_amount", 0))
        if not total_amount and (subtotal or tax_amount):
            total_amount = subtotal + tax_amount

        return {
            "header": {
                "invoice_type": header.get("invoice_type", voucher_type.title()),
                "voucher_date": str(header.get("voucher_date", "")),
                "invoice_number": str(header.get("invoice_number", "")),
                "party_name": str(header.get("party_name", "")),
                "party_gstin": str(header.get("party_gstin", "")),
                "place_of_supply": str(header.get("place_of_supply", "")),
                "confidence": {"source": "spreadsheet"},
            },
            "items": clean_items,
            "taxes": clean_taxes,
            "summary": {
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "total_amount": total_amount,
                "narration": str(summary.get("narration", "")),
            },
        }

    except Exception as e:
        logger.error(f"Excel invoice extraction failed: {e}")
        return _empty_invoice(voucher_type)


def _safe_float(val) -> float:
    try:
        if val is None or val == "":
            return 0.0
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _empty_invoice(voucher_type: str) -> Dict[str, Any]:
    return {
        "header": {
            "invoice_type": voucher_type.title(),
            "voucher_date": "",
            "invoice_number": "",
            "party_name": "",
            "party_gstin": "",
            "place_of_supply": "",
            "confidence": {},
        },
        "items": [],
        "taxes": [],
        "summary": {"subtotal": 0, "tax_amount": 0, "total_amount": 0, "narration": ""},
    }
