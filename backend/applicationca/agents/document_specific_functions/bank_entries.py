import logging
import json
import re
import os
import base64
import asyncio
import time as time_module
from typing import Dict, List, Any, Optional, Tuple
from openai import AsyncOpenAI
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


def detect_bank_name(text: str) -> str:
    """Detect bank name from text using pattern matching."""
    bank_patterns = [
        (r'Central Bank of India', 'Central Bank of India'),
        (r'State Bank of India|SBI', 'State Bank of India'),
        (r'HDFC Bank', 'HDFC Bank'),
        (r'ICICI Bank', 'ICICI Bank'),
        (r'Axis Bank', 'Axis Bank'),
        (r'Kotak Mahindra', 'Kotak Mahindra Bank'),
        (r'Punjab National Bank|PNB', 'Punjab National Bank'),
        (r'Bank of Baroda|BOB', 'Bank of Baroda'),
        (r'Canara Bank', 'Canara Bank'),
        (r'Union Bank', 'Union Bank of India'),
        (r'Indian Bank', 'Indian Bank'),
        (r'Bank of India|BOI', 'Bank of India'),
        (r'IDBI Bank', 'IDBI Bank'),
        (r'Yes Bank', 'Yes Bank'),
        (r'IndusInd Bank', 'IndusInd Bank'),
        (r'Federal Bank', 'Federal Bank'),
    ]

    for pattern, name in bank_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return "Bank"


