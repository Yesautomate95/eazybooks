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

def escape_xml_entities(text: str) -> str:
    """Convert special characters to HTML/XML entities for Tally XML format."""
    if not text:
        return text

    result = str(text)

    # Ampersand MUST be replaced first to avoid double-escaping
    result = result.replace('&', '&amp;')
    result = result.replace('<', '&lt;')
    result = result.replace('>', '&gt;')
    result = result.replace('"', '&quot;')
    result = result.replace("'", '&#39;')

    # Handle line endings
    result = result.replace('\r\n', '&#13;&#10;')
    result = result.replace('\n', '&#10;')
    result = result.replace('\r', '&#13;')
    result = result.replace('\t', '&#9;')

    return result

async def match_inventory_item(
    llm: ChatOpenAI,
    item_name: str,
    available_inventory: List[str]
) -> str:
    """Match a single inventory item name with available inventory using GPT."""
    try:
        # First try exact match
        logger.info(f"Checking for exact match for: '{item_name}'")
        for inventory_item in available_inventory:
            if item_name.lower().replace(" ", "") == inventory_item.lower().replace(" ", ""):
                logger.info(f"Found exact match (ignoring spaces): '{inventory_item}'")
                return inventory_item

        prompt = f"""Find the best matching inventory item from the available list and return it EXACTLY as it appears in the list.

Item Name from Document: {item_name}

Available Inventory Items:
{json.dumps(available_inventory, indent=2)}

Rules:
1. You MUST return the inventory name EXACTLY as it appears in the available list - including all parentheses (), brackets [], special characters
2. Example: If matching "Stanley Hammer Black Chrome" with "Stanley Hammer (Black & Chrome)", you MUST return "Stanley Hammer (Black & Chrome)"
3. DO NOT remove or change ANY characters from the matched name
4. DO NOT reformat or modify the matched name in any way
5. The output MUST be an EXACT COPY of one of the names from the available list
6. If unsure, return the original item name: {item_name}

Return ONLY the matched inventory name, copying it exactly from the available list."""

        logger.info("Sending matching request to GPT")
        response = await llm.ainvoke([
            SystemMessage(content="You are an inventory matching assistant. You MUST return the exact inventory name as it appears in the list, including all special characters and formatting."),
            HumanMessage(content=prompt)
        ])
        
        matched_item = response.content.strip()
        logger.info(f"GPT suggested match: '{matched_item}'")
        
        # Strict validation - must be exactly as in available list
        if matched_item in available_inventory:
            logger.info(f"Successfully matched '{item_name}' to '{matched_item}'")
            return matched_item
        else:
            # Try to find if GPT returned a close match but with wrong formatting
            for inventory_item in available_inventory:
                if matched_item.lower().replace(" ", "") == inventory_item.lower().replace(" ", ""):
                    logger.info(f"Found correct match with proper formatting: '{inventory_item}'")
                    return inventory_item
            
            logger.warning(f"No valid match found for '{item_name}', keeping original name")
            return item_name
            
    except Exception as e:
        logger.error(f"Error matching inventory item '{item_name}': {str(e)}")
        return item_name

async def match_inventory_items_batch(
    llm: ChatOpenAI,
    items: List[Dict[str, Any]],
    available_inventory: List[str]
) -> List[str]:
    """Match multiple inventory item names with available inventory using GPT in a single call."""
    try:
        # Extract item names
        item_names = [item.get("item_name", "") for item in items if item.get("item_name")]
        
        prompt = f"""Match each inventory item from the document with the best matching item from the available inventory list.

Items from Document:
{json.dumps(item_names, indent=2)}

Available Inventory Items:
{json.dumps(available_inventory, indent=2)}

Rules:
1. You MUST return a JSON array with matched names in the EXACT same order as the input items
2. For each item, return the EXACT inventory name from the available list
3. If no good match is found, return the original item name
4. Do not modify or create new names
5. Include ALL special characters, spaces, and formatting from the available list
6. The output array MUST have the same length as the input array

Return ONLY a valid JSON array of matched names, nothing else."""

        logger.info("Sending batch matching request to GPT")
        response = await llm.ainvoke([
            SystemMessage(content="You are an inventory matching assistant that matches items to a master inventory list."),
            HumanMessage(content=prompt)
        ])
        
        try:
            matched_names = json.loads(response.content.strip())
            if not isinstance(matched_names, list):
                raise ValueError("Response is not a list")
            
            # Validate each matched name
            validated_matches = []
            for idx, (original_name, matched_name) in enumerate(zip(item_names, matched_names)):
                if matched_name in available_inventory:
                    validated_matches.append(matched_name)
                    logger.info(f"Successfully matched '{original_name}' to '{matched_name}'")
                else:
                    # Try to find if there's a case/space insensitive match
                    for inventory_item in available_inventory:
                        if matched_name.lower().replace(" ", "") == inventory_item.lower().replace(" ", ""):
                            validated_matches.append(inventory_item)
                            logger.info(f"Found case-insensitive match: '{original_name}' -> '{inventory_item}'")
                            break
                    else:
                        validated_matches.append(original_name)
                        logger.warning(f"No valid match found for '{original_name}', keeping original name")
            
            return validated_matches
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse GPT response as JSON: {str(e)}")
            return item_names
            
    except Exception as e:
        logger.error(f"Error in batch inventory matching: {str(e)}")
        return [item.get("item_name", "") for item in items]

