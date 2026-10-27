import logging
import json
import re
import os
import base64
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from google import genai
from google.genai import types as genai_types
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from applicationca.agents.document_specific_functions.line_item_utils import fix_line_items, derive_discount

logger = logging.getLogger(__name__)

async def identify_vendor_from_document(extracted_text: str, gemini_model: str, google_api_key: str) -> str:
    """Identify the vendor account name from the purchase document text."""
    try:
        client = genai.Client(api_key=google_api_key)

        prompt = f"""Analyze this purchase document and identify the vendor name (the supplier who sold to us).
Return only the vendor name, nothing else.

Document content:
{extracted_text}"""

        response = client.models.generate_content(
            model=gemini_model,
            contents=prompt
        )
        vendor_name = response.text.strip()
        return vendor_name
    except Exception as e:
        logger.error(f"Error identifying vendor: {str(e)}")
        return "Cash"

async def extract_purchase_transactions(
    extracted_text: str,
    file_path: str,
    gemini_model: str,
    google_api_key: str,
    inventory_names: Optional[List[str]] = None,
    inventory_names_with_units: Optional[List[Dict[str, str]]] = None,
    openai_api_key: Optional[str] = None,
    openai_model: str = "gpt-4o",
    anthropic_api_key: Optional[str] = None,
    extraction_provider: str = "openai",
) -> List[Dict[str, Any]]:
    """Extract transactions from purchase documents using the configured provider."""
    start_time = __import__('time').time()
    logger.info(f"Starting purchase transaction extraction using {extraction_provider}")
    print(f"\nExtracting purchase transactions using {extraction_provider}...")

    if extraction_provider == "claude":
        anthropic_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        claude_model = os.getenv("CLAUDE_EXTRACTION_MODEL", "claude-opus-4-7")
        if anthropic_key:
            result = await _extract_purchase_with_claude(
                file_path=file_path,
                extracted_text=extracted_text,
                anthropic_api_key=anthropic_key,
                claude_model=claude_model,
            )
            if result:
                elapsed_time = __import__('time').time() - start_time
                logger.info(f"Purchase extraction (claude) completed in {elapsed_time:.2f} seconds")
                return result
        logger.error("Claude extraction failed or no ANTHROPIC_API_KEY available")
        return []

    # Default: OpenAI
    openai_key = openai_api_key or os.getenv("OPENAI_API_KEY")
    if openai_key:
        result = await _extract_purchase_with_openai(
            file_path=file_path,
            extracted_text=extracted_text,
            openai_api_key=openai_key,
            openai_model=openai_model
        )
        if result:
            elapsed_time = __import__('time').time() - start_time
            logger.info(f"Purchase extraction completed in {elapsed_time:.2f} seconds")
            return result

    logger.error("OpenAI extraction failed or no API key available")
    return []


async def _recover_vendor_name(
    client,
    extracted_text: str,
    content_parts: list,
    image_attached: bool,
) -> Optional[str]:
    """Focused LLM call to recover vendor/supplier name when main extraction missed it."""
    try:
        parts = []
        if image_attached and content_parts:
            for p in content_parts:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    parts.append(p)
                    break

        prompt = (
            "Look at this purchase invoice carefully. I need ONLY the vendor/supplier "
            "company name — the company that ISSUED this invoice (usually at the top with "
            "their logo, address, and GSTIN).\n\n"
            "Do NOT return the buyer's name (the company in 'Bill To' section).\n\n"
            "Return ONLY the vendor company name, nothing else. "
            "If you cannot find it, return exactly: UNKNOWN"
        )
        if extracted_text:
            prompt += f"\n\nDocument text:\n{extracted_text[:4000]}"

        parts.append({"type": "text", "text": prompt})

        model = "gpt-4.1" if image_attached else "gpt-4.1-mini"

        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Extract the vendor name from purchase invoices. Return only the name."},
                {"role": "user", "content": parts},
            ],
            temperature=0,
            max_tokens=100,
        )
        name = resp.choices[0].message.content.strip()
        if name and name.upper() not in ("UNKNOWN", "N/A", "NONE", "NOT FOUND", ""):
            return name
        return None
    except Exception as e:
        logger.warning("Vendor name recovery failed: %s", e)
        return None


