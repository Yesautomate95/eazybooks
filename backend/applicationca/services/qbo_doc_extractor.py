"""
PDF invoice / bill extractor for the QBO pipeline.

Renders each PDF page to PNG via PyMuPDF, base64-encodes them, and asks
OpenAI gpt-4o-mini (vision) to extract structured fields. Returns a dict
matching the shape that downstream sales/purchase pipelines expect.

Output schema (sales / purchase share the same shape):
    {
        "doc_type": "invoice" | "bill",
        "vendor_or_customer": str,
        "vendor_or_customer_email": str | None,
        "vendor_or_customer_address": str | None,
        "doc_number": str,
        "doc_date": "YYYY-MM-DD",
        "due_date": "YYYY-MM-DD" | None,
        "currency": str | None,
        "subtotal": float | None,
        "tax_total": float | None,
        "total": float | None,
        "line_items": [
            {
                "description": str,
                "qty": float,
                "rate": float,
                "amount": float,
                "tax_amount": float | None,
            },
            ...
        ],
        "raw_text_hint": str | None,    # any free-form notes/memo
    }

If extraction fails we return a sentinel dict with `error`.
"""

import base64
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF
from openai import AsyncOpenAI

from config.settings import settings

logger = logging.getLogger(__name__)


_MAX_PAGES = 6  # sane upper bound; multi-page invoices are rare for SMB
_PAGE_DPI = 150


_SYSTEM_PROMPT_INVOICE = (
    "You are an expert accounts-receivable assistant. Extract structured invoice "
    "data from the given image(s). Return STRICT JSON matching the schema "
    "provided. If a field is not visible, use null. Numbers must be numeric, not "
    "strings. Dates must be YYYY-MM-DD. Do not invent values."
)

_SYSTEM_PROMPT_BILL = (
    "You are an expert accounts-payable assistant. Extract structured bill / "
    "purchase invoice data from the given image(s). Return STRICT JSON matching "
    "the schema provided. If a field is not visible, use null. Numbers must be "
    "numeric, not strings. Dates must be YYYY-MM-DD. Do not invent values."
)


_SCHEMA_HINT = {
    "doc_type": "invoice or bill",
    "vendor_or_customer": "company / person name as it appears on the document",
    "vendor_or_customer_email": "string or null",
    "vendor_or_customer_address": "single-line address or null",
    "doc_number": "invoice or bill number",
    "doc_date": "YYYY-MM-DD",
    "due_date": "YYYY-MM-DD or null",
    "currency": "ISO 4217 (USD, INR, etc.) or null",
    "subtotal": "number or null",
    "tax_total": "number or null",
    "total": "number or null",
    "line_items": [
        {
            "description": "string",
            "qty": "number (default 1)",
            "rate": "number per unit",
            "amount": "line total (net of any line discount)",
            "discount_amount": "per-line discount in currency, or null",
            "discount_percentage": "per-line discount percent, or null",
            "tax_amount": "number or null",
        }
    ],
    "discount_total": "total document discount in currency, or null",
    "raw_text_hint": "any notes / memo text the LLM thinks belongs to the bill, or null",
}


# ---------------------------------------------------------------------------
# PDF -> PNG (base64) pages
# ---------------------------------------------------------------------------