async def extract_sales_transactions(
    extracted_text: str,
    file_path: str,
    gemini_model: str,
    google_api_key: str,
    inventory_names: Optional[List[str]] = None,
    inventory_names_with_units: Optional[List[Dict[str, str]]] = None,
    gemini_fallback_model: str = "gemini-2.5-flash",
    anthropic_api_key: Optional[str] = None,
    extraction_provider: str = "gemini",
) -> List[Dict[str, Any]]:
    """Extract transactions from sales documents using the configured provider.
    Uses gemini_model as primary for Gemini, falls back to gemini_fallback_model on failure."""
    start_time = __import__('time').time()

    if extraction_provider == "claude":
        logger.info("Starting sales transaction extraction using Claude")
        print("\nExtracting sales transactions using Claude...")
        anthropic_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        claude_model = os.getenv("CLAUDE_EXTRACTION_MODEL", "claude-opus-4-7")
        if anthropic_key:
            result = await _extract_sales_with_claude(
                file_path=file_path,
                extracted_text=extracted_text,
                anthropic_api_key=anthropic_key,
                claude_model=claude_model,
                inventory_names=inventory_names,
                inventory_names_with_units=inventory_names_with_units,
            )
            if result:
                return result
        logger.error("Claude sales extraction failed or no ANTHROPIC_API_KEY")
        return []

    logger.info(f"Starting sales transaction extraction using Gemini ({gemini_model})")
    print(f"\nExtracting sales transactions using Gemini ({gemini_model})...")

    try:
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
        
        # Enhanced prompt for sales transaction extraction with semantic understanding
        prompt = f"""You are an expert accounting assistant specialized in Indian GST invoices and Tally ERP voucher creation.

Your task is NOT just to extract text, but to UNDERSTAND the document like an accountant and produce structured, Tally-ready data.

File Name: {file_name}

════════════════════════════════════════════════════════════════════════════════
STEP 1: UNDERSTAND THE DOCUMENT SEMANTICS
════════════════════════════════════════════════════════════════════════════════
First, identify:
- Who ISSUED the invoice (Seller/Supplier) — usually at the top with their logo/company name
- Who RECEIVED the invoice (Buyer/Customer) — usually in "Bill To", "Ship To", "Buyer Details"
- Nature of transaction: Is this a SALES invoice (you are the seller) or PURCHASE invoice (you are the buyer)?

**CRITICAL RULE — PARTY IDENTIFICATION:**
- The PARTY (ledger) is ALWAYS the COUNTERPARTY:
  - In SALES invoice → Party = Customer/Buyer (the one being billed)
  - In PURCHASE invoice → Party = Vendor/Supplier (the one who issued the bill)
- **DO NOT confuse the seller's details with the buyer's details**
- The seller's GSTIN is usually prominent at top; the buyer's GSTIN is in billing section

════════════════════════════════════════════════════════════════════════════════
STEP 2: EXTRACT PARTY DETAILS (MOST IMPORTANT)
════════════════════════════════════════════════════════════════════════════════
For the PARTY (counterparty), extract:

1. **party_name**: Clean name without prefixes like "M/s" unless part of legal name
   - Look in: "Bill To", "Ship To", "Buyer", "Customer", "Consignee", "Party Name"
   - Also provide a **normalized_name**: lowercase, no special characters, for matching

2. **gst_number**: The PARTY's 15-character GSTIN
   - **CRITICAL**: This must be the BUYER's GSTIN for sales, VENDOR's GSTIN for purchase
   - DO NOT extract the invoice issuer's own GSTIN here
   - Validate format: 2 digits (state) + 5 letters (PAN) + 4 digits + 1 letter + 1 alphanumeric + Z + 1 alphanumeric

3. **place_of_supply**: Full STATE NAME (e.g., "Maharashtra" not "MH")
   - Derive from party's address or first 2 digits of their GSTIN:
     01=Jammu & Kashmir, 02=Himachal Pradesh, 03=Punjab, 04=Chandigarh, 05=Uttarakhand,
     06=Haryana, 07=Delhi, 08=Rajasthan, 09=Uttar Pradesh, 10=Bihar, 11=Sikkim,
     12=Arunachal Pradesh, 13=Nagaland, 14=Manipur, 15=Mizoram, 16=Tripura, 17=Meghalaya,
     18=Assam, 19=West Bengal, 20=Jharkhand, 21=Odisha, 22=Chhattisgarh, 23=Madhya Pradesh,
     24=Gujarat, 27=Maharashtra, 29=Karnataka, 32=Kerala, 33=Tamil Nadu, 36=Telangana, 37=Andhra Pradesh

4. **dates**: All dates in DD/MM/YYYY format (Invoice Date, Due Date, etc.)
5. **invoice_number**: Look for "Invoice No.", "Bill No.", "Voucher No.", "Document No."

════════════════════════════════════════════════════════════════════════════════
STEP 3: EXTRACT LINE ITEMS WITH CALCULATION VALIDATION
════════════════════════════════════════════════════════════════════════════════
For EACH line item, you MUST correctly identify these THREE DIFFERENT values:

| Column Type | What It Means | Example |
|------------|---------------|---------|
| QUANTITY | Number of units | 10 |
| RATE | Price per ONE unit | 500 |
| AMOUNT | Total = Qty × Rate | 5000 |

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

**VALIDATION RULE**: For each item, verify: Quantity × Rate ≈ Amount
- If Amount = 5000, Qty = 10, Rate = 500 → CORRECT (10 × 500 = 5000)
- If Amount = 5000, Qty = 5000, Rate = 5000 → WRONG — re-examine columns!
- If validation fails, SWAP Qty and Rate and check again before adding to warnings

**CROSS-VALIDATE TOTALS**: After extracting all items, verify:
  sum(all item amounts) ≈ document subtotal (before tax). If they differ by more than 5%,
  you are likely reading the wrong column for Amount — re-check and correct.

Extract for each item:
1. **item_name**: Exact name as shown, preserve formatting
2. **quantity**: NUMBER OF UNITS (not amount!)
3. **rate**: PRICE PER SINGLE UNIT (not total!)
4. **amount**: Calculated or verified amount (Qty × Rate)
5. **hsn_code**: HSN/SAC code if shown
6. **gst_rate**: GST percentage for this item (e.g., 18, 12, 5)
7. **batch_number**: If available
8. **discount_amount**: Discount on this line in RUPEES (₹) — populate ONLY if
   the invoice shows the discount as an absolute amount (e.g. "Disc: ₹100" or
   "Less Discount 250"). Otherwise leave 0.
9. **discount_percentage**: Discount on this line as a PERCENT (plain number,
   5.5 not "5.5%") — populate ONLY if the invoice shows the discount as a
   percentage (e.g. "Disc: 5%", "Disc 10.5%"). Otherwise leave 0.

CRITICAL: do NOT convert between the two formats yourself. If the invoice
shows ₹100, set discount_amount=100 and discount_percentage=0. If it shows
5%, set discount_percentage=5 and discount_amount=0. The Python code will
derive whichever value is missing.

════════════════════════════════════════════════════════════════════════════════
STEP 4: EXTRACT TAX & CHARGE ENTRIES
════════════════════════════════════════════════════════════════════════════════
Read the tax breakup section of the invoice and extract EVERY tax/charge line shown:
- Extract EACH line as a SEPARATE entry with: ledger_name, amount, percentage, ledger_type
- Common entries: CGST, SGST, IGST, Cess, TCS, Freight, Packing, Insurance, Round Off, Discount
- Include entries even if their amount is 0 (if shown on the invoice)
- DO NOT invent entries that are not on the invoice
- DO NOT merge multiple tax lines into one

════════════════════════════════════════════════════════════════════════════════
STEP 5: CALCULATE TOTALS AND VALIDATE
════════════════════════════════════════════════════════════════════════════════
Calculate and verify:
- **subtotal** = Sum of all item amounts (before tax)
- **total_tax** = CGST + SGST or IGST
- **grand_total** = subtotal + total_tax + additional charges

If calculated totals don't match document totals (within 1% tolerance):
- Add warning to warnings array
- Use document totals but note discrepancy

════════════════════════════════════════════════════════════════════════════════
STEP 6: PREPARE TALLY MAPPING
════════════════════════════════════════════════════════════════════════════════
Determine Tally voucher structure:
- **transaction_type**: "sales" or "purchase"
- **voucher_type**: "Sales" or "Purchase"
- **party_ledger**: The counterparty name (for both debit/credit based on type)
- For Sales: Debit = Party (customer), Credit = Sales
- For Purchase: Debit = Purchase, Credit = Party (vendor)

════════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (STRICT JSON)
════════════════════════════════════════════════════════════════════════════════
Return this EXACT JSON structure:
{{
    "transaction_type": "sales",
    "party_details": {{
        "party_name": "Customer/Vendor name (clean, no M/s prefix unless legal name)",
        "normalized_name": "lowercase name without special chars for matching",
        "dates": ["DD/MM/YYYY"],
        "place_of_supply": "Full State Name",
        "gst_number": "15-character GSTIN of the PARTY (not issuer)",
        "invoice_number": "Invoice/Bill number",
        "address": "Party's address if available"
    }},
    "inventory_items": [
        {{
            "item_name": "exact item name",
            "quantity": 10,
            "rate": 500,
            "amount": 5000,
            "hsn_code": "HSN code if available",
            "gst_rate": 18,
            "batch_number": "",
            "discount_amount": 0,
            "discount_percentage": 0
        }}
    ],
    "ledger_entries": [
        {{
            "ledger_name": "CGST",
            "ledger_type": "tax",
            "amount": 450,
            "percentage": 9
        }},
        {{
            "ledger_name": "SGST",
            "ledger_type": "tax",
            "amount": 450,
            "percentage": 9
        }}
    ],
    "totals": {{
        "subtotal": 5000,
        "cgst": 450,
        "sgst": 450,
        "igst": 0,
        "other_charges": 0,
        "grand_total": 5900
    }},
    "tally_mapping": {{
        "voucher_type": "Sales",
        "party_ledger": "Customer Name",
        "sales_ledger": "Sales",
        "tax_ledgers": ["CGST", "SGST"]
    }},
    "confidence": {{
        "party_name": 0.95,
        "voucher_date": 0.95,
        "invoice_number": 0.95,
        "gst_number": 0.90,
        "items": 0.85,
        "totals": 0.90
    }},
    "warnings": []
}}

════════════════════════════════════════════════════════════════════════════════
CRITICAL RULES
════════════════════════════════════════════════════════════════════════════════
1. NO null values — use "" for text, 0 for numbers
2. QUANTITY ≠ RATE ≠ AMOUNT — these are THREE DIFFERENT values
3. Party GSTIN is the COUNTERPARTY's GSTIN, NOT the invoice issuer's
4. Always validate: Quantity × Rate ≈ Amount
5. Add warnings for: calculation mismatches, ambiguous party name, GSTIN format issues
6. Preserve exact item names with all formatting
7. Use DD/MM/YYYY for all dates
8. Remove commas and currency symbols from numbers
9. Full state names, not abbreviations"""

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

        # Process with Gemini (try primary model, fallback on failure)
        logger.info(f"Sending document to Gemini ({gemini_model}) for data extraction")
        print(f"Processing document with Gemini ({gemini_model})...")

        try:
            response = client.models.generate_content(
                model=gemini_model,
                contents=content_parts,
                config=config
            )
            content = response.text.strip()
        except Exception as primary_err:
            if gemini_fallback_model and gemini_fallback_model != gemini_model:
                logger.warning(f"Primary Gemini model ({gemini_model}) failed: {primary_err}. Trying fallback ({gemini_fallback_model})...")
                print(f"Primary model failed, falling back to {gemini_fallback_model}...")
                response = client.models.generate_content(
                    model=gemini_fallback_model,
                    contents=content_parts,
                    config=config
                )
                content = response.text.strip()
            else:
                raise
        
        # Log the raw response
        logger.info("Raw Gemini response:")
        logger.info(content)
        
        # Parse JSON from response
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
            logger.info("Extracted JSON content from code block:")
            logger.info(content)
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            logger.info("Extracted content from code block:")
            logger.info(content)
        
        try:
            extracted_data = json.loads(content)
            
            # Log the raw Gemini response for debugging
            logger.info("Raw Gemini response data:")
            logger.info(json.dumps(extracted_data, indent=2))
            
            # If we have inventory names available, match the items
            if inventory_names and extracted_data.get("inventory_items"):
                logger.info("Starting batch inventory item matching")
                
                # Initialize OpenAI client for matching
                llm = ChatOpenAI(
                    model="gpt-4.1-nano",
                    temperature=0.1,
                    openai_api_key=os.getenv("OPENAI_API_KEY")
                )
                
                # Batch match all inventory items
                matched_names = await match_inventory_items_batch(
                    llm,
                    extracted_data["inventory_items"],
                    inventory_names
                )
                
                # Update item names with matched names and add unit information
                for item, matched_name in zip(extracted_data["inventory_items"], matched_names):
                    original_name = item["item_name"]
                    item["item_name"] = matched_name
                    
                    # Add unit information if available
                    if inventory_names_with_units:
                        for inventory_item in inventory_names_with_units:
                            if inventory_item["name"] == matched_name:
                                item["unit"] = inventory_item.get("unit", "Nos")
                                logger.info(f"Found unit '{item['unit']}' for item '{matched_name}'")
                                break
                        else:
                            item["unit"] = "Nos"  # Default unit if not found
                    else:
                        item["unit"] = "Nos"  # Default unit
                        
                    if original_name != matched_name:
                        logger.info(f"Matched inventory item: '{original_name}' -> '{matched_name}'")
            
            # Extract transaction type (new field)
            transaction_type = extracted_data.get("transaction_type", "sales")
            logger.info(f"Transaction type: {transaction_type}")

            # Validate and process party details
            party_details = extracted_data.get("party_details", {})
            if not isinstance(party_details, dict):
                party_details = {}
                logger.warning("Party details was not a dictionary, using empty dict")
            else:
                logger.info("Party details found:")
                logger.info(json.dumps(party_details, indent=2))

            # Ensure all required fields exist with default values
            party_details.setdefault("party_name", "")
            party_details.setdefault("normalized_name", party_details.get("party_name", "").lower().replace(" ", "").replace(".", "").replace(",", ""))
            party_details.setdefault("dates", [])
            party_details.setdefault("place_of_supply", "")
            party_details.setdefault("gst_number", "")
            party_details.setdefault("invoice_number", "")
            party_details.setdefault("address", "")
            
            # Validate and process inventory items
            inventory_items = extracted_data.get("inventory_items", [])
            if not isinstance(inventory_items, list):
                inventory_items = []
                logger.warning("Inventory items was not a list, using empty list")
            else:
                logger.info(f"Found {len(inventory_items)} inventory items:")
                logger.info(json.dumps(inventory_items, indent=2))
            
            # Process each inventory item with calculation validation
            processed_items = []
            warnings = extracted_data.get("warnings", [])

            # Fix qty/rate swaps and cross-validate against subtotal
            _subtotal = extracted_data.get("totals", {}).get("subtotal", 0)
            inventory_items, _fix_warnings = fix_line_items(inventory_items, subtotal=_subtotal)
            warnings.extend(_fix_warnings)

            for idx, item in enumerate(inventory_items, 1):
                if isinstance(item, dict):
                    # Preserve original item name formatting (including newlines and special characters)
                    original_item_name = str(item.get("item_name", ""))
                    quantity = float(item.get("quantity", 0))
                    rate = float(item.get("rate", 0))

                    # Calculate expected amount and validate
                    calculated_amount = quantity * rate
                    extracted_amount = float(item.get("amount", 0))

                    # Use extracted amount if provided and close to calculated, otherwise use calculated
                    if extracted_amount > 0 and abs(calculated_amount - extracted_amount) / max(extracted_amount, 1) < 0.05:
                        final_amount = extracted_amount
                    elif calculated_amount > 0:
                        final_amount = calculated_amount
                        if extracted_amount > 0 and abs(calculated_amount - extracted_amount) > 1:
                            warnings.append(f"Item {idx}: Amount corrected from {extracted_amount} to {calculated_amount}")
                    else:
                        final_amount = extracted_amount

                    discount_amount, discount_percentage, disc_warnings = derive_discount(
                        item, quantity, rate
                    )
                    for w in disc_warnings:
                        warnings.append(f"Item {idx} ({original_item_name}): {w}")

                    # Persist `amount` as the NET line value (after discount) so the
                    # frontend's amount column matches the cash impact, the subtotal
                    # is straight `sum(amount)`, and Tally's tax base is unambiguous.
                    net_amount = (
                        round(max(final_amount - discount_amount, 0.0), 2)
                        if discount_amount > 0
                        else final_amount
                    )

                    processed_item = {
                        "item_name": original_item_name,  # Keep original formatting
                        "quantity": quantity,
                        "rate": rate,
                        "amount": net_amount,
                        "hsn_code": str(item.get("hsn_code", "")),
                        "gst_rate": float(item.get("gst_rate", 0)),
                        "batch_number": str(item.get("batch_number", "")),
                        "discount_amount": discount_amount,
                        "discount_percentage": discount_percentage,
                        "unit": item.get("unit", "Nos")
                    }
                    processed_items.append(processed_item)
                    logger.info(f"Processed inventory item {idx} (preserving original formatting):")
                    logger.info(json.dumps(processed_item, indent=2))
                else:
                    logger.warning(f"Inventory item {idx} was not a dictionary, skipping")
            
            # Validate and process ledger entries
            ledger_entries = extracted_data.get("ledger_entries", [])
            if not isinstance(ledger_entries, list):
                ledger_entries = []
                logger.warning("Ledger entries was not a list, using empty list")
            else:
                logger.info(f"Found {len(ledger_entries)} ledger entries:")
                logger.info(json.dumps(ledger_entries, indent=2))
            
            # Process each ledger entry
            processed_ledger_entries = []
            for idx, entry in enumerate(ledger_entries, 1):
                if isinstance(entry, dict):
                    processed_entry = {
                        "ledger_name": str(entry.get("ledger_name", "")),
                        "ledger_type": str(entry.get("ledger_type", "")),
                        "amount": float(entry.get("amount", 0)),
                        "percentage": float(entry.get("percentage", 0))
                    }
                    processed_ledger_entries.append(processed_entry)
                    logger.info(f"Processed ledger entry {idx}:")
                    logger.info(json.dumps(processed_entry, indent=2))
                else:
                    logger.warning(f"Ledger entry {idx} was not a dictionary, skipping")
            
            # Extract confidence scores (with heuristic fallbacks)
            raw_confidence = extracted_data.get("confidence", {})
            confidence = {
                "party_name": float(raw_confidence.get("party_name", 0.85 if party_details.get("party_name") else 0.3)),
                "voucher_date": float(raw_confidence.get("voucher_date", 0.85 if party_details.get("dates") else 0.3)),
                "invoice_number": float(raw_confidence.get("invoice_number", 0.85 if party_details.get("invoice_number") else 0.3)),
                "gst_number": float(raw_confidence.get("gst_number", 0.85 if party_details.get("gst_number") else 0.3)),
                "items": float(raw_confidence.get("items", 0.85 if processed_items else 0.3)),
                "totals": float(raw_confidence.get("totals", 0.85)),
            }

            # Extract totals (new field)
            raw_totals = extracted_data.get("totals", {})
            totals = {
                "subtotal": float(raw_totals.get("subtotal", sum(item.get("amount", 0) for item in processed_items))),
                "cgst": float(raw_totals.get("cgst", 0)),
                "sgst": float(raw_totals.get("sgst", 0)),
                "igst": float(raw_totals.get("igst", 0)),
                "other_charges": float(raw_totals.get("other_charges", 0)),
                "grand_total": float(raw_totals.get("grand_total", 0)),
            }

            # Calculate grand_total if not provided
            if totals["grand_total"] == 0:
                totals["grand_total"] = totals["subtotal"] + totals["cgst"] + totals["sgst"] + totals["igst"] + totals["other_charges"]

            # Extract tally_mapping (new field)
            raw_tally_mapping = extracted_data.get("tally_mapping", {})
            tally_mapping = {
                "voucher_type": raw_tally_mapping.get("voucher_type", "Sales" if transaction_type == "sales" else "Purchase"),
                "party_ledger": raw_tally_mapping.get("party_ledger", party_details.get("party_name", "")),
                "sales_ledger": raw_tally_mapping.get("sales_ledger", "Sales"),
                "purchase_ledger": raw_tally_mapping.get("purchase_ledger", "Purchase"),
                "tax_ledgers": raw_tally_mapping.get("tax_ledgers", []),
            }

            # Create the final processed data (backward compatible + new fields)
            processed_data = {
                "transaction_type": transaction_type,
                "party_details": party_details,
                "inventory_items": processed_items,
                "ledger_entries": processed_ledger_entries,
                "totals": totals,
                "tally_mapping": tally_mapping,
                "confidence": confidence,
                "warnings": warnings,
            }

            logger.info("Final processed data:")
            logger.info(json.dumps(processed_data, indent=2))
            
            logger.info(f"Successfully extracted data with {len(processed_items)} inventory items and {len(processed_ledger_entries)} ledger entries")
            print(f"Successfully extracted data with {len(processed_items)} inventory items and {len(processed_ledger_entries)} ledger entries")
            
            return [processed_data]  # Return as list for compatibility with existing code
            
        except json.JSONDecodeError as e:
            # Log the error and content that caused it
            logger.error(f"JSON decode error: {str(e)}")
            logger.error("Content that failed to parse:")
            logger.error(content)
            
            # If JSON parsing fails, try to extract JSON object with regex
            json_match = re.search(r'({[\s\S]*})', content)
            if json_match:
                try:
                    matched_content = json_match.group(1)
                    logger.info("Found JSON-like content using regex:")
                    logger.info(matched_content)
                    return [json.loads(matched_content)]
                except Exception as e:
                    logger.error(f"Failed to parse JSON from regex match: {str(e)}")
                    logger.error("Regex matched content:")
                    logger.error(matched_content)
                    raise ValueError("Failed to parse Gemini response - no data extracted")
            else:
                logger.error("No JSON pattern found in Gemini response")
                raise ValueError("Invalid Gemini response format - no data extracted")
                
    except Exception as e:
        logger.error(f"Error in sales data extraction: {str(e)}")
        print(f"Error in sales data extraction: {str(e)}")
        return []
    finally:
        elapsed_time = __import__('time').time() - start_time
        logger.info(f"Sales data extraction completed in {elapsed_time:.2f} seconds")