async def _extract_purchase_with_openai(
    file_path: str,
    extracted_text: str,
    openai_api_key: str,
    openai_model: str = "gpt-4o"
) -> List[Dict[str, Any]]:
    """Extract purchase invoice data using OpenAI GPT-4o vision."""
    logger.info("Extracting purchase invoice via OpenAI GPT-4o")

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=openai_api_key)

        # Build message content
        content_parts = []

        # Try to attach the file as an image
        file_ext = os.path.splitext(file_path)[1].lower()
        image_attached = False

        if file_ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
            with open(file_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode('utf-8')
            mime_map = {'.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}
            mime = mime_map.get(file_ext, 'image/jpeg')
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{img_b64}", "detail": "high"}
            })
            image_attached = True
        elif file_ext == '.pdf':
            # Try converting first page of PDF to image for vision
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(file_path)
                page = doc[0]
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                img_b64 = base64.b64encode(img_bytes).decode('utf-8')
                doc.close()
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "high"}
                })
                image_attached = True
                logger.info("Converted PDF first page to image for OpenAI vision")
            except ImportError:
                logger.warning("PyMuPDF not installed, falling back to text-only extraction")
            except Exception as e:
                logger.warning(f"PDF to image conversion failed: {e}")

        # Build the improved prompt with semantic understanding
        prompt = """You are an expert accounting assistant specialized in Indian GST purchase invoices and Tally ERP voucher creation.

Your task is NOT just to extract text, but to UNDERSTAND the document like an accountant.

════════════════════════════════════════════════════════════════════════════════
STEP 1: UNDERSTAND THE DOCUMENT SEMANTICS
════════════════════════════════════════════════════════════════════════════════
This is a PURCHASE invoice. Identify:
- **Vendor/Supplier**: The company that ISSUED this invoice (their name, GSTIN at top)
- **Your Company**: The company being billed (in "Bill To" section)
- The PARTY for Tally = Vendor (the one who issued the bill)

**CRITICAL**: Extract the VENDOR's details as the party, NOT your company's details.

════════════════════════════════════════════════════════════════════════════════
STEP 2: EXTRACT VENDOR (PARTY) DETAILS
════════════════════════════════════════════════════════════════════════════════
Extract from the VENDOR/SUPPLIER section (usually at top with logo):
- **vendor_name**: Clean name (remove M/s unless part of legal name)
- **vendor_gstin**: 15-character GSTIN of the VENDOR
- **vendor_address**: Full address
- **place_of_supply**: Full STATE NAME (e.g., "Maharashtra" not "MH")
  State codes: 27=Maharashtra, 29=Karnataka, 07=Delhi, 09=UP, 33=Tamil Nadu, 24=Gujarat

════════════════════════════════════════════════════════════════════════════════
STEP 3: EXTRACT LINE ITEMS WITH VALIDATION
════════════════════════════════════════════════════════════════════════════════
For EACH line item, identify THREE DIFFERENT values:
| QUANTITY | Number of units (e.g., 10) |
| RATE | Price per ONE unit (e.g., 500) |
| AMOUNT | Total = Qty × Rate (e.g., 5000) |

**FIRST: IDENTIFY COLUMN HEADERS**
Before reading any row values, locate the TABLE HEADER ROW and identify which column is which:
- QUANTITY column headers: "Qty", "Quantity", "Nos.", "Units", "Pcs", "Qty (Nos)", "No. of Units", "Qty/Unit"
- RATE column headers: "Rate", "Price", "MRP", "Unit Price", "Rate/Unit", "Basic Price", "Basic Rate", "Price/Unit"
- AMOUNT column headers: "Amount", "Value", "Total", "Net Amount", "Taxable Value", "Line Total", "Gross Amount"

Read values from those SPECIFIC COLUMN POSITIONS only — do not read from adjacent columns like Discount, Tax, or Balance.

**LOGICAL RULES to tell Quantity from Rate (IMPORTANT — do NOT swap them):**
- Quantity is usually a SMALL WHOLE number (1, 2, 5, 10, 50, 100)
- Rate is usually a LARGER number, often with DECIMALS (500.00, 1250.75, 10820.00)
- Edge case — bulk items: qty=500, rate=8.50, amount=4250 → qty IS larger here, this is CORRECT
- For services (travel, consulting, rent, insurance), Quantity is typically 1 and Rate is the full price
- Quantity can never be negative. Rate can never be negative.
- If Qty=10820 and Rate=1 → that is WRONG (swapped). Should be Qty=1, Rate=10820.

**VALIDATION**: Quantity × Rate ≈ Amount. If not, SWAP Qty and Rate and check again!

**CROSS-VALIDATE TOTALS**: After extracting all items, verify:
  sum(all item amounts) ≈ document subtotal (before tax). If they differ by more than 5%,
  you are likely reading the wrong column for Amount — re-check and correct.

Extract:
- item_name: Exact name as shown
- quantity: NUMBER OF UNITS (not amount!)
- rate: PRICE PER UNIT (not total!)
- amount: Total for this item
- hsn_code: HSN/SAC code
- gst_rate: GST percentage (18, 12, 5, etc.)
- discount_amount: Discount in RUPEES (₹) if the invoice shows the discount
  as an absolute amount (e.g. "Less Disc 100"). Otherwise 0.
- discount_percentage: Discount as PERCENT (plain number, 5.5 not "5.5%") if
  the invoice shows it as a percentage. Otherwise 0.
  DO NOT convert between the two — populate whichever the invoice shows. The
  Python code derives the missing one.

════════════════════════════════════════════════════════════════════════════════
STEP 4: EXTRACT TAX AND TOTALS
════════════════════════════════════════════════════════════════════════════════
From tax breakup section, extract EVERY tax/charge line shown on the invoice:
- Extract EACH tax as a SEPARATE entry exactly as written (CGST, SGST, IGST, Cess, etc.)
- Include entries even if amount is 0 (if shown on the invoice)
- DO NOT merge multiple tax lines into one
- Also extract: Subtotal (before tax), Other charges (Freight, Packing, etc.), Grand Total

════════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (STRICT JSON)
════════════════════════════════════════════════════════════════════════════════
{
    "transaction_type": "purchase",
    "vendor": {
        "name": "Vendor/Supplier company name",
        "normalized_name": "lowercase name without special chars",
        "gstin": "15-character GSTIN of vendor",
        "address": "Vendor's address",
        "state": "Full state name"
    },
    "invoice": {
        "number": "Invoice/Bill number",
        "date": "DD/MM/YYYY",
        "place_of_supply": "Full state name"
    },
    "items": [
        {
            "name": "item name",
            "quantity": 10,
            "rate": 500,
            "amount": 5000,
            "hsn_code": "HSN code",
            "gst_rate": 18,
            "discount_amount": 0,
            "discount_percentage": 0
        }
    ],
    "taxes": {
        "cgst": {"amount": 0, "percentage": 0},
        "sgst": {"amount": 0, "percentage": 0},
        "igst": {"amount": 0, "percentage": 0}
    },
    "totals": {
        "subtotal": 5000,
        "tax_amount": 900,
        "other_charges": 0,
        "grand_total": 5900
    },
    "tally_mapping": {
        "voucher_type": "Purchase",
        "party_ledger": "Vendor Name",
        "debit_ledger": "Purchase",
        "credit_ledger": "Vendor Name"
    },
    "confidence": {
        "vendor_name": 0.95,
        "invoice_number": 0.90,
        "items": 0.85,
        "totals": 0.90
    },
    "warnings": []
}

════════════════════════════════════════════════════════════════════════════════
CRITICAL RULES
════════════════════════════════════════════════════════════════════════════════
1. NO null values — use "" for text, 0 for numbers
2. QUANTITY ≠ RATE ≠ AMOUNT — validate Qty × Rate ≈ Amount
3. Party = VENDOR (the one who issued the invoice)
4. GSTIN is the VENDOR's GSTIN
5. Add warnings for calculation mismatches
6. Use DD/MM/YYYY for dates
7. Full state names, not abbreviations
8. Return ONLY valid JSON, no markdown"""

        if extracted_text:
            prompt += f"\n\nDocument text content:\n{extracted_text[:8000]}"

        content_parts.append({"type": "text", "text": prompt})

        model_to_use = "gpt-4o" if image_attached else openai_model

        response = await client.chat.completions.create(
            model=model_to_use,
            messages=[
                {"role": "system", "content": "You are an expert Indian GST purchase invoice data extractor for Tally ERP. Extract vendor details, line items with quantity/rate/amount validation, and tax breakup. Return only valid JSON."},
                {"role": "user", "content": content_parts}
            ],
            temperature=0.1,
            max_tokens=4096,
        )

        content = response.choices[0].message.content.strip()

        # Parse JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        extracted_data = json.loads(content)

        # Handle new format (vendor, invoice, items) or old format (transactions)
        if "vendor" in extracted_data and "items" in extracted_data:
            # New format - convert to transactions for backward compatibility
            vendor = extracted_data.get("vendor", {})
            invoice = extracted_data.get("invoice", {})

            logger.info(
                "Purchase extraction — vendor_name='%s', vendor_gstin='%s', "
                "invoice_number='%s', invoice_date='%s'",
                vendor.get("name"), vendor.get("gstin"),
                invoice.get("number"), invoice.get("date"),
            )
            # Recovery: if vendor name is empty, try a focused extraction
            vendor_name_raw = (vendor.get("name") or "").strip()
            if not vendor_name_raw or vendor_name_raw.lower() in ("cash", "unknown", ""):
                logger.warning("Vendor name missing or default ('%s') — attempting focused recovery", vendor_name_raw)
                recovered = await _recover_vendor_name(client, extracted_text, content_parts, image_attached)
                if recovered:
                    vendor["name"] = recovered
                    logger.info("Recovered vendor name: '%s'", recovered)

            items = extracted_data.get("items", [])
            taxes = extracted_data.get("taxes", {})
            totals = extracted_data.get("totals", {})
            tally_mapping = extracted_data.get("tally_mapping", {})
            confidence = extracted_data.get("confidence", {})
            warnings = extracted_data.get("warnings", [])

            # Fix qty/rate swaps and cross-validate against subtotal
            _subtotal = totals.get("subtotal", 0)
            items, _fix_warnings = fix_line_items(items, subtotal=_subtotal, item_name_key="name")
            warnings.extend(_fix_warnings)

            # Calculate tax amount
            cgst = taxes.get("cgst", {}).get("amount", 0)
            sgst = taxes.get("sgst", {}).get("amount", 0)
            igst = taxes.get("igst", {}).get("amount", 0)
            total_tax = cgst + sgst + igst

            # Build transactions array from items for backward compatibility
            transactions = []
            for idx, item in enumerate(items):
                quantity = float(item.get("quantity", 1))
                rate = float(item.get("rate", 0))
                amount = float(item.get("amount", quantity * rate))

                # Validate calculation
                calculated = quantity * rate
                if amount > 0 and abs(calculated - amount) / max(amount, 1) > 0.05:
                    if calculated > 0:
                        warnings.append(f"Item {idx+1}: Amount corrected from {amount} to {calculated}")
                        amount = calculated

                discount_amount, discount_percentage, _disc_warnings = derive_discount(
                    item, quantity, rate
                )
                for w in _disc_warnings:
                    warnings.append(f"Item {idx+1}: {w}")

                net_amount = round(max(amount - discount_amount, 0.0), 2) if discount_amount > 0 else amount
                tx = {
                    "date": invoice.get("date", ""),
                    "description": item.get("name", ""),
                    "reference_number": invoice.get("number", ""),
                    "place_of_supply": invoice.get("place_of_supply", vendor.get("state", "")),
                    "amount": net_amount,
                    "quantity": quantity,
                    "rate": rate,
                    "discount_amount": discount_amount,
                    "discount_percentage": discount_percentage,
                    "hsn_code": item.get("hsn_code", ""),
                    "gst_percentage": float(item.get("gst_rate", 0)),
                    "gst_amount": net_amount * float(item.get("gst_rate", 0)) / 100,
                    "total_amount": net_amount + (net_amount * float(item.get("gst_rate", 0)) / 100),
                    "type": "purchase",
                    "credit_ledger": vendor.get("name", "Cash"),
                    "debit_ledger": "Purchase",
                    # New fields
                    "vendor_gstin": vendor.get("gstin", ""),
                    "vendor_address": vendor.get("address", ""),
                    "normalized_vendor_name": vendor.get("normalized_name", ""),
                }
                transactions.append(tx)

            # Add metadata to first transaction for frontend
            if transactions:
                transactions[0]["_metadata"] = {
                    "totals": totals,
                    "taxes": taxes,
                    "tally_mapping": tally_mapping,
                    "confidence": confidence,
                    "warnings": warnings,
                    "vendor": vendor,
                    "invoice": invoice,
                }

            logger.info(f"Converted new format to {len(transactions)} transactions")
        else:
            # Old format - process as before
            transactions = extracted_data.get("transactions", [])

        # Process transactions (common processing for both formats)
        for tx in transactions:
            if not tx.get('date'):
                tx['date'] = '01/01/2025'
            if not tx.get('description'):
                tx['description'] = 'Purchase item'
            if not tx.get('reference_number'):
                tx['reference_number'] = ''
            if not tx.get('place_of_supply'):
                tx['place_of_supply'] = ''
            if not tx.get('amount'):
                tx['amount'] = 0
            if not tx.get('gst_percentage'):
                tx['gst_percentage'] = 0
            if not tx.get('gst_amount'):
                tx['gst_amount'] = 0
            if not tx.get('total_amount'):
                tx['total_amount'] = tx.get('amount', 0) + tx.get('gst_amount', 0)
            tx['type'] = 'purchase'
            if not tx.get('credit_ledger'):
                tx['credit_ledger'] = 'Cash'
            if not tx.get('debit_ledger'):
                tx['debit_ledger'] = 'Purchase'

        logger.info(f"OpenAI extraction successful: {len(transactions)} purchase transactions")
        return transactions

    except Exception as e:
        logger.error(f"OpenAI purchase extraction failed: {str(e)}")
        return []