async def extract_bank_transactions(
    extracted_text: str,
    file_path: str,
    openai_model: str,
    openai_api_key: str,
    anthropic_api_key: Optional[str] = None,
    extraction_provider: str = "openai",
) -> List[Dict[str, Any]]:
    """Extract transactions from bank statements using the configured provider."""
    start_time = time_module.time()
    logger.info(f"Starting bank transaction extraction using {extraction_provider}")
    print(f"\nExtracting bank transactions using {extraction_provider}...")

    try:
        # Excel/CSV: use structured tabular extraction
        file_ext = os.path.splitext(file_path)[1].lower() if file_path else ""
        if file_ext in ('.xlsx', '.xls', '.csv'):
            logger.info(f"Tabular bank statement detected ({file_ext}), using structured extraction")
            from utils.tabular_extractor import parse_tabular_file
            from applicationca.agents.document_specific_functions.excel_bank_extractor import extract_bank_transactions_from_dataframe
            df = parse_tabular_file(file_path)
            txns = await extract_bank_transactions_from_dataframe(df, openai_api_key, openai_model)
            elapsed = time_module.time() - start_time
            logger.info(f"Tabular bank extraction completed in {elapsed:.2f}s: {len(txns)} transactions")
            return txns

        # Image files: OCR the text first, then proceed with normal text pipeline
        if file_ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff') and not extracted_text:
            logger.info(f"Image bank statement detected ({file_ext}), running OCR")
            from utils.file_helper import extract_text_from_image
            with open(file_path, 'rb') as f:
                img_bytes = f.read()
            extracted_text = extract_text_from_image(img_bytes)
            if not extracted_text:
                logger.warning("OCR returned empty text for bank statement image")
                return []

        # Detect bank name first (fast, no API needed)
        bank_name = detect_bank_name(extracted_text)
        logger.info(f"Detected bank: {bank_name}")

        # Split into chunks for processing (8000 chars per chunk for safety)
        chunk_size = 8000
        lines = extracted_text.split('\n')
        chunks = []
        current_chunk = ""

        for line in lines:
            if len(current_chunk) + len(line) + 1 > chunk_size:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk = current_chunk + "\n" + line if current_chunk else line

        if current_chunk:
            chunks.append(current_chunk)

        logger.info(f"Split into {len(chunks)} chunks for processing")
        print(f"Processing {len(chunks)} chunks...")

        # Claude extraction path
        if extraction_provider == "claude":
            anthropic_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
            claude_model = os.getenv("CLAUDE_EXTRACTION_MODEL", "claude-opus-4-7")
            if not anthropic_key:
                raise ValueError("Missing Anthropic API key for Claude bank extraction")

            from applicationca.agents.document_specific_functions.claude_extractor import call_claude_async

            async def _extract_chunk_claude(chunk_text, chunk_idx):
                """Extract transactions from a single text chunk using Claude."""
                prompt = f"""Extract ALL bank transactions from this bank statement text.

BANK STATEMENT TEXT:
{chunk_text}

For EACH transaction line, extract:
1. date: in DD/MM/YYYY format
2. description: the full transaction narration/description
3. type: "receipt" if money received (BY TRANSFER, credit), "payment" if money spent (TO TRANSFER, debit)
4. amount: the transaction amount (NOT the balance)

IMPORTANT:
- TO TRANSFER or Debit = payment (money going out)
- BY TRANSFER or Credit = receipt (money coming in)
- Extract the ACTUAL transaction amount, NOT the running balance
- Include ALL transactions, don't skip any

Return a JSON object with a "transactions" key containing an array:
{{"transactions": [{{"date": "DD/MM/YYYY", "description": "full narration", "type": "receipt/payment", "amount": 123.45}}]}}"""

                raw = await call_claude_async(
                    file_path=None,
                    prompt=prompt,
                    api_key=anthropic_key,
                    model=claude_model,
                    system_prompt="You are a bank statement parser. Extract all transactions accurately. Return only valid JSON.",
                    max_tokens=16384,
                )
                content = raw.strip()
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                parsed = json.loads(content)
                txs = parsed.get("transactions", [])
                if isinstance(txs, list):
                    logger.info(f"Chunk {chunk_idx+1}/{len(chunks)}: extracted {len(txs)} transactions (Claude)")
                    return txs
                return []

            logger.info(f"Processing {len(chunks)} chunks in parallel (Claude)...")
            tasks = [_extract_chunk_claude(chunk, i) for i, chunk in enumerate(chunks)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            # Default: OpenAI path
            if not openai_api_key or openai_api_key.strip() == "":
                raise ValueError("Missing OpenAI API key")

            logger.info(f"Extracted text length: {len(extracted_text)} characters")
            logger.info(f"Using model: {openai_model}")

            client = AsyncOpenAI(api_key=openai_api_key)

            async def _extract_chunk(chunk_text, chunk_idx):
                """Extract transactions from a single text chunk."""
                prompt = f"""Extract ALL bank transactions from this bank statement text.

BANK STATEMENT TEXT:
{chunk_text}

For EACH transaction line, extract:
1. date: in DD/MM/YYYY format
2. description: the full transaction narration/description
3. type: "receipt" if money received (BY TRANSFER, credit), "payment" if money spent (TO TRANSFER, debit)
4. amount: the transaction amount (NOT the balance)

IMPORTANT:
- TO TRANSFER or Debit = payment (money going out)
- BY TRANSFER or Credit = receipt (money coming in)
- Extract the ACTUAL transaction amount, NOT the running balance
- Include ALL transactions, don't skip any

Return a JSON object with a "transactions" key containing an array:
{{"transactions": [{{"date": "DD/MM/YYYY", "description": "full narration", "type": "receipt/payment", "amount": 123.45}}]}}"""

                response = await client.chat.completions.create(
                    model=openai_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a bank statement parser. Extract all transactions accurately. Return only valid JSON."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=16384,
                    response_format={"type": "json_object"}
                )

                content = response.choices[0].message.content.strip()
                parsed = json.loads(content)
                txs = parsed.get("transactions", [])
                if isinstance(txs, list):
                    logger.info(f"Chunk {chunk_idx+1}/{len(chunks)}: extracted {len(txs)} transactions")
                    return txs
                return []

            logger.info(f"Processing {len(chunks)} chunks in parallel...")
            print(f"Processing {len(chunks)} chunks in parallel...")
            tasks = [_extract_chunk(chunk, i) for i, chunk in enumerate(chunks)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_transactions = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error processing chunk {i+1}: {str(result)}")
                continue
            all_transactions.extend(result)

        logger.info(f"Total raw transactions extracted: {len(all_transactions)}")

        # Post-process transactions
        processed_transactions = []
        for tx in all_transactions:
            processed_tx = {}

            # Date
            date_str = str(tx.get('date', ''))
            if date_str:
                date_parts = date_str.split('/')
                if len(date_parts) == 3:
                    day, month, year = date_parts
                    if len(year) == 2:
                        year = '20' + year
                    date_str = f"{day}/{month}/{year}"
            processed_tx['date'] = date_str

            # Description
            processed_tx['description'] = tx.get('description', '') or tx.get('narration', '') or 'Transaction'

            # Type
            tx_type = str(tx.get('type', 'payment')).lower()
            if tx_type not in ['receipt', 'payment']:
                tx_type = 'payment'
            processed_tx['type'] = tx_type

            # Amount
            amount = tx.get('amount', 0)
            try:
                amount = float(str(amount).replace(',', ''))
            except:
                amount = 0
            processed_tx['amount'] = abs(amount)

            # Add ledger fields
            if processed_tx['type'] == 'receipt':
                processed_tx['credit_ledger'] = bank_name
                processed_tx['debit_ledger'] = processed_tx['description']
            else:
                processed_tx['credit_ledger'] = processed_tx['description']
                processed_tx['debit_ledger'] = bank_name

            processed_transactions.append(processed_tx)

        logger.info(f"Successfully processed {len(processed_transactions)} transactions")
        print(f"Successfully extracted {len(processed_transactions)} bank transactions")
        return processed_transactions

    except Exception as e:
        logger.error(f"Error in bank transaction extraction: {str(e)}")
        print(f"Error in bank transaction extraction: {str(e)}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        elapsed_time = time_module.time() - start_time
        logger.info(f"Bank transaction extraction completed in {elapsed_time:.2f} seconds")


async def match_bank_transaction_ledgers(
    transactions: List[Dict[str, Any]],
    available_ledgers: List[Dict[str, str]],
    openai_api_key: str,
    openai_model: str
) -> List[Dict[str, Any]]:
    """Match appropriate ledgers for bank transactions using GPT with batch processing."""
    if not transactions:
        logger.warning("No transactions to match ledgers for")
        return []
    
    start_time = time_module.time()
    logger.info(f"Starting ledger matching for {len(transactions)} bank transactions")
    print(f"\nMatching ledgers for {len(transactions)} bank transactions...")
    
    try:
        # Initialize LLM
        llm = ChatOpenAI(
            model=openai_model,
            openai_api_key=openai_api_key,
            temperature=0.1
        )
        
        # Create a list of all available ledger names
        available_ledger_names = [ledger.get("name", "").strip() for ledger in available_ledgers if ledger.get("name", "").strip()]
        
        # Extract unique descriptions that need ledger matching
        # We need to match descriptions that are NOT bank names
        descriptions_to_match = set()
        bank_names = set()
        
        for tx in transactions:
            credit_ledger = tx.get('credit_ledger', '').strip()
            debit_ledger = tx.get('debit_ledger', '').strip()
            
            # Identify bank names (these should already be matched to available ledgers)
            if tx.get('type', '').lower() == 'receipt':
                bank_names.add(credit_ledger)
                if debit_ledger:  # This is the description that needs matching
                    descriptions_to_match.add(debit_ledger)
            else:  # payment
                bank_names.add(debit_ledger)
                if credit_ledger:  # This is the description that needs matching
                    descriptions_to_match.add(credit_ledger)
        
        # Convert to list for processing
        descriptions_list = list(descriptions_to_match)
        
        if not descriptions_list:
            logger.info("No descriptions to match - all ledgers are already identified")
            return transactions
        
        # Match bank names and descriptions concurrently — they are independent
        # lookups, so there is no reason to serialize the LLM round-trips.
        bank_names_list = list(bank_names)
        bank_results, description_ledger_mapping = await asyncio.gather(
            asyncio.gather(*[
                match_bank_name_to_ledger(llm, bank_name, available_ledger_names)
                for bank_name in bank_names_list
            ]),
            batch_match_descriptions_to_ledgers(llm, descriptions_list, available_ledger_names),
        )
        bank_ledger_mapping = dict(zip(bank_names_list, bank_results))
        for bank_name, matched_bank_ledger in bank_ledger_mapping.items():
            logger.info(f"Mapped bank '{bank_name}' to ledger '{matched_bank_ledger}'")
        
        # Update transactions with matched ledgers
        for tx in transactions:
            # Update bank ledgers
            if tx.get('type', '').lower() == 'receipt':
                # credit_ledger is bank, debit_ledger is description
                original_bank = tx.get('credit_ledger', '')
                original_description = tx.get('debit_ledger', '')
                
                tx['credit_ledger'] = bank_ledger_mapping.get(original_bank, original_bank)
                tx['debit_ledger'] = description_ledger_mapping.get(original_description, "Cash")
            else:  # payment
                # credit_ledger is description, debit_ledger is bank
                original_description = tx.get('credit_ledger', '')
                original_bank = tx.get('debit_ledger', '')
                
                tx['credit_ledger'] = description_ledger_mapping.get(original_description, "Cash")
                tx['debit_ledger'] = bank_ledger_mapping.get(original_bank, original_bank)
        
        logger.info(f"Successfully matched ledgers for {len(transactions)} bank transactions")
        print(f"Successfully matched ledgers for {len(transactions)} bank transactions")
        return transactions
        
    except Exception as e:
        logger.error(f"Error in bank ledger matching: {str(e)}")
        # Add default ledger as fallback
        for tx in transactions:
            if 'credit_ledger' in tx:
                tx['credit_ledger'] = "Cash"
            if 'debit_ledger' in tx:
                tx['debit_ledger'] = "Cash"
        return transactions
    finally:
        elapsed_time = time_module.time() - start_time
        logger.info(f"Bank ledger matching completed in {elapsed_time:.2f} seconds")


async def match_bank_name_to_ledger(
    llm: ChatOpenAI,
    bank_name: str,
    available_ledgers: List[str]
) -> str:
    """Match bank name to the closest available ledger."""
    try:
        logger.info(f"Matching bank name '{bank_name}' to available ledgers")
        
        prompt = f"""Find the best matching ledger for this bank name from the available ledgers list.

Bank Name: {bank_name}

Available Ledgers:
{json.dumps(available_ledgers, indent=2)}

Rules:
1. Look for exact matches first
2. Then look for partial matches (e.g., "HDFC" matches "HDFC Bank" or "HDFC Ltd")
3. Consider common bank name variations and abbreviations
4. Return ONLY the exact ledger name from the available list
5. If no good match is found, return the original bank name as-is

Return ONLY the matched ledger name, nothing else."""

        response = await llm.ainvoke([
            SystemMessage(content="You are a bank name matching assistant that finds the closest matching ledger for bank names."),
            HumanMessage(content=prompt)
        ])
        
        matched_ledger = response.content.strip()
        
        # Validate the matched ledger exists in available list
        if matched_ledger in available_ledgers:
            logger.info(f"Bank '{bank_name}' matched to ledger '{matched_ledger}'")
            return matched_ledger
        else:
            logger.info(f"Bank '{bank_name}' not found in available ledgers, keeping original name")
            return bank_name
            
    except Exception as e:
        logger.error(f"Error matching bank name '{bank_name}': {str(e)}")
        return bank_name


async def batch_match_descriptions_to_ledgers(
    llm: ChatOpenAI,
    descriptions: List[str],
    available_ledgers: List[str]
) -> Dict[str, str]:
    """Batch match multiple transaction descriptions to available ledgers in parallel batches."""
    if not descriptions:
        return {}

    BATCH_SIZE = 25
    logger.info(f"Batch matching {len(descriptions)} descriptions to available ledgers (batch size: {BATCH_SIZE})")

    async def _match_batch(batch_descriptions: List[str]) -> Dict[str, str]:
        """Match a single batch of descriptions to ledgers."""
        numbered = {i+1: desc for i, desc in enumerate(batch_descriptions)}

        prompt = f"""Analyze these bank transaction descriptions and match each one to the best available ledger.

Transaction Descriptions:
{json.dumps(numbered, indent=2)}

Available Ledgers:
{json.dumps(available_ledgers, indent=2)}

Rules:
1. The transaction narration contains the complete description of the transaction
2. Extract the relevant party/account from the narration and match it to an available ledger
3. Look for key identifiers like company names, payment methods (NEFT, RTGS, etc.), or transaction purposes
4. Return ONLY the exact ledger name from the available list that best matches
5. If no good match is found, return "Cash"
6. Do not modify or create new ledger names

Return the result in this exact JSON format:
{{
    "1": "matched_ledger_name_for_description_1",
    "2": "matched_ledger_name_for_description_2"
}}

Return ONLY the JSON mapping, nothing else."""

        response = await llm.ainvoke([
            SystemMessage(content="You are an expert transaction description matching assistant specializing in fuzzy matching of bank transaction narrations to ledger names. You excel at identifying parties from abbreviated, truncated, or incomplete transaction descriptions, handling common banking abbreviations, and matching them to the closest available ledgers using semantic similarity."),
            HumanMessage(content=prompt)
        ])

        response_content = response.content.strip()
        if "```json" in response_content:
            response_content = response_content.split("```json")[1].split("```")[0].strip()
        elif "```" in response_content:
            response_content = response_content.split("```")[1].split("```")[0].strip()

        mapping_result = json.loads(response_content)
        batch_mapping = {}
        for idx_str, matched_ledger in mapping_result.items():
            try:
                idx = int(idx_str)
                if idx in numbered:
                    original_desc = numbered[idx]
                    if matched_ledger in available_ledgers or matched_ledger == "Cash":
                        batch_mapping[original_desc] = matched_ledger
                    else:
                        batch_mapping[original_desc] = "Cash"
            except (ValueError, KeyError):
                continue
        return batch_mapping

    try:
        # Split into batches and process in parallel
        batches = [descriptions[i:i+BATCH_SIZE] for i in range(0, len(descriptions), BATCH_SIZE)]
        logger.info(f"Processing {len(batches)} ledger matching batches in parallel...")

        results = await asyncio.gather(*[_match_batch(batch) for batch in batches], return_exceptions=True)

        # Merge results
        merged_mapping = {}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error in ledger matching batch {i+1}: {str(result)}")
                for desc in batches[i]:
                    merged_mapping[desc] = "Cash"
                continue
            merged_mapping.update(result)

        # Ensure all descriptions have a mapping
        for desc in descriptions:
            if desc not in merged_mapping:
                merged_mapping[desc] = "Cash"
                logger.warning(f"No mapping found for '{desc}', defaulting to Cash")

        logger.info(f"Successfully batch matched {len(merged_mapping)} descriptions")
        return merged_mapping

    except Exception as e:
        logger.error(f"Error in batch description matching: {str(e)}")
        return {desc: "Cash" for desc in descriptions}


def convert_date_to_tally_format(date_str: str) -> str:
    """Convert various date formats to Tally's YYYYMMDD format (e.g. '20250312')."""
    try:
        if not date_str:
            logger.warning("Empty date string provided to convert_date_to_tally_format")
            return ""

        date_str = str(date_str).strip()

        # Already YYYYMMDD
        if re.match(r'^\d{8}$', date_str):
            return date_str

        # YYYY-MM-DD
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            return date_str.replace("-", "")

        # DD/MM/YYYY
        if '/' in date_str:
            parts = date_str.split('/')
            if len(parts) == 3:
                day, month, year = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if len(year) == 2:
                    year = '20' + year
                return f"{year}{month.zfill(2)}{day.zfill(2)}"

        # DD-MM-YYYY
        if '-' in date_str:
            parts = date_str.split('-')
            if len(parts) == 3:
                p0, p1, p2 = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if len(p0) == 4:
                    return f"{p0}{p1.zfill(2)}{p2.zfill(2)}"
                else:
                    if len(p2) == 2:
                        p2 = '20' + p2
                    return f"{p2}{p1.zfill(2)}{p0.zfill(2)}"

        # DD.MM.YYYY
        if '.' in date_str:
            parts = date_str.split('.')
            if len(parts) == 3:
                day, month, year = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if len(year) == 2:
                    year = '20' + year
                return f"{year}{month.zfill(2)}{day.zfill(2)}"

        logger.error(f"Unrecognized date format: '{date_str}'")
        return ""
    except Exception as e:
        logger.error(f"Error converting date '{date_str}' to Tally format: {str(e)}")
        return ""

def escape_xml_text(text: str) -> str:
    """Escape special characters for XML text content."""
    if not text:
        return text
    result = str(text)
    result = result.replace('&', '&amp;')
    result = result.replace('<', '&lt;')
    result = result.replace('>', '&gt;')
    result = result.replace('"', '&quot;')
    result = result.replace("'", '&#39;')
    return result


def generate_bank_xml(transactions: List[Dict[str, Any]], company_name: str) -> str:
    """Generate Tally XML for bank transactions."""
    if not transactions:
        logger.warning("No transactions to generate XML for")
        return ""

    start_time = time_module.time()
    logger.info(f"Generating Tally XML for {len(transactions)} bank transactions")
    print(f"\nGenerating Tally XML for {len(transactions)} bank transactions...")

    try:
        escaped_company = escape_xml_text(company_name)

        # Collect voucher blocks first, then build header with FY dates
        voucher_parts = []
        all_tally_dates = []

        # Process each transaction
        for tx in transactions:
            description = escape_xml_text(tx.get('description', 'Bank Transaction'))
            amount = tx.get('amount', 0)
            tx_type = tx.get('type', 'payment').lower()
            credit_ledger = escape_xml_text(tx.get('credit_ledger', 'Cash'))
            debit_ledger = escape_xml_text(tx.get('debit_ledger', 'Cash'))

            # Get the date from transaction and convert to Tally YYYYMMDD format
            date = tx.get('date', '')
            tally_date = convert_date_to_tally_format(date)

            # Skip transaction if we don't have a valid YYYYMMDD date
            if not tally_date or not re.match(r'^\d{8}$', tally_date):
                logger.warning(f"Skipping transaction with invalid date '{date}' -> '{tally_date}': {description}")
                continue

            all_tally_dates.append(tally_date)

            # Determine voucher type based on transaction type
            voucher_type = "Receipt" if tx_type == "receipt" else "Payment"

            # Determine party ledger for PARTYLEDGERNAME (the non-bank counterparty)
            if tx_type == "receipt":
                party_name = debit_ledger   # For receipt, debit_ledger is the party
            else:
                party_name = credit_ledger  # For payment, credit_ledger is the party

            # Both Receipt and Payment use the same XML structure;
            # only the ledger order and voucher type differ
            xml_transaction = f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
     <VOUCHER DATE="{tally_date}" VCHTYPE="{voucher_type}" ACTION="Create" OBJVIEW="Accounting Voucher View">
      <DATE>{tally_date}</DATE>
      <VOUCHERTYPENAME>{voucher_type}</VOUCHERTYPENAME>
      <PARTYLEDGERNAME>{party_name}</PARTYLEDGERNAME>
      <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
      <ISVOUCHERENTRY>Yes</ISVOUCHERENTRY>
      <EFFECTIVEDATE>{tally_date}</EFFECTIVEDATE>
      <NARRATION>{description}</NARRATION>
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{debit_ledger}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-{amount}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{credit_ledger}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <AMOUNT>{amount}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
     </VOUCHER>
    </TALLYMESSAGE>"""

            voucher_parts.append(xml_transaction)

        if not voucher_parts:
            logger.warning("No valid transactions to generate XML for (all skipped)")
            return ""

        # Compute financial year from transaction dates for SVFROMDATE/SVTODATE
        sv_vars = ""
        if all_tally_dates:
            min_date = min(all_tally_dates)
            max_date = max(all_tally_dates)
            min_year, min_month = int(min_date[:4]), int(min_date[4:6])
            max_year, max_month = int(max_date[:4]), int(max_date[4:6])
            fy_start = min_year - 1 if min_month < 4 else min_year
            fy_end = max_year if max_month < 4 else max_year + 1
            sv_vars = f"""
     <SVFROMDATE>{fy_start}0401</SVFROMDATE>
     <SVTODATE>{fy_end}0331</SVTODATE>"""

        # Build complete XML with header, vouchers, and footer
        xml_header = f"""<ENVELOPE>
 <HEADER>
  <TALLYREQUEST>Import Data</TALLYREQUEST>
 </HEADER>
 <BODY>
  <IMPORTDATA>
   <REQUESTDESC>
    <REPORTNAME>Vouchers</REPORTNAME>
    <STATICVARIABLES>
     <SVCURRENTCOMPANY>{escaped_company}</SVCURRENTCOMPANY>{sv_vars}
    </STATICVARIABLES>
   </REQUESTDESC>
   <REQUESTDATA>"""

        xml_footer = """
   </REQUESTDATA>
  </IMPORTDATA>
 </BODY>
</ENVELOPE>"""

        full_xml = xml_header + "".join(voucher_parts) + xml_footer

        logger.info(f"Successfully generated Tally XML for {len(transactions)} bank transactions")
        print(f"Successfully generated Tally XML for {len(transactions)} bank transactions")
        return full_xml

    except Exception as e:
        logger.error(f"Error generating bank transaction XML: {str(e)}")
        return ""
    finally:
        elapsed_time = time_module.time() - start_time
        logger.info(f"Bank XML generation completed in {elapsed_time:.2f} seconds")