async def _extract_sales_with_claude(
    file_path: str,
    extracted_text: str,
    anthropic_api_key: str,
    claude_model: str = "claude-opus-4-7",
    inventory_names: Optional[List[str]] = None,
    inventory_names_with_units: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Extract sales invoice data using Claude vision."""
    logger.info(f"Extracting sales invoice via Claude ({claude_model})")
    try:
        from applicationca.agents.document_specific_functions.claude_extractor import call_claude_async

        file_name = os.path.splitext(os.path.basename(file_path))[0].lower() if file_path else ""

        prompt = f"""You are an expert accounting assistant specialized in Indian GST invoices and Tally ERP voucher creation.

Your task is NOT just to extract text, but to UNDERSTAND the document like an accountant and produce structured, Tally-ready data.

File Name: {file_name}

════════════════════════════════════════════════════════════════════════════════
STEP 1: UNDERSTAND THE DOCUMENT SEMANTICS
════════════════════════════════════════════════════════════════════════════════
First, identify:
- Who ISSUED the invoice (Seller/Supplier) — usually at the top with their logo/company name
- Who RECEIVED the invoice (Buyer/Customer) — usually in "Bill To", "Ship To", "Buyer Details"
- Nature of transaction: Is this a SALES invoice (you are the seller) or PURCHASE invoice (you are the buyer)?

**CRITICAL RULE — PARTY IDENTIFICATION:**
- The PARTY (ledger) is ALWAYS the COUNTERPARTY:
  - In SALES invoice → Party = Customer/Buyer (the one being billed)
  - In PURCHASE invoice → Party = Vendor/Supplier (the one who issued the bill)
- **DO NOT confuse the seller's details with the buyer's details**
- The seller's GSTIN is usually prominent at top; the buyer's GSTIN is in billing section

════════════════════════════════════════════════════════════════════════════════
STEP 2: EXTRACT PARTY DETAILS (MOST IMPORTANT)
════════════════════════════════════════════════════════════════════════════════
For the PARTY (counterparty), extract:

1. **party_name**: Clean name without prefixes like "M/s" unless part of legal name
   - Look in: "Bill To", "Ship To", "Buyer", "Customer", "Consignee", "Party Name"
   - Also provide a **normalized_name**: lowercase, no special characters, for matching

2. **gst_number**: The PARTY's 15-character GSTIN
   - **CRITICAL**: This must be the BUYER's GSTIN for sales, VENDOR's GSTIN for purchase
   - DO NOT extract the invoice issuer's own GSTIN here
   - Validate format: 2 digits (state) + 5 letters (PAN) + 4 digits + 1 letter + 1 alphanumeric + Z + 1 alphanumeric

3. **place_of_supply**: Full STATE NAME (e.g., "Maharashtra" not "MH")
   - Derive from party's address or first 2 digits of their GSTIN:
     01=Jammu & Kashmir, 02=Himachal Pradesh, 03=Punjab, 04=Chandigarh, 05=Uttarakhand,
     06=Haryana, 07=Delhi, 08=Rajasthan, 09=Uttar Pradesh, 10=Bihar, 11=Sikkim,
     12=Arunachal Pradesh, 13=Nagaland, 14=Manipur, 15=Mizoram, 16=Tripura, 17=Meghalaya,
     18=Assam, 19=West Bengal, 20=Jharkhand, 21=Odisha, 22=Chhattisgarh, 23=Madhya Pradesh,
     24=Gujarat, 27=Maharashtra, 29=Karnataka, 32=Kerala, 33=Tamil Nadu, 36=Telangana, 37=Andhra Pradesh

4. **dates**: All dates in DD/MM/YYYY format (Invoice Date, Due Date, etc.)
5. **invoice_number**: Look for "Invoice No.", "Bill No.", "Voucher No.", "Document No."

════════════════════════════════════════════════════════════════════════════════
STEP 3: EXTRACT LINE ITEMS WITH CALCULATION VALIDATION
════════════════════════════════════════════════════════════════════════════════
For EACH line item, you MUST correctly identify these THREE DIFFERENT values:

| Column Type | What It Means | Example |
|------------|---------------|---------|
| QUANTITY | Number of units | 10 |
| RATE | Price per ONE unit | 500 |
| AMOUNT | Total = Qty × Rate | 5000 |

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

**VALIDATION RULE**: For each item, verify: Quantity × Rate ≈ Amount
- If Amount = 5000, Qty = 10, Rate = 500 → CORRECT (10 × 500 = 5000)
- If Amount = 5000, Qty = 5000, Rate = 5000 → WRONG — re-examine columns!
- If validation fails, SWAP Qty and Rate and check again before adding to warnings

**CROSS-VALIDATE TOTALS**: After extracting all items, verify:
  sum(all item amounts) ≈ document subtotal (before tax). If they differ by more than 5%,
  you are likely reading the wrong column for Amount — re-check and correct.

Extract for each item:
1. **item_name**: Exact name as shown, preserve formatting
2. **quantity**: NUMBER OF UNITS (not amount!)
3. **rate**: PRICE PER SINGLE UNIT (not total!)
4. **amount**: Calculated or verified amount (Qty × Rate)
5. **hsn_code**: HSN/SAC code if shown
6. **gst_rate**: GST percentage for this item (e.g., 18, 12, 5)
7. **batch_number**: If available
8. **discount_amount**: Discount on this line in RUPEES (₹) — populate ONLY if
   the invoice shows the discount as an absolute amount (e.g. "Disc: ₹100" or
   "Less Discount 250"). Otherwise leave 0.
9. **discount_percentage**: Discount on this line as a PERCENT (plain number,
   5.5 not "5.5%") — populate ONLY if the invoice shows the discount as a
   percentage (e.g. "Disc: 5%", "Disc 10.5%"). Otherwise leave 0.

CRITICAL: do NOT convert between the two formats yourself. If the invoice
shows ₹100, set discount_amount=100 and discount_percentage=0. If it shows
5%, set discount_percentage=5 and discount_amount=0. The Python code will
derive whichever value is missing.

════════════════════════════════════════════════════════════════════════════════
STEP 4: EXTRACT TAX & CHARGE ENTRIES
════════════════════════════════════════════════════════════════════════════════
Read the tax breakup section of the invoice and extract EVERY tax/charge line shown:
- Extract EACH line as a SEPARATE entry with: ledger_name, amount, percentage, ledger_type
- Common entries: CGST, SGST, IGST, Cess, TCS, Freight, Packing, Insurance, Round Off, Discount
- Include entries even if their amount is 0 (if shown on the invoice)
- DO NOT invent entries that are not on the invoice
- DO NOT merge multiple tax lines into one

════════════════════════════════════════════════════════════════════════════════
STEP 5: CALCULATE TOTALS AND VALIDATE
════════════════════════════════════════════════════════════════════════════════
Calculate and verify:
- **subtotal** = Sum of all item amounts (before tax)
- **total_tax** = CGST + SGST or IGST
- **grand_total** = subtotal + total_tax + additional charges

If calculated totals don't match document totals (within 1% tolerance):
- Add warning to warnings array
- Use document totals but note discrepancy

════════════════════════════════════════════════════════════════════════════════
STEP 6: PREPARE TALLY MAPPING
════════════════════════════════════════════════════════════════════════════════
Determine Tally voucher structure:
- **transaction_type**: "sales" or "purchase"
- **voucher_type**: "Sales" or "Purchase"
- **party_ledger**: The counterparty name (for both debit/credit based on type)
- For Sales: Debit = Party (customer), Credit = Sales
- For Purchase: Debit = Purchase, Credit = Party (vendor)

════════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (STRICT JSON)
════════════════════════════════════════════════════════════════════════════════
Return this EXACT JSON structure:
{{
    "transaction_type": "sales",
    "party_details": {{
        "party_name": "Customer/Vendor name (clean, no M/s prefix unless legal name)",
        "normalized_name": "lowercase name without special chars for matching",
        "dates": ["DD/MM/YYYY"],
        "place_of_supply": "Full State Name",
        "gst_number": "15-character GSTIN of the PARTY (not issuer)",
        "invoice_number": "Invoice/Bill number",
        "address": "Party's address if available"
    }},
    "inventory_items": [
        {{
            "item_name": "exact item name",
            "quantity": 10,
            "rate": 500,
            "amount": 5000,
            "hsn_code": "HSN code if available",
            "gst_rate": 18,
            "batch_number": "",
            "discount_amount": 0,
            "discount_percentage": 0
        }}
    ],
    "ledger_entries": [
        {{
            "ledger_name": "CGST",
            "ledger_type": "tax",
            "amount": 450,
            "percentage": 9
        }},
        {{
            "ledger_name": "SGST",
            "ledger_type": "tax",
            "amount": 450,
            "percentage": 9
        }}
    ],
    "totals": {{
        "subtotal": 5000,
        "cgst": 450,
        "sgst": 450,
        "igst": 0,
        "other_charges": 0,
        "grand_total": 5900
    }},
    "tally_mapping": {{
        "voucher_type": "Sales",
        "party_ledger": "Customer Name",
        "sales_ledger": "Sales",
        "tax_ledgers": ["CGST", "SGST"]
    }},
    "confidence": {{
        "party_name": 0.95,
        "voucher_date": 0.95,
        "invoice_number": 0.95,
        "gst_number": 0.90,
        "items": 0.85,
        "totals": 0.90
    }},
    "warnings": []
}}

════════════════════════════════════════════════════════════════════════════════
CRITICAL RULES
════════════════════════════════════════════════════════════════════════════════
1. NO null values — use "" for text, 0 for numbers
2. QUANTITY ≠ RATE ≠ AMOUNT — these are THREE DIFFERENT values
3. Party GSTIN is the COUNTERPARTY's GSTIN, NOT the invoice issuer's
4. Always validate: Quantity × Rate ≈ Amount
5. Add warnings for: calculation mismatches, ambiguous party name, GSTIN format issues
6. Preserve exact item names with all formatting
7. Use DD/MM/YYYY for all dates
8. Remove commas and currency symbols from numbers
9. Full state names, not abbreviations
10. Return ONLY valid JSON, no markdown"""

        if extracted_text:
            prompt += f"\n\nDocument text content:\n{extracted_text[:8000]}"

        raw = await call_claude_async(
            file_path=file_path,
            prompt=prompt,
            api_key=anthropic_api_key,
            model=claude_model,
            system_prompt="You are an expert Indian GST invoice data extractor for Tally ERP. Extract party details, line items with quantity/rate/amount validation, and tax breakup. Return only valid JSON.",
            max_tokens=8192,
        )

        content = raw.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        extracted_data = json.loads(content)

        # Inventory item matching (same as Gemini path)
        if inventory_names and extracted_data.get("inventory_items"):
            logger.info("Starting batch inventory item matching (Claude)")
            llm = ChatOpenAI(
                model="gpt-4.1-nano",
                temperature=0.1,
                openai_api_key=os.getenv("OPENAI_API_KEY")
            )
            matched_names = await match_inventory_items_batch(
                llm,
                extracted_data["inventory_items"],
                inventory_names
            )
            for item, matched_name in zip(extracted_data["inventory_items"], matched_names):
                original_name = item["item_name"]
                item["item_name"] = matched_name
                if inventory_names_with_units:
                    for inv_item in inventory_names_with_units:
                        if inv_item["name"] == matched_name:
                            item["unit"] = inv_item.get("unit", "Nos")
                            break
                    else:
                        item["unit"] = "Nos"
                else:
                    item["unit"] = "Nos"
                if original_name != matched_name:
                    logger.info(f"Matched inventory item: '{original_name}' -> '{matched_name}'")

        # Same post-processing as Gemini path
        transaction_type = extracted_data.get("transaction_type", "sales")
        party_details = extracted_data.get("party_details", {})
        if not isinstance(party_details, dict):
            party_details = {}
        party_details.setdefault("party_name", "")
        party_details.setdefault("normalized_name", party_details.get("party_name", "").lower().replace(" ", "").replace(".", "").replace(",", ""))
        party_details.setdefault("dates", [])
        party_details.setdefault("place_of_supply", "")
        party_details.setdefault("gst_number", "")
        party_details.setdefault("invoice_number", "")
        party_details.setdefault("address", "")

        inventory_items = extracted_data.get("inventory_items", [])
        if not isinstance(inventory_items, list):
            inventory_items = []

        processed_items = []
        warnings = extracted_data.get("warnings", [])

        # Fix qty/rate swaps and cross-validate against subtotal
        _subtotal = extracted_data.get("totals", {}).get("subtotal", 0)
        inventory_items, _fix_warnings = fix_line_items(inventory_items, subtotal=_subtotal)
        warnings.extend(_fix_warnings)

        for idx, item in enumerate(inventory_items, 1):
            if isinstance(item, dict):
                original_item_name = str(item.get("item_name", ""))
                quantity = float(item.get("quantity", 0))
                rate = float(item.get("rate", 0))
                calculated_amount = quantity * rate
                extracted_amount = float(item.get("amount", 0))
                if extracted_amount > 0 and abs(calculated_amount - extracted_amount) / max(extracted_amount, 1) < 0.05:
                    final_amount = extracted_amount
                elif calculated_amount > 0:
                    final_amount = calculated_amount
                    if extracted_amount > 0 and abs(calculated_amount - extracted_amount) > 1:
                        warnings.append(f"Item {idx}: Amount corrected from {extracted_amount} to {calculated_amount}")
                else:
                    final_amount = extracted_amount
                processed_items.append({
                    "item_name": original_item_name,
                    "quantity": quantity,
                    "rate": rate,
                    "amount": final_amount,
                    "hsn_code": str(item.get("hsn_code", "")),
                    "gst_rate": float(item.get("gst_rate", 0)),
                    "batch_number": str(item.get("batch_number", "")),
                    "discount_percentage": float(item.get("discount_percentage", 0)),
                    "unit": item.get("unit", "Nos"),
                })

        ledger_entries = extracted_data.get("ledger_entries", [])
        if not isinstance(ledger_entries, list):
            ledger_entries = []
        processed_ledger_entries = []
        for entry in ledger_entries:
            if isinstance(entry, dict):
                processed_ledger_entries.append({
                    "ledger_name": str(entry.get("ledger_name", "")),
                    "ledger_type": str(entry.get("ledger_type", "")),
                    "amount": float(entry.get("amount", 0)),
                    "percentage": float(entry.get("percentage", 0)),
                })

        raw_confidence = extracted_data.get("confidence", {})
        confidence = {
            "party_name": float(raw_confidence.get("party_name", 0.85 if party_details.get("party_name") else 0.3)),
            "voucher_date": float(raw_confidence.get("voucher_date", 0.85 if party_details.get("dates") else 0.3)),
            "invoice_number": float(raw_confidence.get("invoice_number", 0.85 if party_details.get("invoice_number") else 0.3)),
            "gst_number": float(raw_confidence.get("gst_number", 0.85 if party_details.get("gst_number") else 0.3)),
            "items": float(raw_confidence.get("items", 0.85 if processed_items else 0.3)),
            "totals": float(raw_confidence.get("totals", 0.85)),
        }

        raw_totals = extracted_data.get("totals", {})
        totals = {
            "subtotal": float(raw_totals.get("subtotal", sum(item.get("amount", 0) for item in processed_items))),
            "cgst": float(raw_totals.get("cgst", 0)),
            "sgst": float(raw_totals.get("sgst", 0)),
            "igst": float(raw_totals.get("igst", 0)),
            "other_charges": float(raw_totals.get("other_charges", 0)),
            "grand_total": float(raw_totals.get("grand_total", 0)),
        }
        if totals["grand_total"] == 0:
            totals["grand_total"] = totals["subtotal"] + totals["cgst"] + totals["sgst"] + totals["igst"] + totals["other_charges"]

        raw_tally_mapping = extracted_data.get("tally_mapping", {})
        tally_mapping = {
            "voucher_type": raw_tally_mapping.get("voucher_type", "Sales" if transaction_type == "sales" else "Purchase"),
            "party_ledger": raw_tally_mapping.get("party_ledger", party_details.get("party_name", "")),
            "sales_ledger": raw_tally_mapping.get("sales_ledger", "Sales"),
            "purchase_ledger": raw_tally_mapping.get("purchase_ledger", "Purchase"),
            "tax_ledgers": raw_tally_mapping.get("tax_ledgers", []),
        }

        processed_data = {
            "transaction_type": transaction_type,
            "party_details": party_details,
            "inventory_items": processed_items,
            "ledger_entries": processed_ledger_entries,
            "totals": totals,
            "tally_mapping": tally_mapping,
            "confidence": confidence,
            "warnings": warnings,
        }

        logger.info(f"Claude sales extraction successful: {len(processed_items)} items, {len(processed_ledger_entries)} ledger entries")
        return [processed_data]

    except Exception as e:
        logger.error(f"Claude sales extraction error: {e}")
        import traceback; traceback.print_exc()
        return []


async def match_sales_transaction_ledgers(
    transactions: List[Dict[str, Any]],
    available_ledgers: List[Dict[str, str]],
    openai_api_key: str,
    openai_model: str,
    user_id: Optional[str] = None,
    company_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Match appropriate ledgers for sales transactions using the unified matcher."""
    if not transactions:
        logger.warning("No transactions to match ledgers for")
        return []

    start_time = __import__('time').time()
    logger.info(f"Starting ledger matching for {len(transactions)} sales transactions")
    print(f"\nMatching ledgers for {len(transactions)} sales transactions...")

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
            # Match party name
            party_details = tx.get('party_details', {})
            party_name = party_details.get('party_name', '').strip()
            logger.info(f"Sales ledger matching — party_name='{party_name}', "
                        f"debit_ledger='{tx.get('debit_ledger', '')}', "
                        f"ledger_entries={[e.get('ledger_name') for e in tx.get('ledger_entries', [])]}")
            if party_name:
                tasks.append(matcher.match_single(party_name, available_names, "party"))

            # Match debit ledger if different from party_name
            debit_ledger = tx.get('debit_ledger', '').strip()
            if debit_ledger and debit_ledger != party_name:
                tasks.append(matcher.match_single(debit_ledger, available_names, "party"))

            # Match tax/charge ledger entries (fuzzy only — no LLM needed)
            for entry in tx.get('ledger_entries', []):
                ledger_name = entry.get('ledger_name', '').strip()
                if ledger_name:
                    ltype = entry.get('ledger_type', 'tax')
                    tasks.append(matcher.match_single(ledger_name, available_names, ltype))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Known default names that indicate "no match found" — do NOT
        # overwrite the original extracted name with these.
        _DEFAULTS = {"Cash", "Purchase", "Sales"}

        result_index = 0
        for tx in transactions:
            party_details = tx.get('party_details', {})
            original_party = party_details.get('party_name', '')
            if original_party:
                if result_index < len(results) and not isinstance(results[result_index], Exception):
                    matched = results[result_index]
                    # Only overwrite if a real ledger was matched, not a default
                    if matched not in _DEFAULTS:
                        party_details['party_name'] = matched
                    # else: keep the original extracted name
                else:
                    pass  # keep original extracted name on error
                result_index += 1

            debit_ledger = tx.get('debit_ledger', '').strip()
            if debit_ledger and debit_ledger != original_party:
                if result_index < len(results) and not isinstance(results[result_index], Exception):
                    matched = results[result_index]
                    if matched not in _DEFAULTS:
                        tx['debit_ledger'] = matched
                result_index += 1
            else:
                tx['debit_ledger'] = party_details.get('party_name', original_party)

            for entry in tx.get('ledger_entries', []):
                if entry.get('ledger_name'):
                    original_tax_name = entry['ledger_name']
                    if result_index < len(results) and not isinstance(results[result_index], Exception):
                        matched = results[result_index]
                        # For taxes, only overwrite if a real match was found
                        # (not a default). Preserve the original extracted name
                        # so SGST stays SGST, IGST stays IGST, etc.
                        if matched not in _DEFAULTS:
                            entry['ledger_name'] = matched
                        # else: keep original extracted name
                    # else: keep original on error
                    result_index += 1

        logger.info(f"Successfully matched ledgers for {len(transactions)} sales transactions")
        print(f"Successfully matched ledgers for {len(transactions)} sales transactions")
        return transactions

    except Exception as e:
        logger.error(f"Error in sales ledger matching: {str(e)}")
        # On error, keep original extracted names — don't overwrite with defaults
        return transactions
    finally:
        elapsed_time = __import__('time').time() - start_time
        logger.info(f"Sales ledger matching completed in {elapsed_time:.2f} seconds")

def generate_sales_xml(transactions: List[Dict[str, Any]], company_name: str) -> str:
    """Generate Tally XML for sales transactions with inventory items."""
    if not transactions or not transactions[0]:
        logger.warning("No data to generate XML for")
        return ""
    
    start_time = __import__('time').time()
    logger.info("Generating Tally XML for sales data")
    print("\nGenerating Tally XML for sales data...")
    
    try:
        # Get the first transaction (our new format has all data in one object)
        data = transactions[0]
        party_details = data.get('party_details', {})
        inventory_items = data.get('inventory_items', [])
        ledger_entries = data.get('ledger_entries', [])
        
        # Calculate total amount from inventory items
        total_inventory_amount = sum(
            float(item.get('quantity', 0)) * float(item.get('rate', 0))
            for item in inventory_items
        )
        
        # Format date - use first date from party_details if available or fallback to today
        dates = party_details.get('dates', [])
        date_str = ""
        if dates and isinstance(dates, list) and len(dates) > 0:
            # Try to parse the first date in DD/MM/YYYY format
            try:
                date_parts = dates[0].split('/')
                if len(date_parts) == 3:
                    date_str = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"  # Convert to YYYY-MM-DD
            except Exception:
                pass

        # Fallback to current date if no valid date found
        if not date_str:
            from datetime import datetime
            date_str = datetime.now().strftime("%Y-%m-%d")
        
        # Escape text values for XML
        esc_company = escape_xml_entities(company_name)
        esc_party = escape_xml_entities(party_details.get('party_name', ''))
        esc_invoice = escape_xml_entities(party_details.get('invoice_number', ''))
        esc_gst = escape_xml_entities(party_details.get('gst_number', ''))
        esc_place = escape_xml_entities(party_details.get('place_of_supply', ''))

        # Generate sales voucher XML
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
   <REQUESTDATA>
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
     <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
      <DATE>{date_str}</DATE>
      <PARTYNAME>{esc_party}</PARTYNAME>
      <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
      <VOUCHERNUMBER>{esc_invoice}</VOUCHERNUMBER>
      <PARTYLEDGERNAME>{esc_party}</PARTYLEDGERNAME>
      <EFFECTIVEDATE>{date_str}</EFFECTIVEDATE>
      <ISINVOICE>Yes</ISINVOICE>
      <HASDISCOUNTS>Yes</HASDISCOUNTS>"""
        
        # Add party ledger entry
        total_amount = 0
        for item in inventory_items:
            quantity = float(item.get('quantity', 0))
            rate = float(item.get('rate', 0))
            amount = quantity * rate
            total_amount += amount
            
        # Add ledger entries for GST and other charges
        for entry in ledger_entries:
            amount = float(entry.get('amount', 0))
            if amount > 0:
                total_amount += amount
                
        # Add GST details if available
        if esc_gst:
            xml += f"""
      <PARTYGSTIN>{esc_gst}</PARTYGSTIN>"""
        if esc_place:
            xml += f"""
      <PLACEOFSUPPLY>{esc_place}</PLACEOFSUPPLY>"""

        # Party ledger entry (negative amount as it's debit)
        xml += f"""
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>{esc_party}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <LEDGERFROMITEM>No</LEDGERFROMITEM>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
       <AMOUNT>-{total_amount:.2f}</AMOUNT>
      </LEDGERENTRIES.LIST>"""
        
        # Add ledger entries for additional costs/taxes
        for entry in ledger_entries:
            ledger_name = escape_xml_entities(entry.get('ledger_name', ''))
            amount = float(entry.get('amount', 0))

            if amount > 0:
                xml += f"""
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>{ledger_name}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <LEDGERFROMITEM>No</LEDGERFROMITEM>
       <ISPARTYLEDGER>No</ISPARTYLEDGER>
       <AMOUNT>{amount:.2f}</AMOUNT>
      </LEDGERENTRIES.LIST>"""
        
        # Add inventory entries
        for item in inventory_items:
            item_name = item.get('item_name', '')
            quantity = float(item.get('quantity', 0))
            rate = float(item.get('rate', 0))
            amount = quantity * rate
            discount_percentage = float(item.get('discount_percentage', 0))
            batch_number = item.get('batch_number', 'Primary Batch')
            unit = item.get('unit', 'Nos')  # Get unit from item or default to 'Nos'
            
            # If empty batch number, use Primary Batch
            if not batch_number:
                batch_number = "Primary Batch"
            
            # Escape special characters for XML
            escaped_item_name = escape_xml_entities(item_name)
            escaped_batch = escape_xml_entities(batch_number)

            xml += f"""
      <ALLINVENTORYENTRIES.LIST>
       <STOCKITEMNAME>{escaped_item_name}</STOCKITEMNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <RATE>{rate:.2f}/{unit}</RATE>"""

            # Add discount if applicable
            if discount_percentage > 0:
                xml += f"""
       <DISCOUNT>{discount_percentage}</DISCOUNT>"""

            xml += f"""
       <AMOUNT>{amount:.2f}</AMOUNT>
       <ACTUALQTY>{quantity} {unit}</ACTUALQTY>
       <BILLEDQTY>{quantity} {unit}</BILLEDQTY>
       <BATCHALLOCATIONS.LIST>
        <GODOWNNAME>Main Location</GODOWNNAME>
        <BATCHNAME>{escaped_batch}</BATCHNAME>
        <AMOUNT>{amount:.2f}</AMOUNT>
        <ACTUALQTY>{quantity} {unit}</ACTUALQTY>
        <BILLEDQTY>{quantity} {unit}</BILLEDQTY>
       </BATCHALLOCATIONS.LIST>
       <ACCOUNTINGALLOCATIONS.LIST>
        <LEDGERNAME>Sales</LEDGERNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <AMOUNT>{amount:.2f}</AMOUNT>
       </ACCOUNTINGALLOCATIONS.LIST>
      </ALLINVENTORYENTRIES.LIST>"""
            
        # Close XML structure
        xml += """
     </VOUCHER>
    </TALLYMESSAGE>
   </REQUESTDATA>
  </IMPORTDATA>
 </BODY>
</ENVELOPE>"""
        
        logger.info("Successfully generated Tally XML")
        print("Successfully generated Tally XML")
        print(xml)
        return xml
        
    except Exception as e:
        logger.error(f"Error generating sales XML: {str(e)}")
        return ""
    finally:
        elapsed_time = __import__('time').time() - start_time
        logger.info(f"Sales XML generation completed in {elapsed_time:.2f} seconds")


async def _recover_party_name(
    client,
    extracted_text: str,
    content_parts: list,
    image_attached: bool,
) -> Optional[str]:
    """Focused LLM call to recover party name when main extraction missed it.

    Uses a short, targeted prompt that's harder for the LLM to skip.
    """
    try:
        parts = []
        # Include image if available
        if image_attached and content_parts:
            for p in content_parts:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    parts.append(p)
                    break

        prompt = (
            "Look at this invoice carefully. I need ONLY the party/company name — "
            "the customer or vendor (NOT the invoice issuer's own company name).\n\n"
            "Look for names in:\n"
            "- 'Bill To', 'Ship To', 'Buyer', 'Customer', 'Consignee' sections\n"
            "- For purchase invoices: the supplier/vendor name at the top\n\n"
            "Return ONLY the company/party name, nothing else. "
            "If you cannot find it, return exactly: UNKNOWN"
        )
        if extracted_text:
            prompt += f"\n\nDocument text:\n{extracted_text[:4000]}"

        parts.append({"type": "text", "text": prompt})

        model = "gpt-4.1" if image_attached else "gpt-4.1-mini"

        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Extract the party name from invoices. Return only the name."},
                {"role": "user", "content": parts},
            ],
            temperature=0,
            max_tokens=100,
        )
        name = resp.choices[0].message.content.strip()
        # Reject obvious non-answers
        if name and name.upper() not in ("UNKNOWN", "N/A", "NONE", "NOT FOUND", ""):
            return name
        return None
    except Exception as e:
        logger.warning("Party name recovery failed: %s", e)
        return None


async def _extract_with_openai(
    file_path: str,
    extracted_text: str,
    openai_api_key: str,
    openai_model: str,
    inventory_names: Optional[List[str]] = None,
    voucher_type: str = "auto",
) -> List[Dict[str, Any]]:
    """Extract invoice data using OpenAI GPT-4o vision."""
    logger.info("Attempting sales invoice extraction via OpenAI (fallback)")

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=openai_api_key)

        # Build message content
        content_parts = []

        # Try to attach the PDF as an image (convert first page)
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

        # Build context-aware party identification instructions.
        # We ask the LLM to extract BOTH GSTINs — the code picks the right one.
        party_instruction = """GSTIN EXTRACTION (CRITICAL — extract BOTH, not just one):
Indian invoices have TWO GSTINs. You MUST extract both separately:

1. SELLER/ISSUER GSTIN — found near the company name/logo at the TOP of the invoice.
   Look for labels: "GSTIN", "GST No.", "Tax Reg. No.", "TIN"
   Location: header area, near the seller's address and state

2. BUYER/CUSTOMER GSTIN — found in "Bill To" / "Ship To" / "Buyer Details" section.
   Also sometimes in the tax details section labeled:
   "Customer GSTIN", "Buyer's GSTIN", "Recipient GSTIN", "Consignee GSTIN"
   WARNING: this GSTIN often appears NEAR the tax amounts (CGST/SGST) — do NOT
   confuse it with the seller's GSTIN.

Return BOTH as "seller_gstin" and "buyer_gstin" in your response.
The code will pick the correct one based on the transaction type."""

        if voucher_type == "purchase":
            party_instruction += """

PARTY IDENTIFICATION:
This is a PURCHASE invoice. The PARTY is the SELLER/VENDOR.
- party_name = the SELLER's company name (at the TOP of the invoice, with logo)
- The BUYER in "Bill To" section is NOT the party."""
        elif voucher_type == "sales":
            party_instruction += """

PARTY IDENTIFICATION:
This is a SALES invoice. The PARTY is the BUYER/CUSTOMER.
- party_name = the BUYER's company name (from "Bill To" / "Ship To" section)
- The SELLER at the TOP is NOT the party."""
        else:
            party_instruction += """

PARTY IDENTIFICATION:
Determine if this is Sales or Purchase, then:
- Purchase → Party = SELLER at the top
- Sales → Party = BUYER in "Bill To" section"""

        prompt = f"""You are an expert invoice data extractor for Indian GST invoices. Carefully analyze this invoice and extract every detail with high precision.

{party_instruction}

EXTRACTION INSTRUCTIONS:

1. PARTY DETAILS:
   - party_name: The PARTY's company name (see identification rules above)
   - gst_number: The PARTY's 15-character GSTIN (see identification rules above)
   - invoice_number: Invoice/Bill number
   - dates: Invoice date in DD/MM/YYYY format
   - place_of_supply: Full state name derived from PARTY's GSTIN or address
     (01=J&K, 02=HP, 03=Punjab, 06=Haryana, 07=Delhi, 08=Rajasthan, 09=UP,
      24=Gujarat, 27=Maharashtra, 29=Karnataka, 33=TN, 36=Telangana)

2. LINE ITEMS (read columns carefully — do NOT swap Quantity and Rate):
   For each line item, identify THREE separate values:
   - QUANTITY: Number of units purchased/sold (e.g., 2, 5, 10, 100)
   - RATE: Price per ONE unit (e.g., 500.00, 1250.75, 10820.00)
   - AMOUNT: Total for this line = Quantity × Rate

   LOGICAL RULES to tell Quantity from Rate:
   - Quantity is usually a SMALL WHOLE number (1, 2, 5, 10, 50, 100)
   - Rate is usually a LARGER number, often with DECIMALS (500.00, 1250.75)
   - If you see two numbers and one is much larger → the larger one is the Rate
   - If Qty=10820 and Rate=1 → that's WRONG (swapped). Should be Qty=1 and Rate=10820
   - ALWAYS verify: Quantity × Rate ≈ Amount. If not, SWAP them and check again.
   - Quantity can never be negative. Rate can never be negative.
   - For services (travel, consulting, rent), Quantity is typically 1

   Remove commas from numbers (e.g., "1,250.75" → 1250.75)

3. TAX & CHARGE ENTRIES (extract EVERY tax/charge line shown on the invoice):
   - Read the tax breakup section of the invoice carefully
   - Extract EACH tax/charge as a SEPARATE entry exactly as written on the invoice
   - Common entries you might find: CGST, SGST, IGST, Cess, TCS, Freight, Packing, Insurance, Round Off, Discount
   - Even if amount is 0, include it if the line is shown on the invoice
   - Each entry MUST have: ledger_name (exact name from invoice), amount, and percentage
   - If the invoice shows "CGST @ 9% = 450" and "SGST @ 9% = 450", extract BOTH as separate entries
   - If the invoice shows "IGST @ 18% = 900", extract as one IGST entry
   - DO NOT invent tax entries that are not shown on the invoice
   - DO NOT merge multiple tax lines into one

4. VERIFICATION: Sum(item amounts) ≈ Subtotal, Subtotal + Taxes ≈ Grand Total.

Return this exact JSON structure:
{{
    "party_details": {{
        "party_name": "party company name (see rules above)",
        "dates": ["DD/MM/YYYY"],
        "place_of_supply": "full state name",
        "seller_gstin": "15-char GSTIN of the SELLER/ISSUER (from top of invoice)",
        "buyer_gstin": "15-char GSTIN of the BUYER/CUSTOMER (from Bill To or tax section)",
        "gst_number": "the PARTY's GSTIN (seller_gstin for purchase, buyer_gstin for sales)",
        "invoice_number": "invoice number"
    }},
    "inventory_items": [
        {{
            "item_name": "product name exactly as shown",
            "quantity": number_of_units,
            "rate": price_per_one_unit,
            "batch_number": "",
            "discount_percentage": 0
        }}
    ],
    "ledger_entries": [
        {{
            "ledger_name": "CGST",
            "ledger_type": "tax",
            "amount": 450,
            "percentage": 9
        }},
        {{
            "ledger_name": "SGST",
            "ledger_type": "tax",
            "amount": 450,
            "percentage": 9
        }}
    ],
    "confidence": {{
        "party_name": 0.0-1.0,
        "voucher_date": 0.0-1.0,
        "invoice_number": 0.0-1.0,
        "gst_number": 0.0-1.0,
        "items": 0.0-1.0
    }}
}}

RULES:
- Use 0 for missing numbers, "" for missing text
- Extract ALL line items with correct Quantity and Rate
- Separate tax entries (CGST, SGST, IGST) from item costs
- Dates in DD/MM/YYYY format
- Return ONLY valid JSON, no markdown"""

        if extracted_text:
            prompt += f"\n\nDocument text content:\n{extracted_text[:8000]}"

        if inventory_names:
            prompt += f"\n\nAvailable inventory items for matching:\n{json.dumps(inventory_names[:100])}"

        content_parts.append({"type": "text", "text": prompt})

        model_to_use = "gpt-4.1" if image_attached else openai_model

        response = await client.chat.completions.create(
            model=model_to_use,
            messages=[
                {"role": "system", "content": "You are an expert invoice data extractor. Return only valid JSON."},
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

        # Process into the same format as Gemini extraction
        party_details = extracted_data.get("party_details", {})
        party_details.setdefault("party_name", "")
        party_details.setdefault("dates", [])
        party_details.setdefault("place_of_supply", "")
        party_details.setdefault("gst_number", "")
        party_details.setdefault("invoice_number", "")

        # ── GSTIN selection: pick the correct GSTIN based on voucher type ──
        # The LLM extracts both seller_gstin and buyer_gstin.
        # The code picks the right one — more reliable than asking the LLM to choose.
        seller_gstin = (party_details.get("seller_gstin") or "").strip()
        buyer_gstin = (party_details.get("buyer_gstin") or "").strip()
        existing_gstin = (party_details.get("gst_number") or "").strip()

        if seller_gstin or buyer_gstin:
            if voucher_type == "purchase":
                # For purchase: party = seller → use seller's GSTIN
                chosen = seller_gstin or existing_gstin
            elif voucher_type == "sales":
                # For sales: party = buyer → use buyer's GSTIN
                chosen = buyer_gstin or existing_gstin
            else:
                chosen = existing_gstin or seller_gstin or buyer_gstin

            if chosen:
                party_details["gst_number"] = chosen

            logger.info(
                "GSTIN selection: seller='%s', buyer='%s', voucher_type='%s' → chose='%s'",
                seller_gstin, buyer_gstin, voucher_type, party_details["gst_number"],
            )

        logger.info(
            "OpenAI extraction — party_name='%s', gst_number='%s', "
            "invoice_number='%s', dates=%s",
            party_details.get("party_name"),
            party_details.get("gst_number"),
            party_details.get("invoice_number"),
            party_details.get("dates"),
        )

        # ── Recovery: if party_name is empty, try a focused extraction ──
        if not party_details.get("party_name", "").strip():
            logger.warning("Party name is empty after extraction — attempting focused recovery")
            recovered_name = await _recover_party_name(client, extracted_text, content_parts, image_attached)
            if recovered_name:
                party_details["party_name"] = recovered_name
                logger.info("Recovered party name: '%s'", recovered_name)

        inventory_items = []
        for item in extracted_data.get("inventory_items", []):
            inventory_items.append({
                "item_name": str(item.get("item_name", "")),
                "quantity": float(item.get("quantity", 0)),
                "rate": float(item.get("rate", 0)),
                "batch_number": str(item.get("batch_number", "")),
                "discount_percentage": float(item.get("discount_percentage", 0)),
                "unit": item.get("unit", "Nos")
            })

        ledger_entries = []
        for entry in extracted_data.get("ledger_entries", []):
            name = str(entry.get("ledger_name", "")).strip()
            if not name:
                continue
            ledger_entries.append({
                "ledger_name": name,
                "ledger_type": str(entry.get("ledger_type", "tax")),
                "amount": float(entry.get("amount", 0)),
                "percentage": float(entry.get("percentage", 0)),
            })

        confidence = extracted_data.get("confidence", {})

        result = {
            "party_details": party_details,
            "inventory_items": inventory_items,
            "ledger_entries": ledger_entries,
            "confidence": {
                "party_name": float(confidence.get("party_name", 0.85 if party_details.get("party_name") else 0.3)),
                "voucher_date": float(confidence.get("voucher_date", 0.85 if party_details.get("dates") else 0.3)),
                "invoice_number": float(confidence.get("invoice_number", 0.85 if party_details.get("invoice_number") else 0.3)),
                "gst_number": float(confidence.get("gst_number", 0.85 if party_details.get("gst_number") else 0.3)),
                "items": float(confidence.get("items", 0.85 if inventory_items else 0.3)),
            }
        }

        logger.info(f"OpenAI extraction successful: {len(inventory_items)} items, {len(ledger_entries)} ledger entries")
        return [result]

    except Exception as e:
        logger.error(f"OpenAI extraction failed: {str(e)}")
        return []


async def extract_sales_invoice_structured(
    file_path: str,
    company: str,
    gemini_model: str,
    google_api_key: str,
    openai_api_key: str,
    openai_model: str,
    available_ledgers: List[Dict[str, str]],
    inventory_names: Optional[List[str]] = None,
    inventory_names_with_units: Optional[List[Dict[str, str]]] = None,
    voucher_type: str = "auto",
    anthropic_api_key: Optional[str] = None,
    extraction_provider: str = "openai",
) -> Dict[str, Any]:
    """
    Extract a sales/purchase invoice from a PDF and return structured data ready for the frontend form.
    Supports openai (default), claude, or gemini as extraction_provider.
    For openai/gemini: Tries OpenAI GPT-4o as primary, falls back to Gemini if OpenAI fails.

    Args:
        voucher_type: "purchase" for purchase invoices (party = seller/vendor),
                      "sales" for sales invoices (party = buyer/customer),
                      "auto" to let the LLM detect.
    """
    from utils.file_helper import extract_text_from_pdf, TABULAR_EXTENSIONS, IMAGE_EXTENSIONS

    logger.info(f"Starting structured sales invoice extraction for: {file_path}")

    # Extract text from file
    extracted_text = ""
    file_ext = os.path.splitext(file_path)[1].lower()

    # Excel/CSV: use structured tabular extraction — skip vision entirely
    if file_ext in TABULAR_EXTENSIONS:
        logger.info(f"Tabular file detected ({file_ext}), using structured extraction")
        try:
            from utils.tabular_extractor import parse_tabular_file
            from applicationca.agents.document_specific_functions.excel_invoice_extractor import extract_invoices_from_dataframe
            df = parse_tabular_file(file_path)
            return await extract_invoices_from_dataframe(
                df=df,
                voucher_type=voucher_type if voucher_type != "auto" else "sales",
                openai_api_key=openai_api_key,
                openai_model=openai_model,
                inventory_names=inventory_names,
            )
        except Exception as e:
            logger.error(f"Tabular extraction failed: {e}")
            return {
                "header": {"invoice_type": "Sales", "voucher_date": "", "invoice_number": "",
                           "party_name": "", "party_gstin": "", "place_of_supply": "", "confidence": {}},
                "items": [], "taxes": [],
                "summary": {"subtotal": 0, "tax_amount": 0, "total_amount": 0, "narration": ""},
            }

    if file_ext == '.pdf':
        try:
            with open(file_path, 'rb') as f:
                file_bytes = f.read()
            extracted_text = extract_text_from_pdf(file_bytes)
        except Exception as e:
            logger.warning(f"Text extraction failed, will rely on vision models: {e}")
    elif file_ext in IMAGE_EXTENSIONS:
        logger.info(f"Image file detected ({file_ext}), will rely on vision model")
        # extracted_text stays empty — _extract_with_openai handles images via vision

    # Step 1: Use configured provider for extraction
    transactions = None
    if extraction_provider == "claude":
        anthropic_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key:
            logger.info("Using Claude for structured sales invoice extraction")
            v_type = voucher_type if voucher_type != "auto" else "sales"
            raw_results = await _extract_sales_with_claude(
                file_path=file_path,
                extracted_text=extracted_text,
                anthropic_api_key=anthropic_key,
                claude_model=os.getenv("CLAUDE_EXTRACTION_MODEL", "claude-opus-4-7"),
                inventory_names=inventory_names,
                inventory_names_with_units=inventory_names_with_units,
            )
            if raw_results:
                transactions = raw_results
    elif openai_api_key:
        logger.info("Using OpenAI GPT-4o for sales invoice extraction")
        transactions = await _extract_with_openai(
            file_path=file_path,
            extracted_text=extracted_text,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            inventory_names=inventory_names,
            voucher_type=voucher_type,
        )

    if not transactions:
        logger.error("OpenAI extraction failed")
        return {
            "header": {
                "invoice_type": "Sales",
                "voucher_date": "",
                "invoice_number": "",
                "party_name": "",
                "party_gstin": "",
                "place_of_supply": "",
                "confidence": {}
            },
            "items": [],
            "taxes": [],
            "summary": {"subtotal": 0, "tax_amount": 0, "total_amount": 0, "narration": ""}
        }

    # Step 3: Match ledgers
    matched = await match_sales_transaction_ledgers(
        transactions=transactions,
        available_ledgers=available_ledgers,
        openai_api_key=openai_api_key,
        openai_model=openai_model
    )

    data = matched[0] if matched else transactions[0]
    party_details = data.get("party_details", {})
    inventory_items = data.get("inventory_items", [])
    ledger_entries = data.get("ledger_entries", [])
    confidence = data.get("confidence", {})

    # Convert date from DD/MM/YYYY to YYYYMMDD
    voucher_date = ""
    dates = party_details.get("dates", [])
    if dates and isinstance(dates, list) and len(dates) > 0:
        try:
            parts = dates[0].split("/")
            if len(parts) == 3:
                voucher_date = f"{parts[2]}{parts[1]}{parts[0]}"
        except Exception:
            pass

    # Detect GSTIN pattern
    gstin = party_details.get("gst_number", "")
    gstin_pattern = re.compile(r'[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[A-Z0-9]{1}[Z]{1}[A-Z0-9]{1}')
    if gstin and not gstin_pattern.match(gstin):
        logger.warning(f"Extracted GSTIN '{gstin}' does not match standard pattern")

    # Build items with auto-computed amounts
    structured_items = []
    subtotal = 0.0
    for idx, item in enumerate(inventory_items, 1):
        qty = float(item.get("quantity", 0))
        rate = float(item.get("rate", 0))
        amount = round(qty * rate, 2)
        subtotal += amount
        structured_items.append({
            "sr_no": idx,
            "item_name": item.get("item_name", ""),
            "quantity": qty,
            "rate": rate,
            "amount": amount,
            "unit": item.get("unit", "Nos"),
            "discount_percentage": float(item.get("discount_percentage", 0)),
            "batch_number": item.get("batch_number", "")
        })

    # Build taxes — pass through exactly what was extracted (including zero
    # amounts), then use the LedgerMatcher pipeline to suggest the closest
    # matching tax ledger from available Tally ledgers.
    structured_taxes = []
    tax_amount = 0.0

    # Collect available tax ledger names for matching
    tax_ledger_names = [
        l.get("name", "") for l in available_ledgers
        if (l.get("parent") or l.get("parent_group") or "").lower()
        in ("duties & taxes", "duties and taxes")
    ]

    # Match each extracted tax entry against available tax ledgers
    # using the same LedgerMatcher pipeline (fuzzy → LLM if needed)
    tax_suggestions = []
    if ledger_entries and tax_ledger_names:
        from applicationca.services.ledger_matcher import LedgerMatcher
        tax_matcher = LedgerMatcher(
            openai_api_key=openai_api_key,
            openai_model=openai_model,
        )
        for entry in ledger_entries:
            extracted_name = (entry.get("ledger_name") or "").strip()
            if not extracted_name:
                tax_suggestions.append(None)
                continue
            # Use match_party with tax ledger list — it runs fuzzy first,
            # then LLM only if fuzzy score < threshold
            ledger_dicts = [{"name": n} for n in tax_ledger_names]
            match_result = await tax_matcher.match_party(
                query_name=extracted_name,
                ledgers=ledger_dicts,
                document_type="purchase_invoice",
            )
            tax_suggestions.append(match_result)

    for idx, entry in enumerate(ledger_entries, 1):
        extracted_name = (entry.get("ledger_name") or "").strip()
        amt = float(entry.get("amount", 0))
        pct = float(entry.get("percentage", 0))
        tax_amount += amt

        # Get the suggestion for this tax entry
        suggestion = tax_suggestions[idx - 1] if idx - 1 < len(tax_suggestions) else None

        if suggestion and suggestion.recommended_ledger:
            matched_name = suggestion.recommended_ledger
            ai_sugg = {
                "recommended_ledger": suggestion.recommended_ledger,
                "confidence": suggestion.confidence,
                "match_type": suggestion.match_type,
                "reasoning": suggestion.reasoning,
                "alternatives": suggestion.alternatives,
                "create_new": suggestion.create_new,
                "suggested_new_name": suggestion.suggested_new_name,
            }
        else:
            matched_name = extracted_name
            ai_sugg = None

        structured_taxes.append({
            "sr_no": idx,
            "ledger_name": matched_name,
            "description": f"{extracted_name} @ {pct}%" if pct else extracted_name,
            "amount": amt,
            "ai_suggestion": ai_sugg,
        })

    logger.info("Tax entries: extracted=%d, matched=%s",
                len(ledger_entries),
                [(t["ledger_name"], t["amount"]) for t in structured_taxes])

    total_amount = round(subtotal + tax_amount, 2)

    # Update confidence with ledger match quality
    ledger_names_set = {l.get("name", "").lower() for l in available_ledgers}
    party_name = party_details.get("party_name", "")
    if party_name.lower() in ledger_names_set:
        confidence["party_name"] = max(confidence.get("party_name", 0), 0.95)

    result = {
        "header": {
            "invoice_type": "Sales",
            "voucher_date": voucher_date,
            "invoice_number": party_details.get("invoice_number", ""),
            "party_name": party_name,
            "party_gstin": gstin,
            "place_of_supply": party_details.get("place_of_supply", ""),
            "confidence": confidence
        },
        "items": structured_items,
        "taxes": structured_taxes,
        "summary": {
            "subtotal": round(subtotal, 2),
            "tax_amount": round(tax_amount, 2),
            "total_amount": total_amount,
            "narration": ""
        }
    }

    logger.info(f"Structured extraction complete: {len(structured_items)} items, {len(structured_taxes)} taxes, total={total_amount}")
    return result


def generate_sales_voucher_xml_from_form(
    header: Dict[str, Any],
    items: List[Dict[str, Any]],
    taxes: List[Dict[str, Any]],
    summary: Dict[str, Any],
    company: str,
    sales_ledger: str = "Sales",
    tds_ledger: str = "",
    tds_amount: float = 0.0,
) -> Dict[str, Any]:
    """
    Generate Tally Sales voucher XML from the frontend's edited form data.
    Returns { "xml": str, "validation_errors": list }.
    """
    validation_errors = []

    # Validate required fields
    party_name = header.get("party_name", "").strip()
    if not party_name:
        validation_errors.append("Party name is required")

    voucher_date = header.get("voucher_date", "").strip()
    if not voucher_date:
        validation_errors.append("Voucher date is required")

    if not items:
        validation_errors.append("At least one item is required")

    # Validate items
    for i, item in enumerate(items, 1):
        if not item.get("item_name", "").strip():
            validation_errors.append(f"Item {i}: Item name is required")
        if float(item.get("quantity", 0)) <= 0:
            validation_errors.append(f"Item {i}: Quantity must be greater than 0")
        if float(item.get("rate", 0)) <= 0:
            validation_errors.append(f"Item {i}: Rate must be greater than 0")

    # Validate amount balance
    items_total = sum(float(it.get("quantity", 0)) * float(it.get("rate", 0)) for it in items)
    taxes_total = sum(float(t.get("amount", 0)) for t in taxes)
    expected_total = round(items_total + taxes_total, 2)
    actual_total = float(summary.get("total_amount", 0))
    if abs(expected_total - actual_total) > 0.50:
        validation_errors.append(
            f"Amount mismatch: items({items_total:.2f}) + taxes({taxes_total:.2f}) = {expected_total:.2f}, but total is {actual_total:.2f}"
        )

    if validation_errors:
        return {"xml": "", "validation_errors": validation_errors}

    # Build the XML
    esc = escape_xml_entities
    esc_company = esc(company)
    esc_party = esc(party_name)
    esc_invoice = esc(header.get("invoice_number", ""))
    esc_gstin = esc(header.get("party_gstin", ""))
    esc_place = esc(header.get("place_of_supply", ""))
    esc_narration = esc(summary.get("narration", ""))
    total_amount = round(items_total + taxes_total, 2)

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
   <REQUESTDATA>
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
     <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
      <DATE>{voucher_date}</DATE>
      <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
      <VOUCHERNUMBER>{esc_invoice}</VOUCHERNUMBER>
      <ISINVOICE>Yes</ISINVOICE>
      <PARTYNAME>{esc_party}</PARTYNAME>
      <PARTYLEDGERNAME>{esc_party}</PARTYLEDGERNAME>"""

    if esc_gstin:
        xml += f"""
      <PARTYGSTIN>{esc_gstin}</PARTYGSTIN>"""
    if esc_place:
        xml += f"""
      <PLACEOFSUPPLY>{esc_place}</PLACEOFSUPPLY>"""

    # Effective TDS deduction (applied to party's net receivable)
    esc_tds = esc(tds_ledger.strip()) if tds_ledger else ""
    tds_amt = round(float(tds_amount), 2) if (tds_ledger and tds_amount) else 0.0
    party_net = round(total_amount - tds_amt, 2)

    # Party ledger entry — net of TDS
    xml += f"""
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>{esc_party}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <LEDGERFROMITEM>No</LEDGERFROMITEM>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
       <AMOUNT>-{party_net:.2f}</AMOUNT>
      </LEDGERENTRIES.LIST>"""

    # TDS Receivable ledger entry (if TDS is applicable)
    if esc_tds and tds_amt > 0:
        xml += f"""
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>{esc_tds}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <LEDGERFROMITEM>No</LEDGERFROMITEM>
       <ISPARTYLEDGER>No</ISPARTYLEDGER>
       <AMOUNT>-{tds_amt:.2f}</AMOUNT>
      </LEDGERENTRIES.LIST>"""

    # Tax ledger entries (before inventory for Tally compatibility)
    for tax in taxes:
        ledger_name = esc(tax.get("ledger_name", ""))
        tax_amt = float(tax.get("amount", 0))
        if tax_amt > 0:
            xml += f"""
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>{ledger_name}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <LEDGERFROMITEM>No</LEDGERFROMITEM>
       <ISPARTYLEDGER>No</ISPARTYLEDGER>
       <AMOUNT>{tax_amt:.2f}</AMOUNT>
      </LEDGERENTRIES.LIST>"""

    # Inventory entries
    esc_sales = esc(sales_ledger)
    for item in items:
        item_name = esc(item.get("item_name", ""))
        qty = float(item.get("quantity", 0))
        rate = float(item.get("rate", 0))
        amount = round(qty * rate, 2)
        unit = item.get("unit", "Nos")

        xml += f"""
      <ALLINVENTORYENTRIES.LIST>
       <STOCKITEMNAME>{item_name}</STOCKITEMNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <RATE>{rate:.2f}/{unit}</RATE>
       <AMOUNT>{amount:.2f}</AMOUNT>
       <ACTUALQTY>{qty} {unit}</ACTUALQTY>
       <BILLEDQTY>{qty} {unit}</BILLEDQTY>
       <ACCOUNTINGALLOCATIONS.LIST>
        <LEDGERNAME>{esc_sales}</LEDGERNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <AMOUNT>{amount:.2f}</AMOUNT>
       </ACCOUNTINGALLOCATIONS.LIST>
      </ALLINVENTORYENTRIES.LIST>"""

    # Narration
    if esc_narration:
        xml += f"""
      <NARRATION>{esc_narration}</NARRATION>"""

    xml += """
     </VOUCHER>
    </TALLYMESSAGE>
   </REQUESTDATA>
  </IMPORTDATA>
 </BODY>
</ENVELOPE>"""

    logger.info(f"Generated sales voucher XML: {len(items)} items, {len(taxes)} taxes, total={total_amount:.2f}, tds={tds_amt:.2f}")
    return {"xml": xml, "validation_errors": []}