async def _extract_purchase_with_claude(
    file_path: str,
    extracted_text: str,
    anthropic_api_key: str,
    claude_model: str = "claude-opus-4-7",
) -> List[Dict[str, Any]]:
    """Extract purchase invoice data using Claude vision."""
    logger.info(f"Extracting purchase invoice via Claude ({claude_model})")
    try:
        from applicationca.agents.document_specific_functions.claude_extractor import call_claude_async

        prompt = """You are an expert accounting assistant specialized in Indian GST purchase invoices and Tally ERP voucher creation.

Your task is NOT just to extract text, but to UNDERSTAND the document like an accountant.

════════════════════════════════════════════════════════════════════════════════
STEP 1: UNDERSTAND THE DOCUMENT SEMANTICS
════════════════════════════════════════════════════════════════════════════════
This is a PURCHASE invoice. Identify:
- **Vendor/Supplier**: The company that ISSUED this invoice (their name, GSTIN at top)
- **Your Company**: The company being billed (in "Bill To" section)
- The PARTY for Tally = Vendor (the one who issued the bill)

**CRITICAL**: Extract the VENDOR's details as the party, NOT your company's details.

════════════════════════════════════════════════════════════════════════════════
STEP 2: EXTRACT VENDOR (PARTY) DETAILS
════════════════════════════════════════════════════════════════════════════════
Extract from the VENDOR/SUPPLIER section (usually at top with logo):
- **vendor_name**: Clean name (remove M/s unless part of legal name)
- **vendor_gstin**: 15-character GSTIN of the VENDOR
- **vendor_address**: Full address
- **place_of_supply**: Full STATE NAME (e.g., "Maharashtra" not "MH")
  State codes: 27=Maharashtra, 29=Karnataka, 07=Delhi, 09=UP, 33=Tamil Nadu, 24=Gujarat

════════════════════════════════════════════════════════════════════════════════
STEP 3: EXTRACT LINE ITEMS WITH VALIDATION
════════════════════════════════════════════════════════════════════════════════
For EACH line item, identify THREE DIFFERENT values:
| QUANTITY | Number of units (e.g., 10) |
| RATE | Price per ONE unit (e.g., 500) |
| AMOUNT | Total = Qty × Rate (e.g., 5000) |

**LOGICAL RULES to tell Quantity from Rate (IMPORTANT — do NOT swap them):**
- Quantity is usually a SMALL WHOLE number (1, 2, 5, 10, 50, 100)
- Rate is usually a LARGER number, often with DECIMALS (500.00, 1250.75, 10820.00)
- If you see two numbers and one is much larger → the larger one is the Rate, smaller is Quantity
- For services (travel, consulting, rent, insurance), Quantity is typically 1
- Quantity can never be negative. Rate can never be negative.
- If Qty=10820 and Rate=1 → that is WRONG (swapped). Should be Qty=1, Rate=10820.

**VALIDATION**: Quantity × Rate ≈ Amount. If not, SWAP Qty and Rate and check again!

════════════════════════════════════════════════════════════════════════════════
STEP 4: EXTRACT TAX AND TOTALS
════════════════════════════════════════════════════════════════════════════════
From tax breakup section, extract EVERY tax/charge line shown on the invoice:
- Extract EACH tax as a SEPARATE entry exactly as written (CGST, SGST, IGST, Cess, etc.)
- Include entries even if amount is 0 (if shown on the invoice)
- DO NOT merge multiple tax lines into one
- Also extract: Subtotal (before tax), Other charges (Freight, Packing, etc.), Grand Total

════════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (STRICT JSON)
════════════════════════════════════════════════════════════════════════════════
{
    "transaction_type": "purchase",
    "vendor": {
        "name": "Vendor/Supplier company name",
        "normalized_name": "lowercase name without special chars",
        "gstin": "15-character GSTIN of vendor",
        "address": "Vendor's address",
        "state": "Full state name"
    },
    "invoice": {
        "number": "Invoice/Bill number",
        "date": "DD/MM/YYYY",
        "place_of_supply": "Full state name"
    },
    "items": [
        {
            "name": "item name",
            "quantity": 10,
            "rate": 500,
            "amount": 5000,
            "hsn_code": "HSN code",
            "gst_rate": 18,
            "discount_amount": 0,
            "discount_percentage": 0
        }
    ],
    "taxes": {
        "cgst": {"amount": 0, "percentage": 0},
        "sgst": {"amount": 0, "percentage": 0},
        "igst": {"amount": 0, "percentage": 0}
    },
    "totals": {
        "subtotal": 5000,
        "tax_amount": 900,
        "other_charges": 0,
        "grand_total": 5900
    },
    "tally_mapping": {
        "voucher_type": "Purchase",
        "party_ledger": "Vendor Name",
        "debit_ledger": "Purchase",
        "credit_ledger": "Vendor Name"
    },
    "confidence": {
        "vendor_name": 0.95,
        "invoice_number": 0.90,
        "items": 0.85,
        "totals": 0.90
    },
    "warnings": []
}

════════════════════════════════════════════════════════════════════════════════
CRITICAL RULES
════════════════════════════════════════════════════════════════════════════════
1. NO null values — use "" for text, 0 for numbers
2. QUANTITY ≠ RATE ≠ AMOUNT — validate Qty × Rate ≈ Amount
3. Party = VENDOR (the one who issued the invoice)
4. GSTIN is the VENDOR's GSTIN
5. Add warnings for calculation mismatches
6. Use DD/MM/YYYY for dates
7. Full state names, not abbreviations
8. Return ONLY valid JSON, no markdown"""

        if extracted_text:
            prompt += f"\n\nDocument text content:\n{extracted_text[:8000]}"

        raw = await call_claude_async(
            file_path=file_path,
            prompt=prompt,
            api_key=anthropic_api_key,
            model=claude_model,
            system_prompt="You are an expert Indian GST purchase invoice data extractor for Tally ERP. Extract vendor details, line items with quantity/rate/amount validation, and tax breakup. Return only valid JSON.",
            max_tokens=4096,
        )

        content = raw.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        extracted_data = json.loads(content)

        # Use the same post-processing logic as _extract_purchase_with_openai
        vendor = extracted_data.get("vendor", {})
        invoice = extracted_data.get("invoice", {})

        logger.info(
            "Claude purchase extraction — vendor_name='%s', vendor_gstin='%s', "
            "invoice_number='%s', invoice_date='%s'",
            vendor.get("name"), vendor.get("gstin"),
            invoice.get("number"), invoice.get("date"),
        )

        items = extracted_data.get("items", [])
        taxes = extracted_data.get("taxes", {})
        totals = extracted_data.get("totals", {})
        tally_mapping = extracted_data.get("tally_mapping", {})
        confidence = extracted_data.get("confidence", {})
        warnings = extracted_data.get("warnings", [])

        # Fix qty/rate swaps and cross-validate against subtotal
        _subtotal = totals.get("subtotal", 0)
        items, _fix_warnings = fix_line_items(items, subtotal=_subtotal, item_name_key="name")
        warnings.extend(_fix_warnings)

        cgst = taxes.get("cgst", {}).get("amount", 0)
        sgst = taxes.get("sgst", {}).get("amount", 0)
        igst = taxes.get("igst", {}).get("amount", 0)

        transactions = []
        for idx, item in enumerate(items):
            quantity = float(item.get("quantity", 1))
            rate = float(item.get("rate", 0))
            amount = float(item.get("amount", quantity * rate))

            calculated = quantity * rate
            if amount > 0 and abs(calculated - amount) / max(amount, 1) > 0.05:
                if calculated > 0:
                    warnings.append(f"Item {idx+1}: Amount corrected from {amount} to {calculated}")
                    amount = calculated

            discount_amount, discount_percentage, _disc_warnings = derive_discount(
                item, quantity, rate
            )
            for w in _disc_warnings:
                warnings.append(f"Item {idx+1}: {w}")

            # Line amount the user sees and pushes to Tally is NET of discount.
            net_amount = round(max(amount - discount_amount, 0.0), 2) if discount_amount > 0 else amount
            tx = {
                "date": invoice.get("date", ""),
                "description": item.get("name", ""),
                "reference_number": invoice.get("number", ""),
                "place_of_supply": invoice.get("place_of_supply", vendor.get("state", "")),
                "amount": net_amount,
                "quantity": quantity,
                "rate": rate,
                "discount_amount": discount_amount,
                "discount_percentage": discount_percentage,
                "hsn_code": item.get("hsn_code", ""),
                "gst_percentage": float(item.get("gst_rate", 0)),
                "gst_amount": net_amount * float(item.get("gst_rate", 0)) / 100,
                "total_amount": net_amount + (net_amount * float(item.get("gst_rate", 0)) / 100),
                "type": "purchase",
                "credit_ledger": vendor.get("name", "Cash"),
                "debit_ledger": "Purchase",
                "vendor_gstin": vendor.get("gstin", ""),
                "vendor_address": vendor.get("address", ""),
                "normalized_vendor_name": vendor.get("normalized_name", ""),
            }
            transactions.append(tx)

        if transactions:
            transactions[0]["_metadata"] = {
                "totals": totals,
                "taxes": taxes,
                "tally_mapping": tally_mapping,
                "confidence": confidence,
                "warnings": warnings,
                "vendor": vendor,
                "invoice": invoice,
            }

        # Common post-processing
        for tx in transactions:
            if not tx.get('date'):
                tx['date'] = '01/01/2025'
            if not tx.get('description'):
                tx['description'] = 'Purchase item'
            if not tx.get('reference_number'):
                tx['reference_number'] = ''
            if not tx.get('place_of_supply'):
                tx['place_of_supply'] = ''
            if not tx.get('amount'):
                tx['amount'] = 0
            if not tx.get('gst_percentage'):
                tx['gst_percentage'] = 0
            if not tx.get('gst_amount'):
                tx['gst_amount'] = 0
            if not tx.get('total_amount'):
                tx['total_amount'] = tx.get('amount', 0) + tx.get('gst_amount', 0)
            tx['type'] = 'purchase'
            if not tx.get('credit_ledger'):
                tx['credit_ledger'] = 'Cash'
            if not tx.get('debit_ledger'):
                tx['debit_ledger'] = 'Purchase'

        logger.info(f"Claude purchase extraction successful: {len(transactions)} transactions")
        return transactions

    except Exception as e:
        logger.error(f"Claude purchase extraction error: {e}")
        import traceback; traceback.print_exc()
        return []