def pdf_bytes_to_page_images(pdf_bytes: bytes, max_pages: int = _MAX_PAGES, dpi: int = _PAGE_DPI) -> List[str]:
    """Render up to `max_pages` pages of a PDF to PNG and return as base64 strings."""
    images: List[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = min(len(doc), max_pages)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for i in range(page_count):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            png = pix.tobytes("png")
            images.append(base64.b64encode(png).decode("ascii"))
    return images


# ---------------------------------------------------------------------------
# OpenAI vision call
# ---------------------------------------------------------------------------

def _coerce_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON from a model response, tolerating ```json fences."""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        # Last resort: extract first top-level {...} block
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


async def _vision_extract(
    pdf_bytes: bytes,
    *,
    doc_type: str,  # "invoice" or "bill"
    extra_context: Optional[str] = None,
) -> Dict[str, Any]:
    if not settings.OPENAI_API_KEY:
        return {"error": "OPENAI_API_KEY not configured"}

    pages = pdf_bytes_to_page_images(pdf_bytes)
    if not pages:
        return {"error": "PDF has no extractable pages"}

    system = _SYSTEM_PROMPT_INVOICE if doc_type == "invoice" else _SYSTEM_PROMPT_BILL
    schema_str = json.dumps(_SCHEMA_HINT, indent=2)

    user_content: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Extract the {doc_type} fields. Return STRICT JSON matching this "
                f"schema (use null for missing fields):\n\n{schema_str}"
                + (f"\n\nAdditional context: {extra_context}" if extra_context else "")
            ),
        }
    ]
    for b64 in pages:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        # Invoice/bill extraction needs a strong vision model. settings.OPENAI_MODEL
        # defaults to gpt-4.1-mini, which is weaker at table/column disambiguation
        # (qty vs rate vs amount). Use a dedicated, stronger default for document
        # extraction so QB invoices/bills aren't extracted on a mini model.
        completion = await client.chat.completions.create(
            model=os.getenv("DOC_EXTRACTION_MODEL", "gpt-4.1"),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI vision extraction failed")
        return {"error": f"vision extract failed: {exc}"}

    raw = completion.choices[0].message.content if completion.choices else ""
    parsed = _coerce_json(raw)
    if not parsed:
        return {"error": "could not parse JSON from model response", "raw": raw}

    parsed.setdefault("doc_type", doc_type)
    parsed.setdefault("line_items", [])
    # Defensive: QBO TxnDate/DueDate require ISO YYYY-MM-DD. The prompt asks for
    # ISO, but guard against the model returning DD/MM/YYYY (Indian invoices) —
    # otherwise QBO rejects/mis-reads the date. ISO values pass through unchanged.
    for k in ("doc_date", "due_date"):
        if parsed.get(k):
            parsed[k] = _to_iso_date(parsed[k])

    # qty/rate-swap correction + subtotal cross-validation. Reuses Tally's
    # line_item_utils.fix_line_items, adapting QB's `qty` key to its `quantity`.
    try:
        from applicationca.agents.document_specific_functions.line_item_utils import (
            fix_line_items, derive_discount,
        )
        items = [it for it in (parsed.get("line_items") or []) if isinstance(it, dict)]
        for it in items:
            it["quantity"] = it.get("qty")
        try:
            subtotal = float(parsed.get("subtotal") or 0)
        except (TypeError, ValueError):
            subtotal = 0.0
        fixed, warns = fix_line_items(items, subtotal, item_name_key="description")
        # Normalize per-line discount to a canonical (amount, percent) pair.
        disc_total = 0.0
        for it in fixed:
            d_amt, d_pct, d_warn = derive_discount(it, it.get("quantity") or 0, it.get("rate") or 0)
            it["discount_amount"] = d_amt
            it["discount_percentage"] = d_pct
            disc_total += d_amt
            if d_warn:
                warns.extend(d_warn)
            if "quantity" in it:
                it["qty"] = it.get("quantity")
                it.pop("quantity", None)
        parsed["line_items"] = fixed
        if parsed.get("discount_total") in (None, "", 0) and disc_total > 0:
            parsed["discount_total"] = round(disc_total, 2)
        if warns:
            parsed.setdefault("warnings", []).extend(warns)
    except Exception as exc:  # noqa: BLE001
        logger.warning("line-item fix-up failed: %s", exc)

    return parsed


def _to_iso_date(val: Any) -> Any:
    """Coerce a DD/MM/YYYY (or DD-MM-YYYY) string to ISO YYYY-MM-DD. ISO and
    non-matching values are returned unchanged. Indian-invoice convention =>
    first group is the day."""
    if not val or not isinstance(val, str):
        return val
    s = val.strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return s


async def extract_invoice(pdf_bytes: bytes, extra_context: Optional[str] = None) -> Dict[str, Any]:
    return await _vision_extract(pdf_bytes, doc_type="invoice", extra_context=extra_context)


async def extract_bill(pdf_bytes: bytes, extra_context: Optional[str] = None) -> Dict[str, Any]:
    return await _vision_extract(pdf_bytes, doc_type="bill", extra_context=extra_context)