async def _extract_purchase_transactions_gemini(
    extracted_text: str,
    file_path: str,
    gemini_model: str,
    google_api_key: str,
    inventory_names: Optional[List[str]] = None,
    inventory_names_with_units: Optional[List[Dict[str, str]]] = None
) -> List[Dict[str, Any]]:
    """Legacy: Extract transactions from purchase documents using Gemini."""
    start_time = __import__('time').time()
    logger.info("Starting purchase transaction extraction using Gemini")
    print("\nExtracting purchase transactions using Gemini...")
    
    try:
        # First identify the vendor from the document
        vendor_name = await identify_vendor_from_document(extracted_text, gemini_model, google_api_key)
        logger.info(f"Identified vendor: {vendor_name}")
        
        # Check if API key is valid
        if not google_api_key or google_api_key.strip() == "":
            raise ValueError("Missing Google API key - please provide a valid API key")
        
        # Initialize Gemini client
        client = genai.Client(api_key=google_api_key)

        # Prepare file content for Gemini processing
        file_extension = os.path.splitext(file_path)[1].lower()
        mime_type_map = {
            '.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif', '.bmp': 'image/bmp',
            '.tiff': 'image/tiff', '.tif': 'image/tiff', '.webp': 'image/webp',
        }

        content_parts = []
        mime_type = mime_type_map.get(file_extension)

        if mime_type:
            with open(file_path, 'rb') as file:
                file_content = file.read()
            content_parts.append(genai_types.Part.from_bytes(data=file_content, mime_type=mime_type))
        else:
            content_parts.append(genai_types.Part.from_text(text=extracted_text))
        
        # Extract file name without extension for potential matching
        file_name = os.path.splitext(os.path.basename(file_path))[0].lower()
        
        # Enhanced prompt for purchase transaction extraction
        prompt = f"""Extract all purchase transactions from this document.

File Name: {file_name}

For each transaction:
1. Extract invoice date (use format 01/01/2025 for all dates)
2. Extract description or item name
3. Extract reference number (invoice number, bill number, etc.)
4. Extract place of supply (state name)
5. Extract the exact amount without GST
6. Extract GST percentage and amount if available

Return in this JSON format:
{{
    "transactions": [
        {{
            "date": "01/01/2025",
            "description": "item description or details",
            "reference_number": "invoice or bill number",
            "place_of_supply": "state name",
            "amount": amount (numeric value without GST),
            "gst_percentage": percentage (numeric value),
            "gst_amount": amount (numeric value),
            "total_amount": total amount including GST (numeric value)
        }},
        ...more transactions...
    ]
}}

IMPORTANT RULES:
1. DO NOT return null for any field - use empty string "" if value is not found
2. For description, use the full item description if available
3. For place_of_supply, extract the state name
4. For amount, use the exact numeric value without currency symbol
5. For GST, extract both percentage and amount
6. For total_amount, calculate amount + gst_amount
7. The type is always "purchase" so no need to extract it"""

        content_parts.append(genai_types.Part.from_text(text=prompt))

        # Configure generation settings
        config = genai_types.GenerateContentConfig(
            temperature=0.1,
            top_p=0.95,
            top_k=40,
            max_output_tokens=8192,
            safety_settings=[
                genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
                genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            ]
        )

        # Process with Gemini
        logger.info("Sending document to Gemini for transaction extraction")
        print("Processing document with Gemini...")

        response = client.models.generate_content(
            model=gemini_model,
            contents=content_parts,
            config=config
        )
        
        # Extract content from response
        content = response.text.strip()
        
        # Parse JSON from response
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        try:
            extracted_data = json.loads(content)
            transactions = extracted_data.get("transactions", [])
            
            # Process empty values in transactions
            for tx in transactions:
                # Set default values for empty fields
                if not tx.get('date'):
                    tx['date'] = '01/01/2025'
                if not tx.get('description'):
                    tx['description'] = 'Purchase item'
                if not tx.get('reference_number'):
                    tx['reference_number'] = ''
                if not tx.get('place_of_supply'):
                    tx['place_of_supply'] = ''
                if not tx.get('amount'):
                    tx['amount'] = 0
                if not tx.get('gst_percentage'):
                    tx['gst_percentage'] = 0
                if not tx.get('gst_amount'):
                    tx['gst_amount'] = 0
                if not tx.get('total_amount'):
                    tx['total_amount'] = tx.get('amount', 0) + tx.get('gst_amount', 0)
                
                # Always add type as purchase
                tx['type'] = 'purchase'
            
            # Post-process transactions to add credit/debit ledger fields
            processed_transactions = []
            for tx in transactions:
                # Create a copy of the transaction
                processed_tx = tx.copy()
                
                # In Purchase: 
                # Credit ledger is the vendor (we pay to vendor)
                # Debit ledger is Purchase (expense account)
                processed_tx['credit_ledger'] = vendor_name
                processed_tx['debit_ledger'] = "Purchase"
                
                processed_transactions.append(processed_tx)
            
            logger.info(f"Extracted {len(processed_transactions)} purchase transactions successfully")
            print(f"Successfully extracted {len(processed_transactions)} purchase transactions")
            return processed_transactions
            
        except json.JSONDecodeError:
            # If JSON parsing fails, try to extract JSON object with regex
            json_match = re.search(r'({[\s\S]*})', content)
            if json_match:
                try:
                    extracted_data = json.loads(json_match.group(1))
                    transactions = extracted_data.get("transactions", [])
                    
                    # Process empty values in transactions
                    for tx in transactions:
                        # Set default values for empty fields
                        if not tx.get('date'):
                            tx['date'] = '01/01/2025'
                        if not tx.get('description'):
                            tx['description'] = 'Purchase item'
                        if not tx.get('reference_number'):
                            tx['reference_number'] = ''
                        if not tx.get('place_of_supply'):
                            tx['place_of_supply'] = ''
                        if not tx.get('amount'):
                            tx['amount'] = 0
                        if not tx.get('gst_percentage'):
                            tx['gst_percentage'] = 0
                        if not tx.get('gst_amount'):
                            tx['gst_amount'] = 0
                        if not tx.get('total_amount'):
                            tx['total_amount'] = tx.get('amount', 0) + tx.get('gst_amount', 0)
                        
                        # Always add type as purchase
                        tx['type'] = 'purchase'
                    
                    # Post-process transactions
                    processed_transactions = []
                    for tx in transactions:
                        processed_tx = tx.copy()
                        
                        # Add credit/debit ledger fields
                        processed_tx['credit_ledger'] = vendor_name
                        processed_tx['debit_ledger'] = "Purchase"
                        
                        processed_transactions.append(processed_tx)
                    
                    return processed_transactions
                except:
                    logger.error("Failed to parse JSON from Gemini response")
                    raise ValueError("Failed to parse Gemini response - no transactions extracted")
            else:
                logger.error("No JSON pattern found in Gemini response")
                raise ValueError("Invalid Gemini response format - no transactions extracted")
                
    except Exception as e:
        logger.error(f"Error in purchase transaction extraction: {str(e)}")
        print(f"Error in purchase transaction extraction: {str(e)}")
        return []
    finally:
        elapsed_time = __import__('time').time() - start_time
        logger.info(f"Purchase transaction extraction completed in {elapsed_time:.2f} seconds")


async def match_purchase_transaction_ledgers(
    transactions: List[Dict[str, Any]],
    available_ledgers: List[Dict[str, str]],
    openai_api_key: str,
    openai_model: str,
    user_id: Optional[str] = None,
    company_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Match appropriate ledgers for purchase transactions using the unified matcher."""
    if not transactions:
        logger.warning("No transactions to match ledgers for")
        return []

    start_time = __import__('time').time()
    logger.info(f"Starting ledger matching for {len(transactions)} purchase transactions")
    print(f"\nMatching ledgers for {len(transactions)} purchase transactions...")

    try:
        from applicationca.services.ledger_matcher import LedgerMatcher

        matcher = LedgerMatcher(
            user_id=user_id,
            company_name=company_name,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
        )

        available_names = [
            l.get("name", "").strip()
            for l in available_ledgers
            if l.get("name", "").strip()
        ]

        tasks = []
        for tx in transactions:
            # Credit ledger = vendor (use match_single with "vendor" type)
            credit = tx.get('credit_ledger', '').strip()
            if credit:
                tasks.append(matcher.match_single(credit, available_names, "vendor"))

            # Debit ledger = expense category
            debit = tx.get('debit_ledger', '').strip()
            if debit:
                tasks.append(matcher.match_single(debit, available_names, "expense"))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Known defaults — don't overwrite the original extracted name with these
        _DEFAULTS = {"Cash", "Purchase", "Sales"}

        result_index = 0
        for tx in transactions:
            if tx.get('credit_ledger'):
                original_credit = tx['credit_ledger']
                if result_index < len(results) and not isinstance(results[result_index], Exception):
                    matched = results[result_index]
                    if matched not in _DEFAULTS:
                        tx['credit_ledger'] = matched
                    # else: keep original extracted vendor name
                # else: keep original on error
                result_index += 1

            if tx.get('debit_ledger'):
                if result_index < len(results) and not isinstance(results[result_index], Exception):
                    tx['debit_ledger'] = results[result_index]
                else:
                    tx['debit_ledger'] = "Purchase"
                result_index += 1

        logger.info(f"Successfully matched ledgers for {len(transactions)} purchase transactions")
        print(f"Successfully matched ledgers for {len(transactions)} purchase transactions")
        return transactions

    except Exception as e:
        logger.error(f"Error in purchase ledger matching: {str(e)}")
        for tx in transactions:
            if 'credit_ledger' in tx:
                tx['credit_ledger'] = "Cash"
            if 'debit_ledger' in tx:
                tx['debit_ledger'] = "Purchase"
        return transactions
    finally:
        elapsed_time = __import__('time').time() - start_time
        logger.info(f"Purchase ledger matching completed in {elapsed_time:.2f} seconds")

def escape_xml_text(text: str) -> str:
    """Escape special characters for Tally XML."""
    if not text:
        return text
    result = str(text)
    result = result.replace('&', '&amp;')
    result = result.replace('<', '&lt;')
    result = result.replace('>', '&gt;')
    result = result.replace('"', '&quot;')
    result = result.replace("'", '&#39;')
    return result


def generate_purchase_xml(transactions: List[Dict[str, Any]], company_name: str) -> str:
    """Generate Tally XML for purchase transactions."""
    if not transactions:
        logger.warning("No transactions to generate XML for")
        return ""

    start_time = __import__('time').time()
    logger.info(f"Generating Tally XML for {len(transactions)} purchase transactions")
    print(f"\nGenerating Tally XML for {len(transactions)} purchase transactions...")

    try:
        esc_company = escape_xml_text(company_name)

        xml = f"""<ENVELOPE>
 <HEADER>
  <TALLYREQUEST>Import Data</TALLYREQUEST>
 </HEADER>
 <BODY>
  <IMPORTDATA>
   <REQUESTDESC>
    <REPORTNAME>Vouchers</REPORTNAME>
    <STATICVARIABLES>
     <SVCURRENTCOMPANY>{esc_company}</SVCURRENTCOMPANY>
    </STATICVARIABLES>
   </REQUESTDESC>
   <REQUESTDATA>"""

        for tx in transactions:
            date_str = tx.get('date', '01/01/2025')
            date = '20250101'

            if isinstance(date_str, str):
                if len(date_str) == 10 and date_str[2] == '/' and date_str[5] == '/':
                    day, month, year = date_str.split('/')
                    date = f"{year}{month}{day}"
                elif len(date_str) == 10 and date_str[4] == '-' and date_str[7] == '-':
                    year, month, day = date_str.split('-')
                    date = f"{year}{month}{day}"

            esc_description = escape_xml_text(str(tx.get('description', 'Purchase Transaction')))
            esc_reference = escape_xml_text(str(tx.get('reference_number', '')))
            esc_place = escape_xml_text(str(tx.get('place_of_supply', '')))
            esc_credit = escape_xml_text(str(tx.get('credit_ledger', 'Cash')))
            esc_debit = escape_xml_text(str(tx.get('debit_ledger', 'Purchase')))

            try:
                amount = float(tx.get('amount', 0))
            except (TypeError, ValueError):
                amount = 0.0
            try:
                gst_percentage = float(tx.get('gst_percentage', 0))
            except (TypeError, ValueError):
                gst_percentage = 0.0
            try:
                gst_amount = float(tx.get('gst_amount', 0))
            except (TypeError, ValueError):
                gst_amount = 0.0
            try:
                total_amount = float(tx.get('total_amount', amount + gst_amount))
            except (TypeError, ValueError):
                total_amount = amount + gst_amount

            xml += f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
     <VOUCHER VCHTYPE="Purchase" ACTION="Create">
      <DATE>{date}</DATE>
      <NARRATION>{esc_description}</NARRATION>
      <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
      <REFERENCE>{esc_reference}</REFERENCE>
      <EFFECTIVEDATE>{date}</EFFECTIVEDATE>"""

            if esc_place:
                xml += f"""
      <STATENAME>{esc_place}</STATENAME>"""

            # Debit ledger entry (Purchase account) — DEBIT in the books.
            # Tally XML sign convention: DEBIT = ISDEEMEDPOSITIVE=Yes + NEGATIVE
            # amount. (Was previously No/positive, which inverted the voucher.)
            xml += f"""
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{esc_debit}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-{amount:.2f}</AMOUNT>"""

            if gst_amount > 0:
                xml += f"""
       <TAXCLASSIFICATIONNAME>GST</TAXCLASSIFICATIONNAME>
       <VATEXPAMOUNT>{amount:.2f}</VATEXPAMOUNT>
       <VATPERCENTAGE>{gst_percentage:.2f}</VATPERCENTAGE>"""

            xml += """
      </ALLLEDGERENTRIES.LIST>"""

            # GST ITC entries
            if gst_amount > 0:
                half_gst = gst_amount / 2
                half_pct = gst_percentage / 2

                # GST ITC is a DEBIT (input credit receivable from govt).
                xml += f"""
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>CGST ITC</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-{half_gst:.2f}</AMOUNT>
       <TAXCLASSIFICATIONNAME>CGST ITC</TAXCLASSIFICATIONNAME>
       <VATEXPAMOUNT>{amount:.2f}</VATEXPAMOUNT>
       <VATPERCENTAGE>{half_pct:.2f}</VATPERCENTAGE>
      </ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>SGST ITC</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-{half_gst:.2f}</AMOUNT>
       <TAXCLASSIFICATIONNAME>SGST ITC</TAXCLASSIFICATIONNAME>
       <VATEXPAMOUNT>{amount:.2f}</VATEXPAMOUNT>
       <VATPERCENTAGE>{half_pct:.2f}</VATPERCENTAGE>
      </ALLLEDGERENTRIES.LIST>"""

            # Credit ledger entry (vendor) — CREDIT in the books, payable.
            # CREDIT = ISDEEMEDPOSITIVE=No + POSITIVE amount.
            xml += f"""
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{esc_credit}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <AMOUNT>{total_amount:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
     </VOUCHER>
    </TALLYMESSAGE>"""

        xml += """
   </REQUESTDATA>
  </IMPORTDATA>
 </BODY>
</ENVELOPE>"""

        print("\nGenerated XML:")
        print(xml)

        logger.info(f"Successfully generated Tally XML for {len(transactions)} purchase transactions")
        return xml

    except Exception as e:
        logger.error(f"Error generating purchase transaction XML: {str(e)}")
        return ""
    finally:
        elapsed_time = __import__('time').time() - start_time
        logger.info(f"Purchase XML generation completed in {elapsed_time:.2f} seconds") 