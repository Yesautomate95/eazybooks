from fastapi import APIRouter, HTTPException, File, UploadFile, Depends, Form, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from config.settings import settings
from typing import List, Dict, Optional, Any
import tempfile
import os
import logging
import asyncio
import re
import requests
from urllib.parse import urlparse
from applicationca.agents.tally_agent import tally_agent
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from utils.auth_utils import verify_jwt
from fastapi.responses import JSONResponse, StreamingResponse
from applicationca.contoller.websocket_controller import manager
from applicationca.database.database_retrieval import (
    get_lockr_by_name_and_user,
    execute_query,
    get_user_id_by_username,
    get_lockers_and_clients_by_user
)
from applicationca.database.database_update import add_new_lockr
import json
import uuid
from applicationca.agents.get_tally_data import TallyVanna, vn, execute_with_fallback, train_tally_structure
import pandas as pd
from firebase_admin import auth
from decimal import Decimal
from datetime import date, datetime
from applicationca.services.tally_connector import tally_connector
from applicationca.agents.tally_react_agent import run_agent as run_tally_agent
from applicationca.services import tally_drafts_store
from applicationca.services import tally_chat_store
from applicationca.services import r2_service
from applicationca.services import tally_cache_store
from applicationca.rbac import require_permission

class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

router = APIRouter()
logger = logging.getLogger(__name__)

# JWT Authentication Scheme
security = HTTPBearer()

@router.get("/check-tally-connection", dependencies=[Depends(require_permission("tally.read"))])
async def check_tally_connection(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Check if Tally is connected and accessible."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        
        # Check Tally connection using websocket manager
        is_connected = await manager.check_tally_connection(user_data["uid"])


        return {
            "connected": is_connected,
            "message": "Tally is connected and accessible" if is_connected else "Tally is not connected"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking Tally connection: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error checking Tally connection: {str(e)}"
        )


@router.post("/sync-company-folders", dependencies=[Depends(require_permission("tally.sync"))])
async def sync_company_folders(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Sync folders with Tally companies:
    1. Get companies from Tally via websocket
    2. Create folders for each company
    3. Return status
    """
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        
        # Get companies from Tally through websocket
        try:
            companies = await manager.get_tally_companies(user_data["uid"])
            if not companies:
                return JSONResponse(
                    {"error": "No companies found in Tally"}, 
                    status_code=404
                )
        except HTTPException as e:
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected. Please ensure the desktop app is running."}, 
                    status_code=400
                )
            raise e
        
        # Create folders for each company
        created_folders = []
        existing_folders = []
        
        for company in companies:
            company_name = company.get("name")
            if not company_name:
                continue
                
            # Check if folder already exists
            existing_lockr = get_lockr_by_name_and_user(user_data["uid"], company_name)
            
            if existing_lockr:
                existing_folders.append({
                    "name": company_name,
                    "lockr_id": existing_lockr
                })
            else:
                # Create new folder
                new_lockr_id = add_new_lockr(user_data["uid"], company_name)
                if new_lockr_id:
                    created_folders.append({
                        "name": company_name,
                        "lockr_id": new_lockr_id
                    })
        
        return {
            "status": "success",
            "companies_found": len(companies),
            "created": {
                "count": len(created_folders),
                "folders": created_folders
            },
            "existing": {
                "count": len(existing_folders),
                "folders": existing_folders
            }
        }
        
    except Exception as e:
        logger.error(f"Error syncing company folders: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error syncing company folders: {str(e)}"
        )

@router.post("/process", dependencies=[Depends(require_permission("tally.push"))])
async def process_file(
    file: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Process an uploaded file through the tally agent."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
            
        # Create a temporary file to store the upload
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(await file.read())
            temp_file_path = temp_file.name

        try:
            # Process the file with the tally agent
            result = await tally_agent(temp_file_path)
            
            # Format the response
            response = {
                "filename": file.filename,
                "success": not bool(result.get("error")),
                "transactions_created": len(result.get("transactions", [])),
                "document_type": result.get("document_type"),
                "error": result.get("error"),
                "transactions": result.get("transactions", []),
                "thought_process": result.get("thought_process", [])
            }
            
            return response
            
        finally:
            # Clean up the temporary file
            try:
                os.unlink(temp_file_path)
            except:
                pass
        
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing file: {str(e)}"
        )

class TallyPushTransaction(BaseModel):
    vendor_name: str = ""
    vendor_gstin: str = ""
    party_ledger: str = ""
    purchase_ledger: str = ""
    sales_ledger: str = ""
    items: List[Dict[str, Any]] = []
    tax_entries: List[Dict[str, Any]] = []
    tds_section: str = ""
    narration: str = ""
    total_amount: Optional[float] = None
    # AI suggestions the frontend stamped onto the row at suggest time so the
    # backend can detect overrides at push time and record them into
    # ledger_suggestion_feedback / confirmation_source.
    ai_party_suggestion: Optional[Dict[str, Any]] = None
    ai_expense_suggestion: Optional[Dict[str, Any]] = None


class TallyPushRequest(BaseModel):
    xml: str
    company: str
    transactions: Optional[List[TallyPushTransaction]] = None
    voucher_type: str = "auto"


_MISSING_LEDGER_RE = re.compile(r"Ledger\s+'([^']+)'\s+does not exist", re.IGNORECASE)


def _missing_ledger_from_error(error_msg: str) -> Optional[str]:
    """Extract the ledger name from Tally's `Ledger '<name>' does not exist!`
    LINEERROR text, or None if the error is something else."""
    match = _MISSING_LEDGER_RE.search(error_msg or "")
    return match.group(1).strip() if match else None


def _guess_ledger_group_and_gstin(
    ledger_name: str,
    voucher_type: str,
    transactions: Optional[List[TallyPushTransaction]],
) -> tuple:
    """Best-effort parent group + GSTIN for a ledger Tally reports missing.

    The missing ledger can be the vendor/customer party, the purchase or
    sales account, or a tax ledger — matching against the pushed
    transaction's own fields (and falling back to name-based heuristics)
    picks the right parent group instead of always guessing Sundry
    Debtors/Creditors, which is wrong for anything that isn't the party.
    """
    name_lower = ledger_name.strip().lower()
    party_group = "Sundry Creditors" if "purchase" in (voucher_type or "").lower() else "Sundry Debtors"

    for tx in transactions or []:
        if tx.party_ledger.strip().lower() == name_lower:
            return party_group, tx.vendor_gstin.strip()
        if tx.purchase_ledger.strip().lower() == name_lower:
            return "Purchase Accounts", ""
        if tx.sales_ledger.strip().lower() == name_lower:
            return "Sales Accounts", ""
        for te in tx.tax_entries or []:
            if str(te.get("ledger_name", "")).strip().lower() == name_lower:
                return "Duties & Taxes", ""

    # Not found on any pushed row (e.g. XML built outside this request) —
    # fall back to name-based heuristics before defaulting to the party group.
    if "purchase" in name_lower:
        return "Purchase Accounts", ""
    if "sales" in name_lower:
        return "Sales Accounts", ""
    if any(tag in name_lower for tag in ("gst", "tax", "cess", "tds", "tcs")):
        return "Duties & Taxes", ""
    return party_group, ""


@router.post("/generate-xml", dependencies=[Depends(require_permission("tally.push"))])
async def generate_xml(
    files: List[UploadFile] = File(...),
    company: str = Form(...),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Generate Tally XML from uploaded files."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        
        logger.info(f"Starting XML generation for company: {company}")
        logger.info(f"Number of files to process: {len(files)}")
        for file in files:
            logger.debug(f"File to process: {file.filename}")
            
        # Get available ledgers
        try:
            logger.info(f"Fetching ledger data from Tally for company: {company}")
            ledger_data = await manager.get_tally_ledgers(user_data["uid"], company)
            ledger_names = [ledger["name"] for ledger in ledger_data]
            logger.info(f"Retrieved {len(ledger_names)} ledgers")
            logger.debug(f"Available ledgers: {json.dumps(ledger_names, indent=2)}")
        except Exception as e:
            logger.error(f"Error retrieving ledgers: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error retrieving ledgers: {str(e)}"
            )

        # Get available inventory items
        try:
            logger.info(f"Fetching inventory data from Tally for company: {company}")
            inventory_data = await manager.get_tally_inventory(user_data["uid"], company)
            inventory_names = [item["name"] for item in inventory_data if "name" in item]
            # Create inventory_names_with_units list including both name and unit information
            inventory_names_with_units = [{"name": item["name"], "unit": item.get("baseUnits", "Nos")}
                                         for item in inventory_data if "name" in item]
            logger.info(f"Retrieved {len(inventory_names)} inventory items")
            logger.debug(f"Available inventory items: {json.dumps(inventory_names, indent=2)}")
            logger.debug(f"Available inventory items with units: {json.dumps(inventory_names_with_units, indent=2)}")
        except Exception as e:
            logger.error(f"Error retrieving inventory items: {str(e)}")
            logger.warning("Proceeding without inventory validation")
            inventory_names = []
            inventory_names_with_units = []

        # Get available godowns
        try:
            logger.info(f"Fetching godown data from Tally for company: {company}")
            godown_data = await manager.get_tally_godowns(user_data["uid"], company)
            godown_names = [godown["name"] for godown in godown_data if "name" in godown]
            logger.info(f"Retrieved {len(godown_names)} godowns")
            logger.debug(f"Available godowns: {json.dumps(godown_names, indent=2)}")
        except Exception as e:
            logger.error(f"Error retrieving godowns: {str(e)}")
            logger.warning("Proceeding without godown information")
            godown_names = []
            
        # Create a list to store temporary file paths
        temp_files_info = []
        logger.info("Creating temporary files for processing...")
        
        # Create temporary files for all uploads
        for file in files:
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as temp_file:
                content = await file.read()
                temp_file.write(content)
                temp_file_path = temp_file.name
                temp_files_info.append({
                    "path": temp_file_path,
                    "filename": file.filename
                })
                logger.debug(f"Created temp file for {file.filename} at {temp_file_path}")
        print('inventory names',inventory_names)
        try:
            # Create tasks for parallel processing
            tasks = []
            for file_info in temp_files_info:
                task = process_file_for_xml(
                    file_info["path"], 
                    file_info["filename"], 
                    ledger_data, 
                    company,
                    inventory_names,
                    inventory_names_with_units
                )
                tasks.append(task)
            
            logger.info(f"Processing {len(tasks)} files in parallel...")
            results = await asyncio.gather(*tasks)
            
            # Process results
            tally_xml = []
            successful_files = 0
            
            for i, result in enumerate(results):
                original_filename = temp_files_info[i]["filename"]
                # Include result even if XML generation failed - transactions may still be extracted
                transactions = result.get("transactions", [])

                if result.get("tally_xml") or transactions:
                    successful_files += 1
                    tally_xml.append({
                        "filename": original_filename,
                        "xml": result.get("tally_xml", ""),
                        "documentType": result.get("document_type", "Unknown"),
                        "extractedText": result.get("extracted_text", ""),
                        "transactions": transactions,
                        "transactionsCount": len(transactions)
                    })
                    logger.debug(f"Successfully processed {original_filename}: {len(transactions)} transactions")
                else:
                    logger.warning(f"Failed to process {original_filename}: no XML or transactions")
            
            logger.info(f"XML generation completed. Success: {successful_files}/{len(files)} files")
            
            response_data = {
                "ledgers": ledger_names,
                "inventory": inventory_names,
                "inventory_with_units": inventory_names_with_units,
                "godowns": godown_names,
                "tallyxml": tally_xml
            }
            debug_info = {
                'ledger_count': len(ledger_names),
                'inventory_count': len(inventory_names),
                'inventory_with_units_count': len(inventory_names_with_units),
                'godown_count': len(godown_names),
                'xml_count': len(tally_xml)
            }
            logger.debug("Final response data structure: %s", json.dumps(debug_info, indent=2))
            
            return response_data
            
        finally:
            # Clean up all temporary files
            logger.info("Cleaning up temporary files...")
            for file_info in temp_files_info:
                try:
                    os.unlink(file_info["path"])
                    logger.debug(f"Deleted temporary file: {file_info['path']}")
                except Exception as e:
                    logger.error(f"Error deleting temporary file {file_info['path']}: {str(e)}")
                    pass
        
    except Exception as e:
        logger.error(f"Error generating XML: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error generating XML: {str(e)}"
        )

## ── Sales Invoice Upload & XML Generation ──

class SalesInvoiceHeader(BaseModel):
    invoice_type: str = "Sales"
    voucher_date: str
    invoice_number: str = ""
    party_name: str
    party_gstin: str = ""
    place_of_supply: str = ""

class SalesInvoiceItem(BaseModel):
    sr_no: int
    item_name: str
    quantity: float
    rate: float
    amount: float
    unit: str = "Nos"
    discount_amount: float = 0
    discount_percentage: float = 0
    batch_number: str = ""

class SalesInvoiceTax(BaseModel):
    sr_no: int
    ledger_name: str
    description: str = ""
    amount: float

class SalesInvoiceSummary(BaseModel):
    subtotal: float
    tax_amount: float
    total_amount: float
    narration: str = ""

class GenerateSalesVoucherRequest(BaseModel):
    header: SalesInvoiceHeader
    items: List[SalesInvoiceItem]
    taxes: List[SalesInvoiceTax]
    summary: SalesInvoiceSummary
    company: str
    sales_ledger: str = "Sales"
    tds_ledger: Optional[str] = ""
    tds_amount: Optional[float] = 0.0


@router.post("/upload-sales-invoice", dependencies=[Depends(require_permission("tally.push"))])
async def upload_sales_invoice(
    files: List[UploadFile] = File(...),
    company: str = Form(...),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Upload a sales invoice PDF and extract structured data for the editable form."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        logger.info(f"Sales invoice upload for company: {company}, files: {len(files)}")

        # Fetch ledgers from Tally (prefer direct HTTP, fallback to WebSocket)
        ledger_data = []
        ledger_names = []
        try:
            ledger_data = await tally_connector.get_ledgers(company)
            ledger_names = [ledger.get("name", "") for ledger in ledger_data] if ledger_data else []
        except Exception:
            try:
                ledger_data = await manager.get_tally_ledgers(user_data["uid"], company)
                ledger_names = [ledger["name"] for ledger in ledger_data]
            except Exception as e:
                logger.error(f"Error retrieving ledgers: {e}")
                ledger_data = []
                ledger_names = []

        # Fetch inventory from Tally (prefer direct HTTP, fallback to WebSocket)
        inventory_data = []
        inventory_names = []
        inventory_names_with_units = []
        try:
            inventory_data = await tally_connector.get_inventory(company)
            inventory_names = [item.get("name", "") for item in inventory_data if "name" in item] if inventory_data else []
            inventory_names_with_units = [
                {"name": item["name"], "unit": item.get("baseUnits", "Nos")}
                for item in inventory_data if "name" in item
            ] if inventory_data else []
        except Exception:
            try:
                inventory_data = await manager.get_tally_inventory(user_data["uid"], company)
                inventory_names = [item["name"] for item in inventory_data if "name" in item]
                inventory_names_with_units = [
                    {"name": item["name"], "unit": item.get("baseUnits", "Nos")}
                    for item in inventory_data if "name" in item
                ]
            except Exception as e:
                logger.error(f"Error retrieving inventory: {e}")
                inventory_names = []
                inventory_names_with_units = []

        # Categorize ledgers for the frontend dropdowns
        party_ledgers = []
        sales_ledgers = []
        tax_ledgers = []
        all_ledger_names = []

        for ledger in ledger_data:
            name = ledger.get("name", "")
            group = ledger.get("parent_group", ledger.get("group", ledger.get("parent", "")))
            all_ledger_names.append(name)

            group_lower = group.lower() if group else ""
            if group_lower in ("sundry debtors", "sundry creditors"):
                party_ledgers.append(name)
            elif group_lower in ("sales accounts", "sales account"):
                sales_ledgers.append(name)
            elif group_lower in ("duties & taxes", "duties and taxes", "duties &amp; taxes"):
                tax_ledgers.append(name)

        # Ensure default ledgers exist in lists
        if not sales_ledgers:
            sales_ledgers = ["Sales"]

        # Process uploaded file
        if not files:
            return JSONResponse({"error": "No files uploaded"}, status_code=400)

        file = files[0]  # Process first file
        temp_path = None
        try:
            # Save to temp
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
                content = await file.read()
                tmp.write(content)
                temp_path = tmp.name

            # Build preview data based on file type
            import base64
            from utils.file_helper import get_file_category, get_mime_type
            file_category = get_file_category(file.filename)
            file_b64 = base64.b64encode(content).decode("utf-8")
            mime = get_mime_type(file.filename)

            if file_category == "pdf":
                file_content = f"data:{mime};base64,{file_b64}"
            elif file_category == "image":
                file_content = f"data:{mime};base64,{file_b64}"
            else:
                file_content = None

            # Extract structured invoice data
            from applicationca.agents.document_specific_functions.sales import extract_sales_invoice_structured
            invoice_data = await extract_sales_invoice_structured(
                file_path=temp_path,
                company=company,
                gemini_model="gemini-2.5-pro",
                google_api_key=settings.GEMINI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                openai_model=settings.OPENAI_MODEL,
                available_ledgers=ledger_data,
                inventory_names=inventory_names,
                inventory_names_with_units=inventory_names_with_units,
                voucher_type="sales",
                anthropic_api_key=settings.ANTHROPIC_API_KEY,
                extraction_provider=settings.EXTRACTION_PROVIDER,
            )

            return {
                "success": True,
                "invoice": invoice_data,
                "ledgers": {
                    "party": party_ledgers,
                    "sales": sales_ledgers,
                    "tax": tax_ledgers,
                    "all": all_ledger_names
                },
                "stock_items": inventory_names,
                "stock_items_with_units": inventory_names_with_units,
                "pdf_content": file_content,
                "file_type": file_category,
            }

        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    except Exception as e:
        logger.error(f"Error in upload-sales-invoice: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing sales invoice: {str(e)}")


# ══════════════════════════════════════════════════════════════
# AI Ledger Suggestion Endpoints
# ══════════════════════════════════════════════════════════════

class SuggestLedgerLineItem(BaseModel):
    name: str = ""
    description: str = ""
    qty: float = 0
    quantity: float = 0
    rate: float = 0
    amount: float = 0

class SuggestLedgerTaxDetails(BaseModel):
    cgst: float = 0
    sgst: float = 0
    igst: float = 0
    total_tax: float = 0

class SuggestLedgerRequest(BaseModel):
    bill_text: str = ""
    vendor_name: str = ""
    vendor_gstin: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    line_items: List[SuggestLedgerLineItem] = []
    total_amount: float = 0
    subtotal: float = 0
    tax_details: SuggestLedgerTaxDetails = SuggestLedgerTaxDetails()
    hsn_codes: List[str] = []
    company_name: str = ""
    voucher_type: str = "auto"  # sales_invoice, purchase_bill, payment_voucher, etc.


@router.post("/suggest-ledger", dependencies=[Depends(require_permission("tally.read"))])
async def suggest_ledger(
    request: SuggestLedgerRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    AI-powered ledger suggestion for a bill/invoice.

    Voucher-type-aware: only fetches the relevant Tally group branch.
    - sales_invoice  → Sundry Debtors + Sales Accounts
    - purchase_bill  → Sundry Creditors + Purchase Accounts
    - auto           → All relevant groups (fallback)

    Returns ranked suggestions with 0-1 confidence scores and action hints:
    - auto_select (>= 0.85): safe to auto-fill
    - suggest (0.50-0.85): show as suggestion, user confirms
    - create_new (< 0.50): offer to create new ledger
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        company = request.company_name
        if not company:
            raise HTTPException(status_code=400, detail="company_name is required")

        from applicationca.services.ledger_suggestion_service import suggest_ledgers

        bill_data = {
            "vendor_name": request.vendor_name,
            "vendor_gstin": request.vendor_gstin,
            "invoice_number": request.invoice_number,
            "invoice_date": request.invoice_date,
            "company_name": company,
            "line_items": [item.dict() for item in request.line_items],
            "total_amount": request.total_amount,
            "subtotal": request.subtotal or request.total_amount,
            "tax_details": request.tax_details.dict(),
            "hsn_codes": request.hsn_codes,
            "bill_text": request.bill_text,
        }

        # Pass firebase_uid for WebSocket bridge and user_id for mapping rules
        firebase_uid = user_data.get("uid")
        logger.info(
            "suggest-ledger request: vendor_name='%s', vendor_gstin='%s', "
            "voucher_type='%s', company='%s', line_items=%d, total=%.2f",
            request.vendor_name, request.vendor_gstin,
            request.voucher_type, company,
            len(request.line_items), request.total_amount,
        )
        result = await suggest_ledgers(
            bill_data, company,
            voucher_type=request.voucher_type,
            firebase_uid=firebase_uid,
            user_id=firebase_uid,
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in suggest-ledger: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error suggesting ledgers: {str(e)}")


class SuggestLedgerFeedback(BaseModel):
    """Live override feedback fired when the user picks a ledger different
    from the AI's suggestion. Recorded into ledger_suggestion_feedback so
    rule mining can spot correction patterns and Layer 0 scoring can give
    user-corrected history rows higher weight."""
    company_name: str
    field: str  # 'party_ledger' | 'expense_ledger'
    vendor_name: Optional[str] = None
    vendor_gstin: Optional[str] = None
    voucher_type: Optional[str] = None
    amount: Optional[float] = None
    hsn_codes: Optional[List[str]] = None
    suggested_ledger: Optional[str] = None
    suggested_match_type: Optional[str] = None
    suggested_confidence: Optional[float] = None
    picked_ledger: str
    invoice_draft_id: Optional[str] = None


@router.post("/suggest-ledger/feedback", dependencies=[Depends(require_permission("tally.read"))])
async def submit_suggest_ledger_feedback(
    feedback: SuggestLedgerFeedback,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Record a live override before push. Fire-and-forget — never raises."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.tally_history_service import record_correction
        record_correction(
            user_id=user_data["uid"],
            company_name=feedback.company_name,
            field=feedback.field,
            vendor_name=feedback.vendor_name,
            vendor_gstin=feedback.vendor_gstin,
            voucher_type=feedback.voucher_type,
            amount=feedback.amount,
            hsn_codes=feedback.hsn_codes,
            suggested_ledger=feedback.suggested_ledger,
            suggested_match_type=feedback.suggested_match_type,
            suggested_confidence=feedback.suggested_confidence,
            picked_ledger=feedback.picked_ledger,
            source="live",
            invoice_draft_id=feedback.invoice_draft_id,
        )
        return {"ok": True}
    except Exception as e:
        logger.warning(f"suggest-ledger/feedback failed (non-blocking): {e}")
        return {"ok": False, "error": str(e)}


class MappingRulePreviewRequest(BaseModel):
    """Test a draft rule against a sample of past invoices before saving."""
    company_name: str
    rule_type: str
    document_type: str
    conditions: Optional[Dict[str, Any]] = None
    keywords: Optional[List[str]] = None
    pattern: Optional[str] = None
    vendor_name: Optional[str] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    sample_size: int = 50


@router.post("/mapping-rules/preview", dependencies=[Depends(require_permission("tally.read"))])
async def preview_mapping_rule(
    request: MappingRulePreviewRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Run a draft rule against the user's recent transaction history and
    return the matches so the UI can preview before saving."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Build a transient rule dict the MappingService evaluator understands
        # without persisting anything.
        draft_rule = {
            "rule_id": "preview",
            "rule_type": request.rule_type,
            "conditions": request.conditions,
            "keywords": request.keywords,
            "pattern": request.pattern,
            "vendor_name": request.vendor_name,
            "amount_min": request.amount_min,
            "amount_max": request.amount_max,
        }

        # Sample recent transactions — drafts table holds the rich payload.
        from applicationca.database.database_retrieval import execute_query
        rows = execute_query(
            """
            SELECT title, payload, status, updated_at
              FROM tally_drafts
             WHERE user_id = %s AND company_name = %s
             ORDER BY updated_at DESC
             LIMIT %s
            """,
            (user_data["uid"], request.company_name, request.sample_size),
        ) or []

        from applicationca.services.mapping_service import MappingService
        svc = MappingService(user_data["uid"], request.company_name)

        matches: List[Dict[str, Any]] = []
        scanned = 0
        for row in rows:
            payload = row.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    continue
            # Drafts hold either a single transaction or an array of them.
            txns = payload.get("transactions") if isinstance(payload, dict) else None
            if not txns:
                txns = [payload] if isinstance(payload, dict) else []
            for txn in txns:
                if not isinstance(txn, dict):
                    continue
                scanned += 1
                if svc._rule_matches(draft_rule, txn):
                    matches.append({
                        "draft_title": row.get("title"),
                        "vendor_name": txn.get("vendor_name") or txn.get("party_name"),
                        "amount": txn.get("total_amount") or txn.get("amount"),
                        "voucher_type": txn.get("voucher_type"),
                        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
                    })
                if len(matches) >= 20:
                    break
            if len(matches) >= 20:
                break

        return {
            "match_count": len(matches),
            "scanned_count": scanned,
            "matches": matches,
        }
    except Exception as e:
        logger.error(f"mapping-rules/preview failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ledgers/grouped", dependencies=[Depends(require_permission("tally.read"))])
async def get_grouped_ledgers(
    company: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Returns all ledgers organized by their account groups for dropdown population.

    Categories returned:
    - party_ledgers:    Sundry Debtors, Sundry Creditors
    - purchase_ledgers: Direct Expenses, Indirect Expenses, Purchase Accounts
    - sales_ledgers:    Direct Incomes, Indirect Incomes, Sales Accounts

    Uses WebSocket bridge to fetch from Tally via connected Electron app.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if not company:
            raise HTTPException(status_code=400, detail="company parameter is required")

        firebase_uid = user_data.get("uid")
        if not firebase_uid or firebase_uid not in manager.electron_connections:
            raise HTTPException(
                status_code=503,
                detail="Electron app not connected. Please ensure the desktop app is running and connected."
            )

        # Use WebSocket bridge to fetch grouped ledgers from Electron app
        raw = await manager.get_tally_grouped_ledgers(firebase_uid, company)

        # Transform from WebSocket format (Title Case keys) to frontend format
        # (snake_case keys with _ledgers suffix) so the frontend can use the
        # grouped data directly instead of falling back to parent-name filtering.
        party = raw.get("party", {})
        purchase = raw.get("purchase", {})
        sales = raw.get("sales", {})

        result = {
            "party_ledgers": {
                "sundry_debtors": party.get("Sundry Debtors", []),
                "sundry_creditors": party.get("Sundry Creditors", []),
            },
            "purchase_ledgers": {
                "purchase_accounts": purchase.get("Purchase Accounts", []),
                "direct_expenses": purchase.get("Direct Expenses", []),
                "indirect_expenses": purchase.get("Indirect Expenses", []),
            },
            "sales_ledgers": {
                "sales_accounts": sales.get("Sales Accounts", []),
                "direct_incomes": sales.get("Direct Incomes", []),
                "indirect_incomes": sales.get("Indirect Incomes", []),
            },
        }
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get-grouped-ledgers: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching grouped ledgers: {str(e)}")


@router.post("/extract-bill", dependencies=[Depends(require_permission("tally.push"))])
async def extract_bill(
    file: UploadFile = File(...),
    company: str = Form(""),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Extract structured data from a bill/invoice PDF using OCR.

    Returns vendor name, GSTIN, line items, amounts, tax details, and raw text
    for use with the /suggest-ledger endpoint.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Save to temp file
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
                content = await file.read()
                tmp.write(content)
                temp_path = tmp.name

            # Use existing extraction pipeline
            from applicationca.agents.document_specific_functions.sales import extract_sales_invoice_structured

            # Fetch ledger/inventory context if company provided
            ledgers_data = []
            inventory_names = []
            inventory_with_units = []
            if company:
                try:
                    ledgers_data = await tally_connector.get_ledgers(company) or []
                    inv_data = await tally_connector.get_inventory(company) or []
                    inventory_names = [i.get("name", "") for i in inv_data if i.get("name")]
                    inventory_with_units = [
                        {"name": i["name"], "unit": i.get("baseUnits", "Nos")}
                        for i in inv_data if i.get("name")
                    ]
                except Exception:
                    pass

            invoice_data = await extract_sales_invoice_structured(
                file_path=temp_path,
                company=company,
                gemini_model="gemini-2.5-pro",
                google_api_key=settings.GEMINI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                openai_model=settings.OPENAI_MODEL,
                available_ledgers=ledgers_data,
                inventory_names=inventory_names,
                inventory_names_with_units=inventory_with_units,
                voucher_type="sales",
                anthropic_api_key=settings.ANTHROPIC_API_KEY,
                extraction_provider=settings.EXTRACTION_PROVIDER,
            )

            # Transform to extract-bill response format
            header = invoice_data.get("header", {})
            items = invoice_data.get("items", [])
            taxes = invoice_data.get("taxes", [])
            summary = invoice_data.get("summary", {})

            # Compute tax totals
            total_tax = sum(float(t.get("amount", 0)) for t in taxes)
            subtotal = float(summary.get("subtotal", 0))
            total_amount = float(summary.get("total_amount", 0)) or (subtotal + total_tax)

            return {
                "vendor_name": header.get("party_name", ""),
                "vendor_gstin": header.get("party_gstin", ""),
                "invoice_number": header.get("invoice_number", ""),
                "invoice_date": header.get("voucher_date", header.get("date", "")),
                "line_items": [
                    {
                        "name": it.get("item_name", ""),
                        "qty": float(it.get("quantity", 0)),
                        "rate": float(it.get("rate", 0)),
                        "amount": float(it.get("amount", 0)),
                    }
                    for it in items
                ],
                "total_amount": total_amount,
                "subtotal": subtotal,
                "tax_details": {
                    "cgst": 0,
                    "sgst": 0,
                    "igst": 0,
                    "total_tax": total_tax,
                },
                "hsn_codes": [],
                "raw_text": header.get("raw_text", ""),
                "confidence": invoice_data.get("confidence", header.get("confidence", {})),
            }

        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in extract-bill: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error extracting bill data: {str(e)}")


@router.post("/generate-sales-voucher-xml", dependencies=[Depends(require_permission("tally.push"))])
async def generate_sales_voucher_xml(
    request: GenerateSalesVoucherRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Generate Tally Sales voucher XML from edited form data."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.agents.document_specific_functions.sales import generate_sales_voucher_xml_from_form

        result = generate_sales_voucher_xml_from_form(
            header=request.header.dict(),
            items=[item.dict() for item in request.items],
            taxes=[tax.dict() for tax in request.taxes],
            summary=request.summary.dict(),
            company=request.company,
            sales_ledger=request.sales_ledger,
            tds_ledger=request.tds_ledger or "",
            tds_amount=float(request.tds_amount or 0),
        )

        if result["validation_errors"]:
            return JSONResponse(
                {"success": False, "validation_errors": result["validation_errors"], "xml": ""},
                status_code=400
            )

        return {
            "success": True,
            "xml": result["xml"],
            "validation_errors": []
        }

    except Exception as e:
        logger.error(f"Error generating sales voucher XML: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating XML: {str(e)}")


async def process_file_for_xml(
    file_path: str,
    filename: str,
    ledgers: List[Dict[str, Any]], 
    company: str,
    inventory_names: List[str],
    inventory_names_with_units: List[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Process a single file for XML generation."""
    try:
        logger.info(f"Starting to process file: {filename}")
        # Process the file with the tally agent, passing the ledgers, company name and inventory names
        result = await tally_agent(file_path, ledgers, company, inventory_names, inventory_names_with_units)
        logger.info(f"Tally agent result for {filename}: {result}")
        
        # Check if we have tally_xml in the result
        if "tally_xml" not in result:
            logger.warning(f"No tally_xml found in result for {filename}")
            result["tally_xml"] = None
            
        return result
    except Exception as e:
        logger.error(f"Error processing file {filename}: {str(e)}")
        return {
            "filename": filename,
            "tally_xml": None,
            "error": str(e),
            "document_type": None
        }

@router.post("/push-to-tally", dependencies=[Depends(require_permission("tally.push"))])
async def push_to_tally(
    request: TallyPushRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Push XML to Tally."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
            
        headers = {'Content-Type': 'application/xml'}
        logger.info("Sending request to Tally...")

        # In local development the backend can reach Tally directly. Use that
        # path first so a stale Electron bridge cannot hold this API for 180s.
        # Production still falls through to the Electron bridge because Tally
        # normally runs on the user's machine, not beside the cloud backend.
        direct_host = urlparse(settings.TALLY_URL).hostname
        is_local_tally = direct_host in {"localhost", "127.0.0.1"}
        if is_local_tally:
            try:
                data = await asyncio.wait_for(
                    tally_connector.push_xml(request.xml, company=request.company),
                    timeout=3,
                )
                # Tally reports one missing ledger at a time, so a voucher
                # referencing several new ledgers (party + tax + purchase/sales
                # account) needs to be retried once per ledger it surfaces.
                for _ in range(5):
                    if not data.get("error"):
                        break
                    missing_ledger = _missing_ledger_from_error(data["error"])
                    if not missing_ledger:
                        break
                    group, gstin = _guess_ledger_group_and_gstin(
                        missing_ledger, request.voucher_type, request.transactions
                    )
                    logger.info(
                        "Auto-creating missing ledger '%s' (%s) before retrying push",
                        missing_ledger, group,
                    )
                    upsert = await tally_connector.ensure_ledgers_exist(
                        request.company,
                        [{"name": missing_ledger, "parent_group": group, "gstin": gstin}],
                    )
                    if not (upsert.get("created") or upsert.get("already_exists")):
                        break
                    data = await tally_connector.push_xml(request.xml, company=request.company)
                if data.get("error"):
                    return JSONResponse(
                        {"success": False, "error": data["error"], "data": data},
                        status_code=422,
                    )
                if data.get("errors", 0) or data.get("exception", 0):
                    error_msg = data.get("error") or "Tally rejected the XML payload"
                    return JSONResponse(
                        {"success": False, "error": error_msg, "data": data},
                        status_code=422,
                    )
                return {
                    "success": True,
                    "message": "Successfully pushed transactions to Tally",
                    "data": data,
                }
            except Exception as direct_exc:
                logger.warning("Direct Tally push unavailable; using Electron bridge: %s", direct_exc)

        # Do not enqueue a request that cannot be completed. The bridge sends
        # the latest Tally reachability with every heartbeat, so an offline
        # status is a definitive fast-fail instead of a 180-second timeout.
        bridge_status = manager._status.get(user_data["uid"], {})
        if user_data["uid"] not in manager.electron_connections:
            if is_local_tally:
                return JSONResponse(
                    {
                        "success": False,
                        "error": "Tally is offline or not reachable at http://127.0.0.1:9000. Start Tally, open the selected company, and try again.",
                    },
                    status_code=503,
                )
            return JSONResponse(
                {
                    "success": False,
                    "error": "Desktop bridge is not connected. Open the EazyBooks desktop app and try again.",
                },
                status_code=503,
            )
        if bridge_status.get("tally") is False:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Tally is offline. Start Tally and open the selected company, then try again.",
                },
                status_code=503,
            )
        
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        
        # Create future for this request
        future = asyncio.Future()
        manager.pending_requests[request_id] = future
        
        try:
            await manager.send_message(user_data["uid"], {
                "type": "push-to-tally",
                "request_id": request_id,
                "company": request.company,
                "data": request.xml
            })
            
            # Keep a small margin over Electron's 120-second Tally request timeout.
            response = await asyncio.wait_for(future, timeout=130)
            logger.info(f"Received push-to-tally response from client {user_data['uid']}")
            
            # Extract and validate the data
            if "error" in response and response["error"]:
                raise HTTPException(status_code=500, detail=response["error"])

            data = response.get("data", {})

            # Check for Tally-level errors in the response data. Tally reports
            # one missing ledger at a time, so retry once per ledger it
            # surfaces (party, tax, purchase/sales account, ...).
            if data.get("error"):
                for _ in range(5):
                    missing_ledger = _missing_ledger_from_error(data["error"])
                    if not missing_ledger:
                        break
                    group, gstin = _guess_ledger_group_and_gstin(
                        missing_ledger, request.voucher_type, request.transactions
                    )
                    logger.info(
                        "Auto-creating missing ledger '%s' (%s) via desktop bridge before retrying push",
                        missing_ledger, group,
                    )
                    try:
                        await manager.create_tally_ledger(user_data["uid"], {
                            "name": missing_ledger,
                            "parent_group": group,
                            "company": request.company,
                            "gstin": gstin,
                        })
                        retry_request_id = str(uuid.uuid4())
                        retry_future = asyncio.Future()
                        manager.pending_requests[retry_request_id] = retry_future
                        try:
                            await manager.send_message(user_data["uid"], {
                                "type": "push-to-tally",
                                "request_id": retry_request_id,
                                "company": request.company,
                                "data": request.xml,
                            })
                            retry_response = await asyncio.wait_for(retry_future, timeout=130)
                        finally:
                            manager.pending_requests.pop(retry_request_id, None)
                        if retry_response.get("error"):
                            break
                        data = retry_response.get("data", {})
                        if not data.get("error"):
                            break
                    except Exception as ledger_exc:
                        logger.warning("Auto-create missing ledger '%s' failed: %s", missing_ledger, ledger_exc)
                        break
                if data.get("error"):
                    logger.error(
                        "Tally push error: %s | Full response data: %s",
                        data["error"], json.dumps(data, default=str)[:2000],
                    )
                    return JSONResponse(
                        {"success": False, "error": data["error"], "data": data},
                        status_code=422,
                    )
            if data.get("exception", 0) > 0:
                error_msg = data.get("error") or f"Tally reported {data['exception']} exception(s)"
                logger.error(
                    "Tally push exception: %s | Full response data: %s | Raw Tally XML: %s",
                    error_msg, json.dumps(data, default=str)[:2000], data.get("raw", "")[:3000],
                )
                return JSONResponse(
                    {"success": False, "error": error_msg, "data": data},
                    status_code=422,
                )

            # Record learning data from structured transactions (fire-and-forget)
            if request.transactions:
                try:
                    from applicationca.services.tally_history_service import record_from_push_payload
                    record_from_push_payload(
                        user_id=user_data["uid"],
                        company_name=request.company,
                        voucher_type=request.voucher_type,
                        transactions=[t.model_dump() for t in request.transactions],
                    )
                except Exception as learn_exc:
                    logger.warning("History recording failed (non-blocking): %s", learn_exc)

            return {
                "success": True,
                "message": "Successfully pushed transactions to Tally",
                "data": data
            }

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for push-to-tally response from client {user_data['uid']}")
            raise HTTPException(status_code=504, detail="Timeout waiting for Tally response")
            
        except Exception as e:
            logger.error(f"Error pushing to Tally: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error pushing to Tally: {str(e)}")
            
        finally:
            # Clean up the pending request
            manager.pending_requests.pop(request_id, None)
            
    except Exception as e:
        logger.error(f"Error pushing to Tally: {str(e)}")
        return JSONResponse(
            {"error": f"Error pushing to Tally: {str(e)}"},
            status_code=500
        )

@router.get("/get-companies", dependencies=[Depends(require_permission("tally.read"))])
async def get_companies(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get list of companies from Tally.

    Live fetch when the bridge is online; fall back to the cached list so a
    user with no current Tally session still sees previously-seen companies
    and can start uploading drafts.
    """
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    uid = user_data["uid"]

    def _cache_fallback():
        cached = tally_cache_store.list_cached_companies(user_id=uid)
        return [{"name": c["company_name"]} for c in cached]

    try:
        companies = await manager.get_tally_companies(uid)
        if not companies:
            companies = await tally_connector.get_companies()
        try:
            tally_cache_store.upsert_companies(user_id=uid, companies=companies)
        except Exception:
            pass
        return {"success": True, "companies": companies, "from_cache": False}
    except HTTPException as e:
        if e.status_code != 404:
            logger.error(f"Error getting companies from Tally: {e.detail}")
        try:
            companies = await tally_connector.get_companies()
            if companies:
                tally_cache_store.upsert_companies(user_id=uid, companies=companies)
                return {"success": True, "companies": companies, "from_cache": False}
        except Exception as direct_exc:
            logger.warning(f"Direct Tally company lookup failed: {direct_exc}")
        fallback = _cache_fallback()
        if fallback:
            return {"success": True, "companies": fallback, "from_cache": True}
        return JSONResponse(
            {
                "error": "Electron client not connected and no cached companies found.",
                "from_cache": False,
                "companies": [],
            },
            status_code=200,
        )
    except Exception as e:
        logger.error(f"Error getting companies from Tally: {str(e)}")
        try:
            companies = await tally_connector.get_companies()
            if companies:
                tally_cache_store.upsert_companies(user_id=uid, companies=companies)
                return {"success": True, "companies": companies, "from_cache": False}
        except Exception as direct_exc:
            logger.warning(f"Direct Tally company lookup failed: {direct_exc}")
        fallback = _cache_fallback()
        if fallback:
            return {"success": True, "companies": fallback, "from_cache": True}
        raise HTTPException(
            status_code=500,
            detail=f"Error getting companies from Tally: {str(e)}"
        )


@router.get("/get-cached-companies", dependencies=[Depends(require_permission("tally.read"))])
async def get_cached_companies(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Return the persisted list of companies the user has seen before,
    regardless of current Tally connectivity. Used as a snappy upload-modal
    bootstrap and a hard fallback when /get-companies returns nothing."""
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)
    cached = tally_cache_store.list_cached_companies(user_id=user_data["uid"])
    return {
        "success": True,
        "companies": [
            {
                "name": c["company_name"],
                "last_seen_at": c["last_seen_at"].isoformat() if c.get("last_seen_at") else None,
                "ledgers_synced_at": c["ledgers_synced_at"].isoformat() if c.get("ledgers_synced_at") else None,
            }
            for c in cached
        ],
    }


@router.get("/get-cached-ledgers", dependencies=[Depends(require_permission("tally.read"))])
async def get_cached_ledgers(
    company: str = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Return the last-known ledger list for a company, populated whenever
    /get-ledgers succeeds. Used by the draft editor so users can match
    ledgers offline."""
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)
    if not company:
        raise HTTPException(status_code=400, detail="company is required")
    cached = tally_cache_store.get_cached_ledgers(user_id=user_data["uid"], company_name=company)
    if not cached:
        return {"success": True, "ledgers": [], "refreshed_at": None, "from_cache": True}
    return {
        "success": True,
        "ledgers": cached.get("ledgers") or [],
        "refreshed_at": cached["refreshed_at"].isoformat() if cached.get("refreshed_at") else None,
        "from_cache": True,
    }


@router.get("/ledger-cache-status", dependencies=[Depends(require_permission("tally.read"))])
async def ledger_cache_status(
    company: str = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Tell the frontend whether we have a usable offline snapshot for this
    company and when it was last refreshed.

    Frontend uses this to decide (a) whether to surface a "Tally offline —
    showing cached suggestions" badge, and (b) whether to prompt the user
    to plug the desktop app back in (stale > 7d). Keeps the suggestion
    endpoint focused on per-request answers."""
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)
    if not company:
        raise HTTPException(status_code=400, detail="company is required")
    cached = tally_cache_store.get_cached_grouped_ledgers(
        user_id=user_data["uid"], company_name=company,
    )
    if not cached:
        return {
            "success": True,
            "has_cache": False,
            "refreshed_at": None,
        }
    refreshed = cached.get("grouped_refreshed_at")
    grouped = cached.get("grouped_ledgers") or {}
    party_count = sum(len(v or []) for v in (grouped.get("party") or {}).values())
    purchase_count = sum(len(v or []) for v in (grouped.get("purchase") or {}).values())
    sales_count = sum(len(v or []) for v in (grouped.get("sales") or {}).values())
    return {
        "success": True,
        "has_cache": True,
        "refreshed_at": refreshed.isoformat() if refreshed else None,
        "party_ledger_count": party_count,
        "purchase_ledger_count": purchase_count,
        "sales_ledger_count": sales_count,
    }

@router.get("/get-inventory", dependencies=[Depends(require_permission("tally.read"))])
async def get_inventory(
    company: str = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get inventory data from Tally."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
            
        # Get inventory from Tally through websocket
        try:
            inventory = await manager.get_tally_inventory(user_data["uid"], company)
            return {
                "success": True,
                "inventory": inventory
            }
        except HTTPException as e:
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected. Please ensure the desktop app is running."}, 
                    status_code=400
                )
            raise e
            
    except Exception as e:
        logger.error(f"Error getting inventory from Tally: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting inventory from Tally: {str(e)}"
        )

@router.get("/get-ledgers", dependencies=[Depends(require_permission("tally.read"))])
async def get_ledgers(
    company: str = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get list of ledgers from Tally."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Get ledgers from Tally through websocket; fall back to cache.
        try:
            ledgers = await manager.get_tally_ledgers(user_data["uid"], company)
            try:
                tally_cache_store.upsert_ledgers(
                    user_id=user_data["uid"],
                    company_name=company or "",
                    ledgers=ledgers or [],
                )
            except Exception:
                pass
            return {
                "success": True,
                "ledgers": ledgers,
                "from_cache": False,
            }
        except HTTPException as e:
            cached = None
            if company:
                cached = tally_cache_store.get_cached_ledgers(
                    user_id=user_data["uid"], company_name=company
                )
            if cached and cached.get("ledgers"):
                return {
                    "success": True,
                    "ledgers": cached.get("ledgers") or [],
                    "from_cache": True,
                    "refreshed_at": cached["refreshed_at"].isoformat() if cached.get("refreshed_at") else None,
                }
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected and no cached ledgers found.",
                     "ledgers": [], "from_cache": False},
                    status_code=200,
                )
            raise e

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting ledgers from Tally: {str(e)}")
        if company:
            cached = tally_cache_store.get_cached_ledgers(
                user_id=user_data["uid"], company_name=company
            )
            if cached and cached.get("ledgers"):
                return {
                    "success": True,
                    "ledgers": cached.get("ledgers") or [],
                    "from_cache": True,
                }
        raise HTTPException(
            status_code=500,
            detail=f"Error getting ledgers from Tally: {str(e)}"
        )


@router.get("/get-groups", dependencies=[Depends(require_permission("tally.read"))])
async def get_groups(
    company: str = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get list of groups from Tally."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Get groups from Tally through websocket
        try:
            groups = await manager.get_tally_groups(user_data["uid"], company)
            return {
                "success": True,
                "groups": groups
            }
        except HTTPException as e:
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected. Please ensure the desktop app is running."},
                    status_code=400
                )
            raise e

    except Exception as e:
        logger.error(f"Error getting groups from Tally: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting groups from Tally: {str(e)}"
        )

@router.get("/get-godowns", dependencies=[Depends(require_permission("tally.read"))])
async def get_godowns(
    company: str = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get list of godowns/warehouses from Tally via WebSocket bridge."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Get godowns from Tally through websocket
        try:
            godowns = await manager.get_tally_godowns(user_data["uid"], company)
            return {
                "success": True,
                "godowns": godowns
            }
        except HTTPException as e:
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected. Please ensure the desktop app is running."},
                    status_code=400
                )
            raise e

    except Exception as e:
        logger.error(f"Error getting godowns from Tally: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting godowns from Tally: {str(e)}"
        )


# ── Ledger create/alter payload contract ──────────────────────────────────
# These are the keys the WS bridge (Electron) and the direct connector both
# understand. All fields besides name/parent_group/company are optional.
# Field names match snake_case in the request; the bridge/connector convert
# to Tally's XML tag names.
_LEDGER_OPTIONAL_KEYS = [
    "opening_balance",
    # identity
    "alias", "mailing_name",
    # address
    "address", "address_lines", "pincode", "state", "country",
    # contact
    "contact_person", "phone", "mobile", "fax", "email", "email_cc",
    # statutory
    "pan", "gstin", "gst_registration_type", "gst_applicable_from",
    # bill-wise & credit
    "bill_wise", "bill_credit_period", "credit_limit",
    # bank (only for Bank Accounts parent group)
    "bank_name", "account_number", "ifsc_code", "payment_favouring",
    # behaviour flags
    "affects_stock", "cost_centres_applicable",
    # notes
    "narration", "description",
]


def _build_ledger_payload(request: dict) -> dict:
    """Extract a normalized ledger payload from a dict request, dropping
    keys the caller didn't set so the WS bridge / connector can distinguish
    'omitted' from 'explicit empty'."""
    payload = {
        "name": request["name"],
        "parent_group": request["parent_group"],
        "company": request["company"],
    }
    for key in _LEDGER_OPTIONAL_KEYS:
        if key in request and request[key] is not None and request[key] != "":
            payload[key] = request[key]
    return payload


@router.post("/create-ledger", dependencies=[Depends(require_permission("tally.push"))])
async def create_ledger(
    request: dict,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Create a new ledger in Tally via the desktop WebSocket bridge.

    Accepts all ledger fields documented in _LEDGER_OPTIONAL_KEYS. Empty /
    missing optional fields are dropped before forwarding, so the bridge
    only receives values the user actually provided.
    """
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Validate required fields
        required_fields = ["name", "parent_group", "company"]
        for field in required_fields:
            if field not in request:
                return JSONResponse(
                    {"error": f"Missing required field: {field}"},
                    status_code=400
                )

        payload = _build_ledger_payload(request)

        # Send create ledger request through websocket
        try:
            result = await manager.create_tally_ledger(user_data["uid"], payload)
            return {
                "success": True,
                "message": "Ledger created successfully",
                "ledger": payload,
            }
        except HTTPException as e:
            if e.status_code == 404:
                # The desktop bridge is preferred, but local Tally installations
                # are also reachable directly from the backend. This keeps the
                # web dashboard usable when Electron is not running.
                try:
                    await tally_connector.create_ledger(**payload)
                    return {
                        "success": True,
                        "message": "Ledger created successfully",
                        "ledger": payload,
                        "connection": "direct",
                    }
                except Exception as direct_error:
                    logger.error(
                        "Electron bridge unavailable and direct Tally ledger creation failed: %s",
                        direct_error,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "Tally is not reachable. Start Tally on the configured "
                            "TALLY_URL or start the desktop app."
                        ),
                    ) from direct_error
            raise e

    except HTTPException:
        # Preserve the original status (401 invalid token, 504 timeout, etc.)
        raise
    except Exception as e:
        logger.error(f"Error creating ledger in Tally: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error creating ledger in Tally: {str(e)}"
        )

@router.post("/create-inventory", dependencies=[Depends(require_permission("tally.push"))])
async def create_inventory(
    request: dict,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Create a new inventory item in Tally."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Validate required fields
        required_fields = ["name", "parent", "baseUnits"]
        for field in required_fields:
            if field not in request:
                return JSONResponse(
                    {"error": f"Missing required field: {field}"},
                    status_code=400
                )

        # Send create inventory request through websocket
        try:
            result = await manager.create_tally_inventory(
                user_data["uid"],
                {
                    "name": request["name"],
                    "parent": request["parent"],
                    "category": request.get("category", ""),
                    "baseUnits": request["baseUnits"],
                    "openingBalance": request.get("openingBalance", "0"),
                    "openingValue": request.get("openingValue", "0"),
                    "description": request.get("description", ""),
                    "godown": request.get("godown", "")
                }
            )
            return {
                "success": True,
                "message": "Inventory item created successfully",
                "inventory": {
                    "name": request["name"],
                    "parent": request["parent"],
                    "category": request.get("category", ""),
                    "baseUnits": request["baseUnits"],
                    "openingBalance": request.get("openingBalance", "0"),
                    "openingValue": request.get("openingValue", "0"),
                    "description": request.get("description", ""),
                    "godown": request.get("godown", "")
                }
            }
        except HTTPException as e:
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected. Please ensure the desktop app is running."},
                    status_code=400
                )
            raise e

    except Exception as e:
        logger.error(f"Error creating inventory item in Tally: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error creating inventory item in Tally: {str(e)}"
        )

@router.put("/edit-ledger", dependencies=[Depends(require_permission("tally.push"))])
async def edit_ledger(
    request: dict,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Edit an existing ledger in Tally.
    
    IMPORTANT: The current_name field MUST contain the exact name as it appears in Tally.
    For ledger names containing line breaks, use HTML/XML encoding (&#13;&#10;) instead of literal line breaks.
    Using an incorrect name format will result in creating a new ledger instead of editing the existing one.
    """
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Validate required fields
        required_fields = ["name", "parent_group", "company", "current_name"]
        for field in required_fields:
            if field not in request:
                return JSONResponse(
                    {"error": f"Missing required field: {field}"},
                    status_code=400
                )

        # Ensure we have either new_name or name
        if "new_name" not in request and "name" not in request:
            return JSONResponse(
                {"error": "Either new_name or name is required for editing a ledger"},
                status_code=400
            )

        # Handle line breaks in current_name - ensure they are properly encoded
        current_name = request["current_name"].replace("\r\n", "&#13;&#10;").replace("\n", "&#13;&#10;")
        
        # Send edit ledger request through websocket
        try:
            result = await manager.edit_tally_ledger(
                user_data["uid"],
                {
                    "current_name": current_name,  # Current name in Tally with encoded line breaks
                    "new_name": request.get("new_name") or request["name"],  # New name to change to
                    "name": request["name"],
                    "parent_group": request["parent_group"],
                    "company": request["company"],
                    "opening_balance": request.get("opening_balance", "0"),
                    "address": request.get("address", ""),
                    "country": request.get("country", ""),
                    "state": request.get("state", ""),
                    "mobile": request.get("mobile", ""),
                    "gstin": request.get("gstin", "")
                }
            )
            return {
                "success": True,
                "message": "Ledger updated successfully",
                "ledger": {
                    "name": request.get("new_name") or request["name"],  # Use the new name in response
                    "parent_group": request["parent_group"],
                    "company": request["company"],
                    "opening_balance": request.get("opening_balance", "0"),
                    "address": request.get("address", ""),
                    "country": request.get("country", ""),
                    "state": request.get("state", ""),
                    "mobile": request.get("mobile", ""),
                    "gstin": request.get("gstin", "")
                }
            }
        except HTTPException as e:
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected. Please ensure the desktop app is running."},
                    status_code=400
                )
            raise e

    except Exception as e:
        logger.error(f"Error editing ledger in Tally: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error editing ledger in Tally: {str(e)}"
        )

@router.post("/create-group", dependencies=[Depends(require_permission("tally.push"))])
async def create_group(
    request: dict,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Create a new group in Tally."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Validate required fields
        required_fields = ["name", "company"]
        for field in required_fields:
            if field not in request:
                return JSONResponse(
                    {"error": f"Missing required field: {field}"},
                    status_code=400
                )

        # Send create group request through websocket
        try:
            result = await manager.create_tally_group(
                user_data["uid"],
                {
                    "name": request["name"],
                    "parent_group": request.get("parent_group"),
                    "company": request["company"],
                    "nature": request.get("nature"),
                    "under": request.get("under")
                }
            )
            return {
                "success": True,
                "message": "Group created successfully",
                "group": {
                    "name": request["name"],
                    "parent_group": request.get("parent_group"),
                    "nature": request.get("nature"),
                    "under": request.get("under")
                }
            }
        except HTTPException as e:
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected. Please ensure the desktop app is running."},
                    status_code=400
                )
            raise e

    except Exception as e:
        logger.error(f"Error creating group in Tally: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error creating group in Tally: {str(e)}"
        )

def remove_sensitive_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Remove sensitive columns like 'guid' and 'user_id' from DataFrame."""
    sensitive_columns = ['guid', 'user_id']
    return df.drop(columns=[col for col in sensitive_columns if col in df.columns])

@router.websocket("/ws/tally-chat")
async def tally_chat_websocket(websocket: WebSocket):
    """WebSocket endpoint for Tally chat functionality"""
    try:
        # Get the JWT token from Authorization header
        auth_header = websocket.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            await websocket.close(code=4001, reason="No token provided")
            return

        # Extract the token
        token = auth_header.split(" ")[1]
        if not token:
            await websocket.close(code=4001, reason="No token provided")
            return

        # Verify JWT token
        try:
            user_data = verify_jwt(token)
            if not user_data:
                await websocket.close(code=4003, reason="Invalid token")
                return
        except Exception as e:
            logger.error(f"Error verifying JWT token: {str(e)}")
            await websocket.close(code=4003, reason="Invalid token")
            return

        user_id = user_data.get("uid")
        logger.info(f"Extracted user_id from JWT: {user_id}")

        await websocket.accept()
        logger.info(f"Tally chat client connected with user ID: {user_id}")

        # Create a new Vanna instance for this connection
        connection_vn = TallyVanna(config={
            'api_key': settings.OPENAI_API_KEY,
            'model': settings.OPENAI_MODEL
        })

        # Connect to PostgreSQL
        db = settings.db_components
        connection_vn.connect_to_postgres(
            host=db["host"],
            dbname=db["dbname"],
            user=db["user"],
            password=db["password"],
            port=db["port"],
            sslmode='require'
        )

        # Initialize Vanna with training data
        try:
            train_tally_structure(connection_vn)
            logger.info("Successfully initialized Vanna with Tally structure")
        except Exception as e:
            logger.error(f"Error initializing Vanna: {str(e)}")
            await websocket.close(code=4000, reason="Failed to initialize chat system")
            return

        # Set the user_id for this connection's Vanna instance
        logger.info(f"Setting user_id in connection's Vanna instance: {user_id}")
        connection_vn.set_user_id(user_id)

        while True:
            try:
                # Wait for messages
                message_data = await websocket.receive_json()
                
                # Extract message text and company name
                if isinstance(message_data, dict):
                    message = message_data.get("message", "")
                    company = message_data.get("company", "")
                    
                    # Set company name for this query
                    if company:
                        connection_vn.company_name = company
                        logger.info(f"Set company name for query: {company}")
                else:
                    # Fallback for plain text messages
                    message = await websocket.receive_text()
                
                # Process the message using this connection's Vanna instance
                result, status_message, sql = execute_with_fallback(message, vanna_instance=connection_vn)
                
                # Convert DataFrame to JSON-compatible format and remove sensitive columns
                if isinstance(result, pd.DataFrame):
                    # Remove sensitive columns before converting to JSON
                    result = remove_sensitive_columns(result)
                    result_json = result.to_json(orient='records', date_format='iso')
                    result_dict = json.loads(result_json)
                else:
                    result_dict = None

                # Send response back
                await websocket.send_text(json.dumps({
                    "status": status_message,
                    "sql": sql,
                    "result": result_dict
                }, cls=CustomJSONEncoder))

            except WebSocketDisconnect:
                logger.info(f"Tally chat client disconnected: {user_id}")
                break
            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                await websocket.send_json({
                    "error": f"Error processing message: {str(e)}"
                })

    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        try:
            await websocket.close(code=4000, reason=str(e))
        except:
            pass


class ChatRequest(BaseModel):
    message: str
    company: str
    # Pin a chat session. Omit to reuse the latest thread for (user, company)
    # so history survives page reloads; the frontend can store the thread_id
    # returned in the first `meta` event.
    thread_id: Optional[str] = None
    # Optional client-supplied history. When absent, the server hydrates from
    # the persisted thread.
    history: Optional[List[Dict[str, str]]] = None


class TallySyncRequest(BaseModel):
    company: str
    # "incremental" (default) auto-detects the date range and does a complete
    # initial pull when there's no cursor — the same path the analytics "first
    # sync" uses. "full" reconciles against a fixed historical window, so we
    # avoid it as a default (it would miss data newer than that window).
    mode: Optional[str] = "incremental"


@router.post("/tally-chat/sync", dependencies=[Depends(require_permission("chat.use"))])
async def tally_chat_sync(
    request: TallySyncRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Sync a company's Tally data into the Postgres tables backing TallyChat.

    Runs through the desktop bridge (tally-database-loader) — the same production
    path the analytics page uses. We deliberately do NOT fetch from Tally over
    direct HTTP here: the cloud backend can't reach the user's local Tally, so a
    direct pull fails in production ("All connection attempts failed") even though
    voucher push works (push already goes through this bridge).

    Returns immediately with a "started" ack — the loader run can take minutes.
    The frontend polls /ca/tally/analytics/sync-status for progress, then reads
    the now-populated mst_/trn_ tables.
    """
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    user_id = user_data.get("uid")
    company = request.company
    if not company:
        return JSONResponse(
            {"success": False, "error": "company is required"}, status_code=400
        )
    mode = request.mode if request.mode in ("incremental", "full") else "incremental"
    try:
        if user_id not in manager.electron_connections:
            raise HTTPException(status_code=404, detail="Desktop app not connected")
        from applicationca.services.tally_db_sync import sync_masters_via_bridge

        result = await sync_masters_via_bridge(user_id, company)
        if not result.get("success"):
            return JSONResponse(
                {"success": False, "error": result.get("error") or "Sync failed"},
                status_code=502,
            )
        return JSONResponse(
            {
                "success": True,
                "started": True,
                "completed": True,
                "company": company,
                "mode": mode,
                "counts": result.get("counts", {}),
            }
        )
    except HTTPException as exc:
        # Desktop bridge offline / loader unavailable / timeout — surface cleanly
        # so the chat UI can tell the user to open the desktop app with Tally running.
        return JSONResponse(
            {"success": False, "error": exc.detail}, status_code=exc.status_code
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"tally-chat sync trigger failed: {exc}")
        return JSONResponse(
            {"success": False, "error": "Could not reach the desktop bridge"},
            status_code=502,
        )


@router.post("/tally-chat", dependencies=[Depends(require_permission("chat.use"))])
async def tally_chat(
        request: ChatRequest,
        credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """SSE endpoint for Tally chat. Runs a ReAct agent (list_tables /
    describe_table / run_sql tool-loop) and emits NDJSON events:
      - {type: "status",       content}
      - {type: "tool_call",    name, arguments}
      - {type: "tool_result",  name, content}
      - {type: "sql",          content, purpose, rejected?}
      - {type: "result",       sql, content, row_count, columns, truncated?, error?}
      - {type: "answer_delta", content}         streaming tokens of final answer
      - {type: "error",        content}
      - {type: "done"}
    """
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    user_id = user_data.get("uid")
    company = request.company
    question = request.message
    logger.info(f"Tally chat agent — user={user_id} company={company}")

    thread_id = request.thread_id or tally_chat_store.get_or_create_latest_thread(
        user_id=user_id, company_name=company,
    )

    # Hydrate history from the DB when the frontend didn't pass one. This is
    # what makes a fresh page load keep prior turns in context.
    history = request.history
    if not history:
        rows = tally_chat_store.load_thread(
            user_id=user_id, company_name=company, thread_id=thread_id,
        )
        history = [{"role": r["role"], "content": r["content"]} for r in rows if r.get("content")]

    # Record the new user turn before streaming starts so it survives even if
    # the assistant blows up mid-answer.
    tally_chat_store.append_message(
        user_id=user_id, company_name=company, thread_id=thread_id,
        role="user", content=question,
    )

    def emit(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, cls=CustomJSONEncoder) + "\n\n"

    async def event_generator():
        final_answer_parts: list[str] = []
        try:
            yield emit({"type": "meta", "thread_id": thread_id})
            async for event in run_tally_agent(user_id, company, question, history=history):
                if event.get("type") == "answer_delta":
                    final_answer_parts.append(event.get("content") or "")
                yield emit(event)
        except Exception as exc:
            logger.error(f"Tally chat agent error: {exc}")
            yield emit({"type": "error", "content": str(exc)})
            yield emit({"type": "done"})
        finally:
            answer = "".join(final_answer_parts).strip()
            if answer:
                tally_chat_store.append_message(
                    user_id=user_id, company_name=company, thread_id=thread_id,
                    role="assistant", content=answer,
                )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/tally-chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def tally_chat_history(
    company: str,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the chat history for (user, company, thread). If thread_id is
    omitted, returns the latest thread for the user/company."""
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)
    user_id = user_data.get("uid")
    tid = thread_id or tally_chat_store.get_or_create_latest_thread(
        user_id=user_id, company_name=company,
    )
    messages = tally_chat_store.load_thread(
        user_id=user_id, company_name=company, thread_id=tid,
    )
    return {"thread_id": tid, "messages": messages}


@router.delete("/tally-chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def tally_chat_clear(
    company: str,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Clear messages for (user, company, thread). If no thread_id is given,
    clears the latest one — frontend treats this as 'start a fresh chat'."""
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)
    user_id = user_data.get("uid")
    tid = thread_id or tally_chat_store.get_or_create_latest_thread(
        user_id=user_id, company_name=company,
    )
    tally_chat_store.clear_thread(
        user_id=user_id, company_name=company, thread_id=tid,
    )
    return {"ok": True, "thread_id": tid}

@router.get("/get-unique-companies", dependencies=[Depends(require_permission("tally.read"))])
async def get_unique_companies(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get list of unique company names from database for the authenticated user."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
            
        user_id = user_data.get("uid")
        logger.info(f"Fetching unique companies for user ID: {user_id}")
        
        # Query to get unique company names
        query = """
        SELECT DISTINCT company_name 
        FROM mst_ledger 
        WHERE user_id = %s 
        AND company_name IS NOT NULL 
        AND company_name != ''
        ORDER BY company_name;
        """
        
        try:
            # Execute the query
            result = execute_query(query, (user_id,))
            
            # Extract company names from the result
            companies = [row['company_name'] for row in result] if result else []
            
            return {
                "success": True,
                "companies": companies,
                "count": len(companies)
            }
            
        except Exception as e:
            logger.error(f"Database error while fetching companies: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Database error: {str(e)}"
            )
            
    except Exception as e:
        logger.error(f"Error getting unique companies: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting unique companies: {str(e)}"
        )

@router.get("/get-ledger-details", dependencies=[Depends(require_permission("tally.read"))])
async def get_ledger_details(
    ledger_name: str,
    company: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get details of a specific ledger from Tally."""
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Validate required parameters
        if not ledger_name or not company:
            return JSONResponse(
                {"error": "Both ledger_name and company are required"},
                status_code=400
            )

        # Send get ledger details request through websocket
        try:
            result = await manager.get_tally_ledger_details(
                user_data["uid"],
                {
                    "ledger_name": ledger_name,
                    "company": company
                }
            )

            if not result or not result.get("ledger"):
                return JSONResponse(
                    {"error": "Ledger not found"},
                    status_code=404
                )

            # Get the ledger details from the result
            ledger_details = result["ledger"]
            logger.info(f"Ledger details from Tally: {ledger_details}")

            # Return the exact structure that was received
            return {
                "success": True,
                "ledger": ledger_details
            }

        except HTTPException as e:
            if e.status_code == 404:
                return JSONResponse(
                    {"error": "Electron client not connected. Please ensure the desktop app is running."},
                    status_code=400
                )
            raise e

    except Exception as e:
        logger.error(f"Error getting ledger details from Tally: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting ledger details from Tally: {str(e)}"
        )


class AISuggestRequest(BaseModel):
    document_type: str
    extracted_text: str = ""
    transactions: List[Dict[str, Any]]
    company: str
    available_ledgers: List[str] = []
    filename: str = ""


@router.post("/ai-suggest-fields", dependencies=[Depends(require_permission("tally.read"))])
async def ai_suggest_fields(
    request: AISuggestRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Use AI to suggest values for empty/missing fields based on document text
    and available ledger data.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Lazy import to avoid circular deps and only load when needed
        try:
            from google import genai as _genai
            from applicationca.agents.tally_agent import GOOGLE_API_KEY, SELECTED_GEMINI_MODEL
        except ImportError:
            return {"suggestions": [], "error": "Gemini not available"}

        if not GOOGLE_API_KEY:
            return {"suggestions": [], "error": "No Google API key configured"}

        # Optionally search Pinecone for similar past entries
        past_context = ""
        try:
            from applicationca.contoller.vector import get_text_embedding, index as pinecone_index
            if request.extracted_text:
                embedding = get_text_embedding(request.extracted_text[:500])
                results = pinecone_index.query(
                    vector=embedding,
                    top_k=3,
                    include_metadata=True,
                    filter={"user_id": user_data["uid"]}
                )
                if results and results.matches:
                    past_entries = []
                    for match in results.matches:
                        meta = match.metadata or {}
                        past_entries.append(meta.get("text", "")[:300])
                    past_context = "\n\nSimilar past entries:\n" + "\n---\n".join(past_entries)
        except Exception as e:
            logger.warning(f"Pinecone lookup failed (non-fatal): {e}")

        # Build transactions summary highlighting empty fields
        tx_summary = []
        for i, tx in enumerate(request.transactions):
            empty_fields = []
            for key, val in tx.items():
                if key == "index":
                    continue
                if val is None or val == "" or val == 0:
                    empty_fields.append(key)
                elif isinstance(val, list):
                    for j, item in enumerate(val):
                        if isinstance(item, dict):
                            for ik, iv in item.items():
                                if iv is None or iv == "" or iv == 0:
                                    empty_fields.append(f"{key}[{j}].{ik}")
            if empty_fields:
                tx_summary.append(f"Transaction {i}: {json.dumps(tx, default=str)}\n  Empty fields: {', '.join(empty_fields)}")

        if not tx_summary:
            return {"suggestions": []}

        prompt = f"""You are a Tally accounting assistant. Analyze the document text and transaction data below, then suggest values for empty/missing fields.

Document type: {request.document_type}
Company: {request.company}
Filename: {request.filename}

Available ledgers: {', '.join(request.available_ledgers[:50])}

Document text (excerpt):
{request.extracted_text[:2000]}
{past_context}

Transactions with empty fields:
{chr(10).join(tx_summary[:10])}

For each empty field, suggest a value based on the document text, available ledgers, and accounting conventions.

Return a JSON array of suggestions. Each suggestion should have:
- "transaction_index": integer (the transaction index)
- "field": string (the field name, e.g. "debitLedger", "creditLedger", "partyName", "voucherNumber")
- "suggested_value": string (the suggested value - must be from available_ledgers if it's a ledger field)
- "confidence": float between 0 and 1
- "reason": string (brief explanation)

Return ONLY the JSON array, no markdown formatting or extra text. Example:
[{{"transaction_index": 0, "field": "debitLedger", "suggested_value": "Office Expenses", "confidence": 0.8, "reason": "Narration mentions office supplies"}}]

If no suggestions can be made, return an empty array: []"""

        client = _genai.Client(api_key=GOOGLE_API_KEY)
        response = client.models.generate_content(
            model=SELECTED_GEMINI_MODEL,
            contents=prompt
        )

        # Parse the response
        response_text = response.text.strip()
        # Strip markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            response_text = response_text.strip()

        try:
            suggestions = json.loads(response_text)
            if not isinstance(suggestions, list):
                suggestions = []
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse AI suggestions JSON: {response_text[:200]}")
            suggestions = []

        # Validate suggestions
        valid_suggestions = []
        for s in suggestions:
            if all(k in s for k in ("transaction_index", "field", "suggested_value", "confidence")):
                s["confidence"] = max(0, min(1, float(s.get("confidence", 0.5))))
                s.setdefault("reason", "")
                valid_suggestions.append(s)

        return {"suggestions": valid_suggestions}

    except Exception as e:
        logger.error(f"Error generating AI suggestions: {str(e)}")
        return {"suggestions": [], "error": str(e)}


# ============================================
# MAPPING RULES ENDPOINTS
# ============================================

class MappingRuleCreate(BaseModel):
    rule_name: str
    rule_type: str  # keyword | pattern | vendor | amount_range | composite
    document_type: str  # bank_statement | purchase_invoice | sales_invoice
    target_ledger: str
    keywords: Optional[List[str]] = None
    pattern: Optional[str] = None
    vendor_name: Optional[str] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    target_voucher_type: Optional[str] = None
    # 'party' fills the party/vendor slot. 'expense' fills the
    # expense/revenue slot. None means "either" — only kept for backward
    # compat with rules created before the slot column existed.
    ledger_slot: Optional[str] = None
    priority: Optional[int] = 0
    conditions: Optional[Dict[str, Any]] = None  # composite rules only

    def __init__(self, **data):
        # Convert empty strings to None for optional fields before validation
        for field in ['pattern', 'vendor_name', 'target_voucher_type', 'ledger_slot']:
            if field in data and data[field] == '':
                data[field] = None
        for field in ['amount_min', 'amount_max']:
            if field in data and (data[field] == '' or data[field] is None):
                data[field] = None
        if 'priority' in data and (data['priority'] == '' or data['priority'] is None):
            data['priority'] = 0
        super().__init__(**data)


class MappingRuleUpdate(BaseModel):
    rule_name: Optional[str] = None
    rule_type: Optional[str] = None
    keywords: Optional[List[str]] = None
    pattern: Optional[str] = None
    vendor_name: Optional[str] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    target_ledger: Optional[str] = None
    target_voucher_type: Optional[str] = None
    ledger_slot: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    conditions: Optional[Dict[str, Any]] = None


@router.get("/mapping-rules", dependencies=[Depends(require_permission("tally.push"))])
async def get_mapping_rules(
    company: str,
    document_type: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get all mapping rules for a company."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.mapping_service import MappingService
        service = MappingService(user_data["uid"], company)
        rules = service.get_rules(document_type=document_type, active_only=False)

        return {"rules": rules or [], "total": len(rules or [])}

    except Exception as e:
        logger.error(f"Error getting mapping rules: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mapping-rules", dependencies=[Depends(require_permission("tally.push"))])
async def create_mapping_rule(
    company: str,
    rule: MappingRuleCreate,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Create a new mapping rule."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.mapping_service import MappingService
        service = MappingService(user_data["uid"], company)

        rule_id = service.create_rule(
            rule_name=rule.rule_name,
            rule_type=rule.rule_type,
            document_type=rule.document_type,
            target_ledger=rule.target_ledger,
            keywords=rule.keywords,
            pattern=rule.pattern,
            vendor_name=rule.vendor_name,
            amount_min=rule.amount_min,
            amount_max=rule.amount_max,
            target_voucher_type=rule.target_voucher_type,
            ledger_slot=rule.ledger_slot,
            priority=rule.priority or 0,
            conditions=rule.conditions,
        )

        if not rule_id:
            raise HTTPException(status_code=400, detail="Failed to create rule")

        return {"rule_id": str(rule_id), "message": "Rule created successfully"}

    except Exception as e:
        logger.error(f"Error creating mapping rule: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/mapping-rules/{rule_id}", dependencies=[Depends(require_permission("tally.push"))])
async def update_mapping_rule(
    rule_id: str,
    rule: MappingRuleUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Update an existing mapping rule."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.database.database_update import update_mapping_rule as db_update_rule

        # Build update dict from non-None values
        updates = {k: v for k, v in rule.model_dump().items() if v is not None}

        success = db_update_rule(rule_id, **updates)

        if not success:
            raise HTTPException(status_code=400, detail="Failed to update rule")

        return {"message": "Rule updated successfully"}

    except Exception as e:
        logger.error(f"Error updating mapping rule: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/mapping-rules/{rule_id}", dependencies=[Depends(require_permission("tally.push"))])
async def delete_mapping_rule(
    rule_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Delete a mapping rule."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.database.database_update import delete_mapping_rule as db_delete_rule

        success = db_delete_rule(rule_id)

        if not success:
            raise HTTPException(status_code=400, detail="Failed to delete rule")

        return {"message": "Rule deleted successfully"}

    except Exception as e:
        logger.error(f"Error deleting mapping rule: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mapping-rules/apply", dependencies=[Depends(require_permission("tally.push"))])
async def apply_mapping_rules(
    company: str,
    document_type: str,
    transactions: List[Dict[str, Any]],
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Apply mapping rules to a list of transactions."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.mapping_service import MappingService
        service = MappingService(user_data["uid"], company)

        processed = service.apply_rules(transactions, document_type)
        rules_applied = sum(1 for t in processed if t.get('rule_applied'))

        return {
            "transactions": processed,
            "rules_applied_count": rules_applied
        }

    except Exception as e:
        logger.error(f"Error applying mapping rules: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mapping-rules/statistics", dependencies=[Depends(require_permission("tally.read"))])
async def get_rule_statistics(
    company: str,
    document_type: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get statistics about mapping rules."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.mapping_service import MappingService
        service = MappingService(user_data["uid"], company)

        return service.get_rule_statistics(document_type)

    except Exception as e:
        logger.error(f"Error getting rule statistics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mapping-rules/suggestions", dependencies=[Depends(require_permission("tally.read"))])
async def get_rule_suggestions(
    company: str,
    document_type: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Mined rule proposals from observed history + correction patterns.

    Delegates to rule_mining_service, which mines tally_transaction_history
    and ledger_suggestion_feedback for four pattern types: high-consistency
    vendor → ledger, correction patterns, GSTIN-prefix → expense ledger,
    and amount-band × voucher → expense ledger.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.rule_mining_service import suggest_rules
        suggestions = suggest_rules(user_data["uid"], company, document_type)

        return {"suggestions": suggestions}

    except Exception as e:
        logger.error(f"Error getting rule suggestions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# BANK STATEMENT IMPORT ENDPOINTS
# ============================================

class BankStatementUploadRequest(BaseModel):
    company: str
    bank_ledger: Optional[str] = None
    apply_rules: Optional[bool] = True


@router.post("/bank-statement/upload", dependencies=[Depends(require_permission("tally.push"))])
async def upload_bank_statement(
    files: List[UploadFile] = File(...),
    company: str = Form(...),
    bank_ledger: Optional[str] = Form(None),
    apply_rules: bool = Form(True),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Upload and process bank statement files.

    Supports: CSV, Excel, PDF, XML formats.
    Returns extracted transactions with duplicate detection and confidence scores.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.import_service import ImportService
        import_service = ImportService(user_data["uid"], company)

        # Create import batch
        file_info = [{"filename": f.filename, "size": f.size} for f in files]
        batch_id = import_service.create_batch('bank_statement', file_info)

        if not batch_id:
            raise HTTPException(status_code=500, detail="Failed to create import batch")

        all_transactions = []
        all_results = []

        # Process each file
        for file in files:
            # Save to temp file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1])
            try:
                content = await file.read()
                temp_file.write(content)
                temp_file.close()

                # Get ledgers from Tally
                try:
                    ledgers = await manager.get_tally_ledgers(user_data["uid"], company)
                except:
                    ledgers = []

                # Process file through tally_agent
                result = await tally_agent(
                    file_path=temp_file.name,
                    company_name=company,
                    available_ledgers=ledgers,
                    user_id=user_data.get("uid"),
                )

                # Extract transactions from result
                if result.get('transactions'):
                    for t in result['transactions']:
                        t['original_filename'] = file.filename
                        if bank_ledger:
                            t['bank_ledger'] = bank_ledger
                    all_transactions.extend(result['transactions'])

            finally:
                os.unlink(temp_file.name)

        # Process transactions through import service
        if all_transactions:
            import hashlib
            file_hash = hashlib.sha256(str(file_info).encode()).hexdigest()

            original_filename = files[0].filename if len(files) == 1 else "multiple_files"

            result = import_service.process_bank_statement(
                batch_id=str(batch_id),
                transactions=all_transactions,
                original_filename=original_filename,
                file_hash=file_hash,
                apply_rules=apply_rules
            )

            # Save processed transactions to the database
            saved_ids = import_service.save_transactions(
                batch_id=str(batch_id),
                transactions=result['transactions'],
                document_type='bank_statement',
                original_filename=original_filename,
                file_hash=file_hash
            )
            logger.info(f"Saved {len(saved_ids)} transactions to database for batch {batch_id}")

            return {
                "batch_id": str(batch_id),
                "transactions": result['transactions'],
                "statistics": {
                    "total": result['total'],
                    "processed": result['processed'],
                    "duplicates": result['duplicates'],
                    "potential_duplicates": result['potential_duplicates'],
                    "rules_applied": result['rules_applied'],
                    "saved": len(saved_ids)
                }
            }

        return {
            "batch_id": str(batch_id),
            "transactions": [],
            "statistics": {"total": 0, "processed": 0, "duplicates": 0, "potential_duplicates": 0, "rules_applied": 0}
        }

    except Exception as e:
        logger.error(f"Error processing bank statement: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bank-statement/preview/{batch_id}", dependencies=[Depends(require_permission("tally.read"))])
async def preview_bank_statement_batch(
    batch_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get transactions from a bank statement batch for preview/editing."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.import_service import ImportService
        import_service = ImportService(user_data["uid"], "")

        batch = import_service.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        transactions = import_service.get_batch_transactions(batch_id)

        return {
            "batch": batch,
            "transactions": transactions
        }

    except Exception as e:
        logger.error(f"Error getting batch preview: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class BulkLedgerUpdateRequest(BaseModel):
    import_ids: List[str]
    debit_ledger: Optional[str] = None
    credit_ledger: Optional[str] = None


@router.put("/bank-statement/transactions", dependencies=[Depends(require_permission("tally.push"))])
async def update_bank_statement_transactions(
    company: str,
    request: BulkLedgerUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Bulk update ledger assignments for bank statement transactions."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.import_service import ImportService
        import_service = ImportService(user_data["uid"], company)

        success = import_service.update_transaction_ledgers(
            request.import_ids,
            debit_ledger=request.debit_ledger,
            credit_ledger=request.credit_ledger
        )

        if not success:
            raise HTTPException(status_code=400, detail="Failed to update transactions")

        return {"message": f"Updated {len(request.import_ids)} transactions"}

    except Exception as e:
        logger.error(f"Error updating transactions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bank-statement/check-duplicates", dependencies=[Depends(require_permission("tally.read"))])
async def check_bank_statement_duplicates(
    company: str,
    transactions: List[Dict[str, Any]],
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Check a list of transactions for duplicates."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.agents.document_specific_functions.common.duplicate_detection import DuplicateDetector

        detector = DuplicateDetector(user_data["uid"], company)
        results = detector.check_transactions_batch(transactions)

        # Convert to list format
        duplicate_info = []
        for idx, info in results.items():
            duplicate_info.append({
                "index": idx,
                **info
            })

        duplicates_count = sum(1 for r in results.values() if r['is_exact_duplicate'])
        potential_count = sum(1 for r in results.values() if r['fuzzy_matches'] and not r['is_exact_duplicate'])

        return {
            "results": duplicate_info,
            "duplicates_count": duplicates_count,
            "potential_duplicates_count": potential_count
        }

    except Exception as e:
        logger.error(f"Error checking duplicates: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# PURCHASE INVOICE IMPORT ENDPOINTS
# ============================================

async def _extract_purchase_openai_fallback(
    file_path: str,
    extracted_text: str,
    openai_api_key: str,
    openai_model: str = "gpt-4.1-mini",
) -> List[Dict[str, Any]]:
    """OpenAI fallback for purchase invoice extraction (mirrors sales fallback)."""
    import json as _json

    logger.info("Attempting purchase invoice extraction via OpenAI (fallback)")

    # Convert PDF first page to image for OpenAI vision (using PyMuPDF like sales flow)
    import base64 as _b64
    image_b64 = None
    file_ext = os.path.splitext(file_path)[1].lower()

    if file_ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        with open(file_path, 'rb') as f:
            image_b64 = _b64.b64encode(f.read()).decode('utf-8')
    elif file_ext == '.pdf':
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            page = doc[0]
            pix = page.get_pixmap(dpi=200)
            image_b64 = _b64.b64encode(pix.tobytes("png")).decode('utf-8')
            doc.close()
            logger.info("Converted PDF first page to image for OpenAI vision")
        except ImportError:
            logger.warning("PyMuPDF not installed, falling back to text-only extraction")
        except Exception as e:
            logger.warning(f"PDF to image conversion failed: {e}")

    # Use OpenAI directly (same pattern as sales _extract_with_openai)
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=openai_api_key)

    # Build prompt — rich extraction like sales
    prompt = """Extract ALL details from this purchase invoice document.

Return a JSON object with this exact structure:
{
    "vendor_details": {
        "vendor_name": "supplier/vendor name",
        "dates": ["DD/MM/YYYY"],
        "place_of_supply": "state name",
        "gst_number": "GSTIN if found",
        "invoice_number": "invoice/bill number",
        "due_date": "DD/MM/YYYY or empty"
    },
    "line_items": [
        {
            "description": "item/service description",
            "hsn_code": "HSN/SAC code if shown",
            "quantity": numeric_value,
            "rate": numeric_value,
            "amount": numeric_value,
            "gst_rate": numeric_percent
        }
    ],
    "tax_entries": [
        {
            "tax_name": "CGST/SGST/IGST/etc",
            "percentage": numeric_value,
            "amount": numeric_value
        }
    ],
    "totals": {
        "subtotal": numeric_value,
        "total_tax": numeric_value,
        "total_amount": numeric_value,
        "narration": "any notes on the invoice"
    },
    "confidence": {
        "vendor_name": 0.0-1.0,
        "invoice_date": 0.0-1.0,
        "invoice_number": 0.0-1.0,
        "gst_number": 0.0-1.0,
        "amounts": 0.0-1.0
    }
}

RULES:
- Use 0 for missing numeric values, "" for missing text
- Extract ALL line items visible on the invoice
- Separate tax entries (CGST, SGST, IGST) from item costs
- Dates in DD/MM/YYYY format
- Return ONLY valid JSON, no markdown
- You MUST extract at least the vendor name, date, and total amount"""

    if extracted_text and extracted_text.strip():
        prompt += f"\n\nDocument text content:\n{extracted_text[:8000]}"

    content_parts = []
    if image_b64:
        content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}", "detail": "high"}})
    content_parts.append({"type": "text", "text": prompt})

    # Use gpt-4o for vision (like sales), fallback model for text-only
    model_to_use = "gpt-4.1" if image_b64 else openai_model

    response = await client.chat.completions.create(
        model=model_to_use,
        temperature=0.1,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": "You are an expert purchase invoice data extractor. Return only valid JSON."},
            {"role": "user", "content": content_parts},
        ],
    )
    resp_text = response.choices[0].message.content.strip()
    logger.info(f"OpenAI purchase extraction raw response length: {len(resp_text)}")

    # Parse JSON
    if "```json" in resp_text:
        resp_text = resp_text.split("```json")[1].split("```")[0].strip()
    elif "```" in resp_text:
        resp_text = resp_text.split("```")[1].split("```")[0].strip()

    data = _json.loads(resp_text)

    # Transform into the flat transaction format expected by the endpoint normalizer
    vendor = data.get("vendor_details", {})
    line_items = data.get("line_items", [])
    tax_entries = data.get("tax_entries", [])
    totals = data.get("totals", {})
    confidence = data.get("confidence", {})

    vendor_name = vendor.get("vendor_name", "")
    dates = vendor.get("dates", [])
    invoice_date = dates[0] if dates else ""
    invoice_number = vendor.get("invoice_number", "")
    gstin = vendor.get("gst_number", "")
    place_of_supply = vendor.get("place_of_supply", "")

    subtotal = float(totals.get("subtotal", 0))
    total_tax = float(totals.get("total_tax", 0))
    total_amount = float(totals.get("total_amount", 0))

    # Compute GST percentage from tax entries if possible
    gst_pct = 0
    if tax_entries and subtotal > 0:
        gst_pct = round(total_tax / subtotal * 100, 1)

    # Build a single transaction with all the rich data
    tx = {
        'type': 'purchase',
        'date': invoice_date,
        'description': totals.get("narration", "") or (line_items[0].get("description", "") if line_items else ""),
        'reference_number': invoice_number,
        'place_of_supply': place_of_supply,
        'amount': subtotal or (total_amount - total_tax),
        'gst_percentage': gst_pct,
        'gst_amount': total_tax,
        'total_amount': total_amount or (subtotal + total_tax),
        'credit_ledger': vendor_name or 'Cash',
        'debit_ledger': 'Purchase',
        # Extra fields for the frontend
        'vendor_name': vendor_name,
        'vendor_gstin': gstin,
        'invoice_number': invoice_number,
        'items': [{
            "description": item.get("description", ""),
            "hsn_code": item.get("hsn_code", ""),
            "quantity": float(item.get("quantity", 0)),
            "rate": float(item.get("rate", 0)),
            "amount": float(item.get("amount", 0)),
            "gst_rate": float(item.get("gst_rate", 0)),
        } for item in line_items],
        'tax_entries': [{
            "tax_name": te.get("tax_name", ""),
            "percentage": float(te.get("percentage", 0)),
            "amount": float(te.get("amount", 0)),
        } for te in tax_entries],
        'confidence_scores': confidence,
    }

    logger.info(f"OpenAI purchase extraction successful: vendor={vendor_name}, items={len(line_items)}, total={total_amount}")
    return [tx]


@router.post("/purchase-invoice/upload", dependencies=[Depends(require_permission("tally.push"))])
async def upload_purchase_invoices(
    files: List[UploadFile] = File(...),
    company: str = Form(...),
    apply_rules: bool = Form(True),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Upload and process purchase invoice files.

    Supports: PDF, images (JPG, PNG), Excel formats.
    Returns extracted invoice data with confidence scores.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Try to create import batch in DB (optional — works without DB)
        import_service = None
        batch_id = None
        try:
            from applicationca.services.import_service import ImportService
            import_service = ImportService(user_data["uid"], company)
            file_info = [{"filename": f.filename, "size": f.size} for f in files]
            batch_id = import_service.create_batch('purchase_invoice', file_info)
        except Exception as db_err:
            logger.warning(f"Database unavailable for import batch, proceeding without: {db_err}")
            batch_id = str(uuid.uuid4())

        all_invoices = []
        import base64

        # Fetch ledgers and inventory once (not per-file)
        # Try direct HTTP first, fall back to WebSocket bridge for production
        ledgers_data = []
        inventory_names_list = []
        try:
            ledgers_data = await tally_connector.get_ledgers(company) or []
            inventory_data = await tally_connector.get_inventory(company) or []
            inventory_names_list = [i.get("name", "") for i in inventory_data]
        except Exception:
            pass

        # Fallback: if direct connection failed, use WebSocket bridge
        firebase_uid = user_data.get("uid")
        if not ledgers_data and firebase_uid and firebase_uid in manager.electron_connections:
            try:
                ledgers_data = await manager.get_tally_ledgers(firebase_uid, company) or []
                logger.info(f"Fetched {len(ledgers_data)} ledgers via WebSocket for purchase upload")
            except Exception as ws_err:
                logger.warning(f"WebSocket ledger fetch also failed: {ws_err}")

        # Reuse the same high-quality extraction as sales (Gemini + OpenAI fallback + ledger matching)
        from applicationca.agents.document_specific_functions.sales import extract_sales_invoice_structured

        inventory_names_with_units = [
            {"name": i.get("name", ""), "unit": i.get("baseUnits", "Nos")}
            for i in (await tally_connector.get_inventory(company) or []) if i.get("name")
        ] if inventory_names_list else []

        for file in files:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1])
            try:
                content = await file.read()
                temp_file.write(content)
                temp_file.close()

                pdf_b64 = base64.b64encode(content).decode('utf-8')
                from utils.file_helper import get_mime_type as _get_mime_type
                file_mime = _get_mime_type(file.filename)

                # Use the same extraction pipeline as sales — it handles Gemini, OpenAI fallback, ledger matching
                invoice_data = await extract_sales_invoice_structured(
                    file_path=temp_file.name,
                    company=company,
                    gemini_model="gemini-2.5-pro",
                    google_api_key=settings.GEMINI_API_KEY,
                    openai_api_key=settings.OPENAI_API_KEY,
                    openai_model=settings.OPENAI_MODEL,
                    available_ledgers=ledgers_data,
                    inventory_names=inventory_names_list,
                    inventory_names_with_units=inventory_names_with_units,
                    voucher_type="purchase",
                    anthropic_api_key=settings.ANTHROPIC_API_KEY,
                    extraction_provider=settings.EXTRACTION_PROVIDER,
                )

                # Transform the sales-format extraction into purchase-format transaction
                header = invoice_data.get("header", {})
                items = invoice_data.get("items", [])
                taxes = invoice_data.get("taxes", [])
                summary = invoice_data.get("summary", {})

                subtotal = float(summary.get("subtotal", 0))
                tax_amount = float(summary.get("tax_amount", 0))
                total_amount = float(summary.get("total_amount", 0))

                # Compute GST % from tax/subtotal
                gst_pct = round(tax_amount / subtotal * 100, 1) if subtotal > 0 else 0

                tx = {
                    "date": header.get("voucher_date", ""),
                    "invoice_number": header.get("invoice_number", ""),
                    "vendor_name": header.get("party_name", ""),
                    "vendor_gstin": header.get("party_gstin", ""),
                    "place_of_supply": header.get("place_of_supply", ""),
                    "description": summary.get("narration", ""),
                    "amount": subtotal,
                    "gst_percentage": gst_pct,
                    "gst_amount": tax_amount,
                    "total_amount": total_amount,
                    "credit_ledger": header.get("party_name", ""),
                    "debit_ledger": "Purchase",
                    "items": [{
                        "description": it.get("item_name", ""),
                        "hsn_code": "",
                        "quantity": float(it.get("quantity", 0)),
                        "rate": float(it.get("rate", 0)),
                        "amount": float(it.get("amount", 0)),
                        "gst_rate": 0,
                        "unit": it.get("unit", "Nos"),
                    } for it in items],
                    "tax_entries": [{
                        "ledger_name": t.get("ledger_name", ""),
                        "tax_name": t.get("ledger_name", ""),
                        "description": t.get("description", ""),
                        "percentage": float(t.get("percentage", 0)),
                        "amount": float(t.get("amount", 0)),
                        "ai_suggestion": t.get("ai_suggestion"),
                    } for t in taxes],
                    "confidence_scores": header.get("confidence", {}),
                    "original_filename": file.filename,
                    "pdf_data": pdf_b64,
                    "file_mime": file_mime,
                }
                all_invoices.append(tx)

            finally:
                os.unlink(temp_file.name)

        # Process invoices through import service if DB is available
        if all_invoices and import_service:
            try:
                result = import_service.process_purchase_invoices(
                    batch_id=str(batch_id),
                    invoices=all_invoices,
                    apply_rules=apply_rules
                )
                return {
                    "batch_id": str(batch_id),
                    "invoices": result['invoices'],
                    "statistics": {
                        "total": result['total'],
                        "processed": result['processed'],
                        "duplicates": result['duplicates'],
                        "vendors_to_create": result['vendors_to_create'],
                        "items_to_create": result['items_to_create']
                    }
                }
            except Exception as proc_err:
                logger.warning(f"Import service processing failed, returning raw invoices: {proc_err}")

        # Fallback: return raw extracted invoices without DB processing
        # Normalize extracted fields for the frontend
        normalized = []
        for inv in all_invoices:
            # Handle both flat (Gemini) and rich (OpenAI) formats
            vendor_name = inv.get("vendor_name", "") or inv.get("credit_ledger", "")
            subtotal_val = float(inv.get("amount", 0) or 0)
            gst_val = float(inv.get("gst_amount", 0) or 0)
            total_val = float(inv.get("total_amount", 0) or 0)
            # If total is 0 but we have subtotal + gst, compute it
            if not total_val and (subtotal_val or gst_val):
                total_val = subtotal_val + gst_val
            # If subtotal is 0 but total > gst, back-compute
            if not subtotal_val and total_val:
                subtotal_val = total_val - gst_val

            normalized.append({
                "invoice_number": inv.get("invoice_number", "") or inv.get("reference_number", ""),
                "invoice_date": inv.get("date", ""),
                "vendor_name": vendor_name,
                "vendor_ledger": "",  # User selects from dropdown
                "vendor_gstin": inv.get("vendor_gstin", "") or inv.get("gstin", ""),
                "place_of_supply": inv.get("place_of_supply", ""),
                "description": inv.get("description", "") or inv.get("narration", ""),
                "subtotal": subtotal_val,
                "gst_percentage": float(inv.get("gst_percentage", 0) or 0),
                "gst_amount": gst_val,
                "tax_amount": gst_val,
                "total_amount": total_val,
                "items": inv.get("items", []),
                "tax_entries": inv.get("tax_entries", []),
                "original_filename": inv.get("original_filename", ""),
                "pdf_data": inv.get("pdf_data", ""),
                "file_mime": inv.get("file_mime", "application/pdf"),
                "confidence_scores": inv.get("confidence_scores", {}),
            })

        return {
            "batch_id": str(batch_id),
            "invoices": normalized,
            "statistics": {
                "total": len(normalized),
                "processed": len(normalized),
                "duplicates": 0,
                "vendors_to_create": 0,
                "items_to_create": 0
            }
        }

    except Exception as e:
        logger.error(f"Error processing purchase invoices: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


async def _upload_to_r2_async(file_bytes: bytes, r2_key: str, content_type: Optional[str] = None) -> Optional[str]:
    """Run the sync r2_service.upload_to_r2 in a worker thread so it doesn't
    block the async extraction. Returns the public URL on success, None on
    failure — R2 errors must never break the extraction stream."""
    try:
        from applicationca.services import r2_service
        return await asyncio.to_thread(r2_service.upload_to_r2, file_bytes, r2_key, content_type)
    except Exception as exc:
        logger.warning("R2 upload failed for key=%s (non-blocking): %s", r2_key, exc)
        return None


async def _extract_one_purchase_invoice(
    file_bytes: bytes,
    filename: str,
    company: str,
    ledgers_data: list,
    inventory_names_list: list,
    inventory_names_with_units: list,
    user_id: Optional[str] = None,
    batch_id: Optional[str] = None,
):
    """Extract a single purchase invoice. Used by both batch and streaming endpoints.

    When user_id + batch_id are passed, also kicks off an R2 upload in parallel
    with extraction so the file_url is available to persist on the draft (the
    base64 pdf_data is not stored — too big for the JSONB payload).
    """
    import base64
    from applicationca.agents.document_specific_functions.sales import extract_sales_invoice_structured
    from applicationca.services import r2_service
    from utils.file_helper import get_file_category, get_mime_type

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1])
    try:
        temp_file.write(file_bytes)
        temp_file.close()

        file_category = get_file_category(filename)
        file_b64 = base64.b64encode(file_bytes).decode('utf-8')
        file_mime = get_mime_type(filename)

        # Kick off the R2 upload alongside extraction. Falls back to None if
        # user_id/batch_id aren't available or the upload itself errors out.
        upload_task = None
        r2_key = None
        if user_id and batch_id:
            r2_key = r2_service.build_r2_key(user_id, "purchase", batch_id, filename)
            upload_task = asyncio.create_task(_upload_to_r2_async(file_bytes, r2_key, file_mime))

        invoice_data = await extract_sales_invoice_structured(
            file_path=temp_file.name,
            company=company,
            gemini_model="gemini-2.5-pro",
            google_api_key=settings.GEMINI_API_KEY,
            openai_api_key=settings.OPENAI_API_KEY,
            openai_model=settings.OPENAI_MODEL,
            available_ledgers=ledgers_data,
            inventory_names=inventory_names_list,
            inventory_names_with_units=inventory_names_with_units,
            voucher_type="purchase",
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            extraction_provider=settings.EXTRACTION_PROVIDER,
        )

        file_url = await upload_task if upload_task else None

        return {
            "header": invoice_data.get("header", {}),
            "items": invoice_data.get("items", []),
            "taxes": invoice_data.get("taxes", []),
            "summary": invoice_data.get("summary", {}),
            "original_filename": filename,
            "pdf_data": file_b64,
            "file_type": file_category,
            "file_mime": file_mime,
            "file_url": file_url,
            "r2_key": r2_key if file_url else None,
        }
    finally:
        try:
            os.unlink(temp_file.name)
        except Exception:
            pass


async def _extract_one_sales_invoice(
    file_bytes: bytes,
    filename: str,
    company: str,
    ledgers_data: list,
    inventory_names_list: list,
    inventory_names_with_units: list,
    user_id: Optional[str] = None,
    batch_id: Optional[str] = None,
):
    """Extract a single sales invoice. Used by the sales streaming endpoint.

    Same R2-upload-in-parallel pattern as the purchase variant.
    """
    import base64
    from applicationca.agents.document_specific_functions.sales import extract_sales_invoice_structured
    from applicationca.services import r2_service
    from utils.file_helper import get_file_category, get_mime_type

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1])
    try:
        temp_file.write(file_bytes)
        temp_file.close()

        file_category = get_file_category(filename)
        file_b64 = base64.b64encode(file_bytes).decode('utf-8')
        file_mime = get_mime_type(filename)

        upload_task = None
        r2_key = None
        if user_id and batch_id:
            r2_key = r2_service.build_r2_key(user_id, "sales", batch_id, filename)
            upload_task = asyncio.create_task(_upload_to_r2_async(file_bytes, r2_key, file_mime))

        invoice_data = await extract_sales_invoice_structured(
            file_path=temp_file.name,
            company=company,
            gemini_model="gemini-2.5-pro",
            google_api_key=settings.GEMINI_API_KEY,
            openai_api_key=settings.OPENAI_API_KEY,
            openai_model=settings.OPENAI_MODEL,
            available_ledgers=ledgers_data,
            inventory_names=inventory_names_list,
            inventory_names_with_units=inventory_names_with_units,
            voucher_type="sales",
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            extraction_provider=settings.EXTRACTION_PROVIDER,
        )

        file_url = await upload_task if upload_task else None

        return {
            "header": invoice_data.get("header", {}),
            "items": invoice_data.get("items", []),
            "taxes": invoice_data.get("taxes", []),
            "summary": invoice_data.get("summary", {}),
            "original_filename": filename,
            "pdf_data": file_b64,
            "file_type": file_category,
            "file_mime": file_mime,
            "file_url": file_url,
            "r2_key": r2_key if file_url else None,
        }
    finally:
        try:
            os.unlink(temp_file.name)
        except Exception:
            pass


@router.post("/sales-invoice/upload-stream")
async def upload_sales_invoices_stream(
    files: List[UploadFile] = File(...),
    company: str = Form(...),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Multi-file sales invoice upload with SSE streaming.

    Yields events:
      - {type: "batch_start", batch_id, total}
      - {type: "file_start", index, filename}
      - {type: "file_done", index, filename, invoice}
      - {type: "file_error", index, filename, error}
      - {type: "batch_end", batch_id, total, succeeded, failed}
    """
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    file_payloads = []
    for f in files:
        content = await f.read()
        file_payloads.append({"filename": f.filename, "bytes": content})

    batch_id = str(uuid.uuid4())

    ledgers_data = []
    inventory_names_list = []
    inventory_names_with_units = []
    try:
        ledgers_data = await tally_connector.get_ledgers(company) or []
        inventory_data = await tally_connector.get_inventory(company) or []
        inventory_names_list = [i.get("name", "") for i in inventory_data]
        inventory_names_with_units = [
            {"name": i.get("name", ""), "unit": i.get("baseUnits", "Nos")}
            for i in inventory_data if i.get("name")
        ]
    except Exception:
        pass

    firebase_uid = user_data.get("uid")
    if not ledgers_data and firebase_uid and firebase_uid in manager.electron_connections:
        try:
            ledgers_data = await manager.get_tally_ledgers(firebase_uid, company) or []
        except Exception as ws_err:
            logger.warning(f"WebSocket ledger fetch failed: {ws_err}")

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event, default=str)}\n\n"

    async def event_generator():
        yield sse({"type": "batch_start", "batch_id": batch_id, "total": len(file_payloads)})

        async def run_one(index: int, payload: dict):
            try:
                invoice = await _extract_one_sales_invoice(
                    file_bytes=payload["bytes"],
                    filename=payload["filename"],
                    company=company,
                    ledgers_data=ledgers_data,
                    inventory_names_list=inventory_names_list,
                    inventory_names_with_units=inventory_names_with_units,
                    user_id=firebase_uid,
                    batch_id=batch_id,
                )
                return ("ok", index, payload["filename"], invoice)
            except Exception as e:
                logger.exception(f"Sales invoice extraction failed for {payload['filename']}")
                return ("err", index, payload["filename"], str(e))

        tasks = [asyncio.create_task(run_one(i, p)) for i, p in enumerate(file_payloads)]

        for i, p in enumerate(file_payloads):
            yield sse({"type": "file_start", "index": i, "filename": p["filename"]})

        succeeded = 0
        failed = 0
        for coro in asyncio.as_completed(tasks):
            status, index, filename, result = await coro
            if status == "ok":
                succeeded += 1
                yield sse({"type": "file_done", "index": index, "filename": filename, "invoice": result})
            else:
                failed += 1
                yield sse({"type": "file_error", "index": index, "filename": filename, "error": result})

        yield sse({
            "type": "batch_end",
            "batch_id": batch_id,
            "total": len(file_payloads),
            "succeeded": succeeded,
            "failed": failed,
        })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/purchase-invoice/upload-stream")
async def upload_purchase_invoices_stream(
    files: List[UploadFile] = File(...),
    company: str = Form(...),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Multi-file purchase invoice upload with SSE streaming.

    Yields events:
      - {type: "batch_start", batch_id, total}
      - {type: "file_start", index, filename}
      - {type: "file_done", index, filename, invoice}
      - {type: "file_error", index, filename, error}
      - {type: "batch_end", batch_id, total, succeeded, failed}
    """
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    # Read all files up-front (UploadFile streams close after the request handler returns —
    # but the generator runs inside the same request, so reading here is safe and avoids
    # contention when tasks run concurrently).
    file_payloads = []
    for f in files:
        content = await f.read()
        file_payloads.append({"filename": f.filename, "bytes": content})

    batch_id = str(uuid.uuid4())

    # Fetch master data once
    ledgers_data = []
    inventory_names_list = []
    inventory_names_with_units = []
    try:
        ledgers_data = await tally_connector.get_ledgers(company) or []
        inventory_data = await tally_connector.get_inventory(company) or []
        inventory_names_list = [i.get("name", "") for i in inventory_data]
        inventory_names_with_units = [
            {"name": i.get("name", ""), "unit": i.get("baseUnits", "Nos")}
            for i in inventory_data if i.get("name")
        ]
    except Exception:
        pass

    firebase_uid = user_data.get("uid")
    if not ledgers_data and firebase_uid and firebase_uid in manager.electron_connections:
        try:
            ledgers_data = await manager.get_tally_ledgers(firebase_uid, company) or []
        except Exception as ws_err:
            logger.warning(f"WebSocket ledger fetch failed: {ws_err}")

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event, default=str)}\n\n"

    async def event_generator():
        yield sse({"type": "batch_start", "batch_id": batch_id, "total": len(file_payloads)})

        # Wrap each file extraction in a task that carries its index
        async def run_one(index: int, payload: dict):
            try:
                invoice = await _extract_one_purchase_invoice(
                    file_bytes=payload["bytes"],
                    filename=payload["filename"],
                    company=company,
                    ledgers_data=ledgers_data,
                    inventory_names_list=inventory_names_list,
                    inventory_names_with_units=inventory_names_with_units,
                    user_id=firebase_uid,
                    batch_id=batch_id,
                )
                return ("ok", index, payload["filename"], invoice)
            except Exception as e:
                logger.exception(f"Purchase invoice extraction failed for {payload['filename']}")
                return ("err", index, payload["filename"], str(e))

        tasks = [asyncio.create_task(run_one(i, p)) for i, p in enumerate(file_payloads)]

        # Announce file_start immediately for UI skeletons
        for i, p in enumerate(file_payloads):
            yield sse({"type": "file_start", "index": i, "filename": p["filename"]})

        succeeded = 0
        failed = 0
        for coro in asyncio.as_completed(tasks):
            status, index, filename, result = await coro
            if status == "ok":
                succeeded += 1
                yield sse({"type": "file_done", "index": index, "filename": filename, "invoice": result})
            else:
                failed += 1
                yield sse({"type": "file_error", "index": index, "filename": filename, "error": result})

        yield sse({
            "type": "batch_end",
            "batch_id": batch_id,
            "total": len(file_payloads),
            "succeeded": succeeded,
            "failed": failed,
        })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


@router.get("/purchase-invoice/preview/{batch_id}")
async def preview_purchase_invoice_batch(
    batch_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get invoices from a purchase invoice batch for preview/editing."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.import_service import ImportService
        import_service = ImportService(user_data["uid"], "")

        batch = import_service.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        invoices = import_service.get_batch_transactions(batch_id)

        return {
            "batch": batch,
            "invoices": invoices
        }

    except Exception as e:
        logger.error(f"Error getting batch preview: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class VendorMatchRequest(BaseModel):
    invoice_indices: List[int]
    vendor_name: str
    vendor_ledger: str
    create_if_not_exists: bool = False


@router.post("/purchase-invoice/match-vendors/{batch_id}")
async def match_purchase_invoice_vendors(
    batch_id: str,
    company: str,
    request: VendorMatchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Match or create vendor ledgers for purchase invoices."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # If create_if_not_exists, create the ledger in Tally first
        if request.create_if_not_exists:
            try:
                await manager.create_tally_ledger(
                    user_id=user_data["uid"],
                    company_name=company,
                    ledger_name=request.vendor_ledger,
                    parent_group="Sundry Creditors"
                )
            except Exception as e:
                logger.warning(f"Failed to create vendor ledger: {e}")

        # Update the transactions with the vendor ledger
        from applicationca.database.database_retrieval import get_transaction_imports_by_batch
        from applicationca.database.database_update import update_transaction_import

        transactions = get_transaction_imports_by_batch(batch_id)
        updated = 0

        for idx in request.invoice_indices:
            if idx < len(transactions):
                update_transaction_import(
                    str(transactions[idx]['import_id']),
                    credit_ledger=request.vendor_ledger
                )
                updated += 1

        return {"message": f"Updated {updated} invoices with vendor: {request.vendor_ledger}"}

    except Exception as e:
        logger.error(f"Error matching vendors: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class RecordCorrectionRequest(BaseModel):
    import_id: str
    field: str
    original_value: str
    corrected_value: str
    document_type: str


class GenerateBankXmlRequest(BaseModel):
    vouchers: List[Dict[str, Any]]
    company: str
    bank_ledger: str


@router.post("/generate-bank-xml")
async def generate_bank_xml(
    request: GenerateBankXmlRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Generate Tally XML for bank vouchers (Receipt/Payment/Contra) using the proven Tally format."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if not request.vouchers:
            return JSONResponse({"error": "No vouchers provided"}, status_code=400)

        xml_content = tally_connector.build_bank_vouchers_xml(
            company=request.company,
            bank_ledger=request.bank_ledger,
            transactions=request.vouchers,
        )

        if not xml_content:
            return JSONResponse({"error": "No valid vouchers to generate XML for"}, status_code=400)

        return {
            "success": True,
            "xml": xml_content,
            "voucher_count": len(request.vouchers)
        }

    except Exception as e:
        logger.error(f"Error generating bank XML: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# VALIDATE PRE-PUSH
# ============================================

class ValidatePrePushRequest(BaseModel):
    company: str
    bank_ledger: str = ""
    transactions: List[Dict[str, Any]]


@router.post("/validate-pre-push")
async def validate_pre_push(
    request: ValidatePrePushRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Validate transactions before pushing to Tally.
    Checks: ledgers exist, amounts valid, dates valid, bank ledger exists.
    Returns missing ledgers and any validation errors.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        errors = []
        warnings = []
        missing_ledgers = []
        skipped = []

        # Fetch existing ledgers from Tally
        try:
            existing_ledgers = await tally_connector.get_ledgers(request.company)
            ledger_names = {l["name"].strip().lower() for l in existing_ledgers}
        except Exception as e:
            logger.warning(f"Could not fetch ledgers from Tally: {e}")
            # If we can't connect to Tally, we can't validate ledgers
            return {
                "valid": False,
                "missing_ledgers": [],
                "errors": [f"Cannot connect to Tally to validate ledgers: {str(e)}"],
                "warnings": [],
                "skipped": [],
            }

        # Check bank ledger exists
        if request.bank_ledger and request.bank_ledger.strip().lower() not in ledger_names:
            errors.append(f"Bank ledger '{request.bank_ledger}' does not exist in Tally")

        # Check each transaction
        seen_party_ledgers = set()
        for i, tx in enumerate(request.transactions):
            party_ledger = tx.get("party_ledger", "").strip()
            amount = tx.get("amount", 0)
            date_str = tx.get("date", "")
            voucher_type = tx.get("voucher_type", "")

            # Validate amount
            try:
                amt = float(amount)
                if amt == 0:
                    skipped.append({"index": i, "reason": "Zero amount"})
                    continue
            except (ValueError, TypeError):
                errors.append(f"Transaction {i + 1}: Invalid amount '{amount}'")
                continue

            # Validate voucher type
            if voucher_type not in ("Receipt", "Payment", "Contra", "Journal"):
                errors.append(f"Transaction {i + 1}: Invalid voucher type '{voucher_type}'")

            # Validate party ledger
            if not party_ledger:
                errors.append(f"Transaction {i + 1}: Missing party ledger")
                continue

            # Check if party ledger exists
            if party_ledger.lower() not in ledger_names and party_ledger not in seen_party_ledgers:
                missing_ledgers.append(party_ledger)
                seen_party_ledgers.add(party_ledger)

            # Validate date
            if not date_str:
                errors.append(f"Transaction {i + 1}: Missing date")

        # Deduplicate missing ledgers
        missing_ledgers = list(dict.fromkeys(missing_ledgers))

        is_valid = len(errors) == 0 and len(missing_ledgers) == 0

        return {
            "valid": is_valid,
            "missing_ledgers": missing_ledgers,
            "errors": errors,
            "warnings": warnings,
            "skipped": skipped,
            "total_transactions": len(request.transactions),
            "valid_transactions": len(request.transactions) - len(skipped) - len([e for e in errors if "Missing party" in e or "Invalid amount" in e]),
        }

    except Exception as e:
        logger.error(f"Error validating pre-push: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# CREATE MISSING LEDGERS
# ============================================

class CreateMissingLedgersRequest(BaseModel):
    company: str
    ledgers: List[Dict[str, str]]  # [{"name": "...", "parent_group": "Sundry Debtors"}]


@router.post("/create-missing-ledgers")
async def create_missing_ledgers(
    request: CreateMissingLedgersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Create missing ledgers in Tally before pushing vouchers."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if not request.ledgers:
            return {"created": 0, "results": []}

        results = []
        created_count = 0
        failed_count = 0

        for ledger_info in request.ledgers:
            name = ledger_info.get("name", "").strip()
            parent_group = ledger_info.get("parent_group", "Sundry Debtors")

            if not name:
                results.append({"name": name, "success": False, "error": "Empty name"})
                failed_count += 1
                continue

            try:
                result = await tally_connector.create_ledger(
                    company=request.company,
                    name=name,
                    parent_group=parent_group,
                )
                success = result.get("success", False) or result.get("created", 0) > 0
                results.append({
                    "name": name,
                    "success": success,
                    "error": result.get("error", "") if not success else "",
                })
                if success:
                    created_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                results.append({"name": name, "success": False, "error": str(e)})
                failed_count += 1

        # Invalidate ledger cache since we created new ones
        tally_connector.invalidate_ledger_cache(request.company)

        return {
            "created": created_count,
            "failed": failed_count,
            "results": results,
        }

    except Exception as e:
        logger.error(f"Error creating missing ledgers: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# BATCH PUSH VOUCHERS (Direct to Tally)
# ============================================

class PushVouchersBatchRequest(BaseModel):
    company: str
    bank_ledger: str
    transactions: List[Dict[str, Any]]
    batch_size: int = 20
    auto_create_ledgers: bool = False


@router.post("/push-vouchers-batch")
async def push_vouchers_batch(
    request: PushVouchersBatchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Push bank vouchers directly to Tally via HTTP, in batches.

    This is the preferred method for pushing vouchers — it generates the correct
    XML format and sends it directly to Tally's HTTP API (no websocket/Electron needed).

    Set auto_create_ledgers=true to automatically create missing party ledgers
    before pushing vouchers (two-stage upsert pattern).
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if not request.transactions:
            return JSONResponse({"error": "No transactions provided"}, status_code=400)

        # Verify Tally is reachable
        try:
            company_info = await tally_connector.get_current_company()
            if not company_info.get("success"):
                return JSONResponse(
                    {"error": "Cannot connect to Tally. Ensure Tally Prime is running with XML Server enabled (F12 > Advanced Configuration > Enable ODBC Server > Yes)."},
                    status_code=503,
                )
            # Verify company matches — use Tally's actual company name to avoid silent failures
            current_company = company_info.get("company", "").strip()
            if current_company and current_company.lower() != request.company.strip().lower():
                logger.warning(
                    f"Company mismatch: requested '{request.company}', Tally has '{current_company}'. "
                    f"Using Tally's company name '{current_company}' to avoid silent import failures."
                )
                request.company = current_company
        except Exception as e:
            return JSONResponse(
                {"error": f"Cannot connect to Tally at {tally_connector.base_url}: {str(e)}"},
                status_code=503,
            )

        # Filter valid transactions
        valid_transactions = []
        skipped = []
        for i, tx in enumerate(request.transactions):
            party_ledger = tx.get("party_ledger", "").strip()
            try:
                amount = abs(float(tx.get("amount", 0)))
            except (ValueError, TypeError):
                skipped.append({"index": i, "reason": f"Invalid amount: {tx.get('amount')}"})
                continue

            if amount == 0:
                skipped.append({"index": i, "reason": "Zero amount"})
                continue
            if not party_ledger:
                skipped.append({"index": i, "reason": "Missing party ledger"})
                continue
            if not tx.get("voucher_type"):
                skipped.append({"index": i, "reason": "Missing voucher type"})
                continue
            if not tx.get("date", "").strip():
                skipped.append({"index": i, "reason": "Missing date"})
                continue

            valid_transactions.append(tx)

        # Log date values for debugging
        for i, tx in enumerate(valid_transactions[:3]):
            logger.info(f"Transaction {i} date='{tx.get('date')}' type='{tx.get('voucher_type')}' party='{tx.get('party_ledger')}'")

        logger.info(
            f"Push vouchers: {len(request.transactions)} total, "
            f"{len(valid_transactions)} valid, {len(skipped)} skipped"
        )
        if skipped:
            logger.warning(f"Skipped transactions: {skipped[:5]}")

        if not valid_transactions:
            return JSONResponse(
                {"error": f"No valid transactions to push. {len(skipped)} were skipped: {skipped[:5]}"},
                status_code=400,
            )

        # Auto-create missing ledgers if requested (two-stage upsert)
        upsert_results = None
        if request.auto_create_ledgers:
            required_ledgers = []
            seen = set()
            for tx in valid_transactions:
                party = tx.get("party_ledger", "").strip()
                if party and party.lower() not in seen:
                    seen.add(party.lower())
                    required_ledgers.append({
                        "name": party,
                        "parent_group": tx.get("party_ledger_group", "Sundry Debtors"),
                    })
            if required_ledgers:
                upsert_results = await tally_connector.ensure_ledgers_exist(
                    request.company, required_ledgers
                )
                logger.info(f"Upsert results: {upsert_results}")

        # Split into batches and push sequentially
        batch_size = min(max(request.batch_size, 1), 50)
        batches = [
            valid_transactions[i:i + batch_size]
            for i in range(0, len(valid_transactions), batch_size)
        ]

        total_created = 0
        total_altered = 0
        total_errors = 0
        total_combined = 0
        total_ignored = 0
        batch_results = []
        last_vch_id = ""
        all_line_errors = []

        for batch_idx, batch in enumerate(batches):
            xml = tally_connector.build_bank_vouchers_xml(
                company=request.company,
                bank_ledger=request.bank_ledger,
                transactions=batch,
            )

            if not xml:
                logger.error(f"Batch {batch_idx + 1}: Empty XML generated for {len(batch)} transactions")
                batch_results.append({
                    "batch": batch_idx + 1,
                    "success": False,
                    "error": "Empty XML generated",
                    "count": len(batch),
                })
                total_errors += len(batch)
                continue

            # DEBUG: Log XML details
            logger.info(f"Batch {batch_idx + 1}: Generated XML ({len(xml)} chars) for {len(batch)} transactions")
            logger.debug(f"Batch {batch_idx + 1} XML preview: {xml[:500]}...")

            # Validate XML structure
            xml_issues = []
            if "<ENVELOPE>" not in xml:
                xml_issues.append("Missing <ENVELOPE> tag")
            if request.company and request.company not in xml:
                xml_issues.append(f"Company name '{request.company}' not found in XML")
            if xml_issues:
                logger.warning(f"Batch {batch_idx + 1} XML issues: {xml_issues}")

            try:
                result = await tally_connector.push_xml(xml, company=request.company)

                # DEBUG: Log raw Tally response
                raw_response = result.get("raw", "")
                if raw_response:
                    logger.info(f"Batch {batch_idx + 1} Tally raw response: {raw_response[:1000]}")
                    if "LINEERROR" in raw_response:
                        logger.error(f"Batch {batch_idx + 1} contains LINEERROR(s)")
                    if "CREATED" in raw_response:
                        logger.info(f"Batch {batch_idx + 1}: Tally confirmed CREATED")

                created = result.get("created", 0)
                altered = result.get("altered", 0)
                errors = result.get("errors", 0)
                combined = result.get("combined", 0)
                ignored = result.get("ignored", 0)
                vch_id = result.get("lastvchid", "")

                total_created += created
                total_altered += altered
                total_errors += errors
                total_combined += combined
                total_ignored += ignored
                if vch_id:
                    last_vch_id = vch_id

                # Parse line errors from raw XML if present
                raw = result.get("raw", "")
                if raw and errors > 0:
                    try:
                        root = tally_connector._parse_xml(raw)
                        for le in root.iter("LINEERROR"):
                            if le.text:
                                all_line_errors.append(le.text.strip())
                    except Exception:
                        pass

                batch_results.append({
                    "batch": batch_idx + 1,
                    "success": result.get("success", False),
                    "created": created,
                    "altered": altered,
                    "errors": errors,
                    "combined": combined,
                    "ignored": ignored,
                    "lastvchid": vch_id,
                    "count": len(batch),
                    "tally_response": raw_response[:500] if raw_response else None,
                })

                # Stop if a batch fails completely
                if errors > 0 and created == 0 and altered == 0:
                    logger.error(f"Batch {batch_idx + 1} failed completely, stopping.")
                    remaining = sum(len(b) for b in batches[batch_idx + 1:])
                    total_errors += remaining
                    break

            except Exception as e:
                logger.error(f"Error pushing batch {batch_idx + 1}: {str(e)}")
                batch_results.append({
                    "batch": batch_idx + 1,
                    "success": False,
                    "error": str(e),
                    "count": len(batch),
                })
                total_errors += len(batch)
                break

        overall_success = total_created > 0 and total_errors == 0

        response = {
            "success": overall_success,
            "created": total_created,
            "altered": total_altered,
            "errors": total_errors,
            "combined": total_combined,
            "ignored": total_ignored,
            "last_voucher_id": last_vch_id,
            "line_errors": all_line_errors[:20],
            "total_transactions": len(request.transactions),
            "valid_transactions": len(valid_transactions),
            "skipped": skipped,
            "batches": batch_results,
            "batch_count": len(batches),
        }
        if upsert_results is not None:
            response["upsert"] = upsert_results
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in push-vouchers-batch: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# TALLY STATUS (Direct HTTP check with company)
# ============================================

@router.get("/tally/status")
async def tally_status():
    """
    Check Tally connection status and get current company name.
    No auth required — used as a health check by the frontend.
    """
    try:
        company_info = await tally_connector.get_current_company()
        connected = company_info.get("success", False)
        company_name = company_info.get("company", "")

        return {
            "connected": connected,
            "company": company_name,
            "tally_url": tally_connector.base_url,
            "message": f"Connected to Tally ({company_name})" if connected else "Tally is not reachable",
        }
    except Exception as e:
        return {
            "connected": False,
            "company": "",
            "tally_url": tally_connector.base_url,
            "message": str(e),
        }


# ============================================
# TALLY FILTERED LEDGERS (Direct HTTP)
# ============================================

@router.get("/tally/ledgers")
async def tally_filtered_ledgers(
    company: str,
    type: str = "all",
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Fetch ledgers from Tally with optional filtering.
    type: "party", "bank", or "all"
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        ledgers = await tally_connector.get_filtered_ledgers(company, type)
        return {"success": True, "ledgers": ledgers, "count": len(ledgers)}
    except Exception as e:
        logger.error(f"Error fetching filtered ledgers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tally/ledgers/refresh")
async def tally_refresh_ledgers(
    company: str = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Force refresh the ledger cache."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        tally_connector.invalidate_ledger_cache(company)
        return {"success": True, "message": "Ledger cache cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# CHECK DUPLICATES IN TALLY
# ============================================

class CheckTallyDuplicatesRequest(BaseModel):
    company: str
    transactions: List[Dict[str, Any]]


@router.post("/tally/check-duplicates")
async def check_tally_duplicates(
    request: CheckTallyDuplicatesRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Check if transactions already exist in Tally by comparing date + amount + narration.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if not request.transactions:
            return {"duplicates": [], "checked": 0}

        # Determine date range from transactions
        dates = []
        for tx in request.transactions:
            date_str = tx.get("date", "")
            tally_date = tally_connector._convert_date(date_str)
            if tally_date and len(tally_date) == 8:
                dates.append(tally_date)

        if not dates:
            return {"duplicates": [], "checked": 0}

        from_date = min(dates)
        to_date = max(dates)

        # Fetch recent vouchers from Tally
        try:
            existing = await tally_connector.get_recent_vouchers(
                request.company, from_date, to_date
            )
        except Exception as e:
            logger.warning(f"Could not fetch vouchers for duplicate check: {e}")
            return {"duplicates": [], "checked": 0, "error": str(e)}

        # Compare transactions. Primary match key per the product spec:
        #   (party_gstin, invoice_number) — different vendors with the same
        #     name can't collide because GSTINs are unique.
        # Fallback when GSTIN isn't extracted:
        #   (party_name, invoice_number) — Tally exposes PartyLedgerName on
        #     each voucher; party ledger names ARE allowed to overlap across
        #     real-world vendors, so this is intentionally weaker.
        # Date+amount equality is kept as a tertiary signal in case the
        # invoice number was edited but the same bill was pushed twice.
        def norm(v):
            return str(v or "").strip().lower()

        duplicates = []
        for i, tx in enumerate(request.transactions):
            tx_invoice = norm(tx.get("invoice_number") or tx.get("reference_number"))
            tx_party_gstin = str(tx.get("party_gstin") or "").strip().upper()
            tx_party_name = norm(tx.get("party_name") or tx.get("vendor_name"))
            tx_date = tally_connector._convert_date(tx.get("date", ""))
            try:
                tx_amount = abs(float(tx.get("amount", 0)))
            except (ValueError, TypeError):
                tx_amount = 0.0

            for ev in existing:
                # Tally stores invoice # in VOUCHERNUMBER (Sales) or REFERENCE
                # (Purchase) — probe both.
                ev_invoice = norm(ev.get("voucher_number") or ev.get("reference"))
                ev_party_name = norm(ev.get("party_ledger"))
                ev_date = (ev.get("date", "") or "").replace("-", "")
                try:
                    ev_amount = abs(float(ev.get("amount", "0")))
                except (ValueError, TypeError):
                    ev_amount = 0.0

                matched_by = None
                confidence = None

                # Strongest signal: same invoice # AND same party. Tally's voucher
                # collection doesn't expose the party's GSTIN directly (it's on
                # the ledger master), so we match against PartyLedgerName here.
                # If the bookkeeper rolls the vendor under a different ledger
                # name we'll miss — name match is best-effort by design.
                if tx_invoice and ev_invoice and tx_invoice == ev_invoice:
                    if tx_party_name and ev_party_name and tx_party_name == ev_party_name:
                        confidence = "high"
                        matched_by = "gstin_or_name+invoice"
                    elif not tx_party_name:
                        # User didn't extract a vendor name — match on invoice # alone.
                        confidence = "medium"
                        matched_by = "invoice_only"

                # Tertiary signal — same date + same amount even if invoice # differs.
                # Demoted to "low" so the UI can present it as informational.
                if not matched_by and tx_date == ev_date and abs(tx_amount - ev_amount) < 0.01 and tx_amount > 0:
                    confidence = "low"
                    matched_by = "date_amount"

                if matched_by:
                    duplicates.append({
                        "index": i,
                        "confidence": confidence,
                        "matched_by": matched_by,
                        "input": {
                            "invoice_number": tx.get("invoice_number") or tx.get("reference_number"),
                            "party_name": tx.get("party_name") or tx.get("vendor_name"),
                            "party_gstin": tx_party_gstin,
                            "date": tx.get("date"),
                            "amount": tx_amount,
                        },
                        "existing_voucher": {
                            "date": ev.get("date"),
                            "amount": ev.get("amount"),
                            "narration": ev.get("narration"),
                            "voucher_type": ev.get("voucher_type"),
                            "voucher_number": ev.get("voucher_number"),
                            "reference": ev.get("reference"),
                            "party_ledger": ev.get("party_ledger"),
                            "master_id": ev.get("master_id"),
                        },
                    })
                    break

        return {
            "duplicates": duplicates,
            "checked": len(request.transactions),
            "existing_vouchers_in_range": len(existing),
        }

    except Exception as e:
        logger.error(f"Error checking Tally duplicates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# ALTER / DELETE VOUCHER IN TALLY
# ============================================

class AlterVoucherRequest(BaseModel):
    company: str
    voucher_type: str
    date: str  # DD-Mon-YYYY for alteration
    master_id: str  # LASTVCHID from original push
    narration: Optional[str] = None
    ledger_entries: Optional[List[Dict[str, Any]]] = None


@router.post("/tally/alter-voucher")
async def tally_alter_voucher(
    request: AlterVoucherRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Alter an existing voucher in Tally using Master ID."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.alter_voucher(
            company=request.company,
            voucher_type=request.voucher_type,
            date=request.date,
            tag_name="Master ID",
            tag_value=request.master_id,
            ledger_entries=request.ledger_entries,
            narration=request.narration,
        )
        return result
    except Exception as e:
        logger.error(f"Error altering voucher: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class DeleteVoucherRequest(BaseModel):
    company: str
    voucher_type: str
    date: str  # DD-Mon-YYYY
    master_id: str


@router.post("/tally/delete-voucher")
async def tally_delete_voucher(
    request: DeleteVoucherRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Delete a voucher from Tally using Master ID."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.delete_voucher(
            company=request.company,
            voucher_type=request.voucher_type,
            date=request.date,
            tag_name="Master ID",
            tag_value=request.master_id,
        )
        return result
    except Exception as e:
        logger.error(f"Error deleting voucher: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/record-correction")
async def record_ai_correction(
    company: str,
    request: RecordCorrectionRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Record a user correction for AI learning."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        from applicationca.services.import_service import ImportService
        import_service = ImportService(user_data["uid"], company)

        feedback_id = import_service.record_correction(
            import_id=request.import_id,
            field=request.field,
            original_value=request.original_value,
            corrected_value=request.corrected_value,
            document_type=request.document_type
        )

        return {"feedback_id": str(feedback_id) if feedback_id else None}

    except Exception as e:
        print(f"Error recording correction: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ==============================================================================
#  DIRECT TALLY ENDPOINTS — bypass WebSocket/Electron, talk to Tally HTTP API
# ==============================================================================

# --- Connection ---

@router.get("/direct/check-connection")
async def direct_check_connection():
    """Check if Tally is reachable via direct HTTP (no auth needed for health check)."""
    try:
        connected = await tally_connector.check_connection()
        return {
            "connected": connected,
            "tally_url": tally_connector.base_url,
            "message": "Tally is connected and accessible" if connected else "Cannot reach Tally",
        }
    except Exception as e:
        logger.error(f"Direct Tally connection check failed: {e}")
        return {"connected": False, "error": str(e)}

# --- Export: Companies ---

@router.get("/direct/get-companies")
async def direct_get_companies(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Get companies directly from Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        companies = await tally_connector.get_companies()
        tally_cache_store.upsert_companies(user_id=user_data["uid"], companies=companies)
        return {"success": True, "companies": companies}
    except Exception as e:
        logger.error(f"Error getting companies from Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Export: Ledgers ---

@router.get("/direct/get-ledgers")
async def direct_get_ledgers(
    company: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Get all ledgers for a company directly from Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        ledgers = await tally_connector.get_ledgers(company)
        return {"success": True, "ledgers": ledgers}
    except Exception as e:
        logger.error(f"Error getting ledgers from Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Export: Groups ---

@router.get("/direct/get-groups")
async def direct_get_groups(
    company: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Get all groups for a company directly from Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        groups = await tally_connector.get_groups(company)
        return {"success": True, "groups": groups}
    except Exception as e:
        logger.error(f"Error getting groups from Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Export: Inventory ---

@router.get("/direct/get-inventory")
async def direct_get_inventory(
    company: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Get all stock items for a company directly from Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        inventory = await tally_connector.get_inventory(company)
        return {"success": True, "inventory": inventory}
    except Exception as e:
        logger.error(f"Error getting inventory from Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Export: Godowns ---

@router.get("/direct/get-godowns")
async def direct_get_godowns(
    company: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Get all godowns for a company directly from Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        godowns = await tally_connector.get_godowns(company)
        return {"success": True, "godowns": godowns}
    except Exception as e:
        logger.error(f"Error getting godowns from Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Export: Voucher Types ---

@router.get("/direct/get-voucher-types")
async def direct_get_voucher_types(
    company: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Get all voucher types for a company directly from Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        voucher_types = await tally_connector.get_voucher_types(company)
        return {"success": True, "voucher_types": voucher_types}
    except Exception as e:
        logger.error(f"Error getting voucher types from Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Export: Ledger Details ---

@router.get("/direct/get-ledger-details")
async def direct_get_ledger_details(
    company: str,
    ledger_name: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Get detailed info for a specific ledger directly from Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.get_ledger_details(company, ledger_name)
        return result
    except Exception as e:
        logger.error(f"Error getting ledger details from Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Export: Trial Balance ---

@router.get("/direct/trial-balance")
async def direct_trial_balance(
    company: str = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Export Trial Balance from Tally. Returns raw XML."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        raw_xml = await tally_connector.get_trial_balance(company)
        return {"success": True, "raw_xml": raw_xml}
    except Exception as e:
        logger.error(f"Error getting trial balance from Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Import: Push raw XML ---

class DirectPushRequest(BaseModel):
    xml: str
    company: Optional[str] = None

@router.post("/direct/push-xml")
async def direct_push_xml(
    request: DirectPushRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push raw XML directly to Tally (masters or vouchers)."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.push_xml(request.xml, company=request.company)
        return result
    except Exception as e:
        logger.error(f"Error pushing XML to Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Import: Create Ledger ---

class DirectCreateLedgerRequest(BaseModel):
    company: str
    name: str
    parent_group: str
    opening_balance: str = "0"
    # ── Identity ──
    alias: str = ""
    mailing_name: str = ""
    # ── Address ──
    address: str = ""
    address_lines: Optional[List[str]] = None
    pincode: str = ""
    state: str = ""
    country: str = "India"
    # ── Contact ──
    contact_person: str = ""
    phone: str = ""
    mobile: str = ""
    fax: str = ""
    email: str = ""
    email_cc: str = ""
    # ── Statutory ──
    pan: str = ""
    gstin: str = ""
    gst_registration_type: str = ""
    gst_applicable_from: str = ""
    # ── Bill-wise / credit ──
    bill_wise: Optional[bool] = None
    bill_credit_period: str = ""
    credit_limit: str = ""
    # ── Bank ──
    bank_name: str = ""
    account_number: str = ""
    ifsc_code: str = ""
    payment_favouring: str = ""
    # ── Behaviour flags ──
    affects_stock: Optional[bool] = None
    cost_centres_applicable: Optional[bool] = None
    # ── Notes ──
    narration: str = ""
    description: str = ""

@router.post("/direct/create-ledger")
async def direct_create_ledger(
    request: DirectCreateLedgerRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a new ledger directly in Tally (local-dev path; bypasses the WS bridge)."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.create_ledger(**request.dict())
        return result
    except Exception as e:
        logger.error(f"Error creating ledger in Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Import: Alter Ledger ---

class DirectAlterLedgerRequest(BaseModel):
    company: str
    current_name: str
    new_name: Optional[str] = None
    parent_group: Optional[str] = None
    opening_balance: Optional[str] = None
    alias: Optional[str] = None
    mailing_name: Optional[str] = None
    address: Optional[str] = None
    address_lines: Optional[List[str]] = None
    pincode: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    email: Optional[str] = None
    email_cc: Optional[str] = None
    pan: Optional[str] = None
    gstin: Optional[str] = None
    gst_registration_type: Optional[str] = None
    gst_applicable_from: Optional[str] = None
    bill_wise: Optional[bool] = None
    bill_credit_period: Optional[str] = None
    credit_limit: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    payment_favouring: Optional[str] = None
    affects_stock: Optional[bool] = None
    cost_centres_applicable: Optional[bool] = None
    narration: Optional[str] = None
    description: Optional[str] = None

@router.post("/direct/alter-ledger")
async def direct_alter_ledger(
    request: DirectAlterLedgerRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Alter an existing ledger directly in Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Forward only fields the caller explicitly set
        kwargs = {k: v for k, v in request.dict().items() if v is not None}
        result = await tally_connector.alter_ledger(**kwargs)
        return result
    except Exception as e:
        logger.error(f"Error altering ledger in Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Import: Create Group ---

class DirectCreateGroupRequest(BaseModel):
    company: str
    name: str
    parent_group: str

@router.post("/direct/create-group")
async def direct_create_group(
    request: DirectCreateGroupRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a new group directly in Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.create_group(
            company=request.company,
            name=request.name,
            parent_group=request.parent_group,
        )
        return result
    except Exception as e:
        logger.error(f"Error creating group in Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Import: Create Voucher ---

class VoucherLedgerEntry(BaseModel):
    ledger_name: str
    amount: str
    is_deemed_positive: str = "No"

class DirectCreateVoucherRequest(BaseModel):
    company: str
    voucher_type: str
    date: str  # YYYY-MM-DD format (XML standard ISO 8601)
    ledger_entries: List[VoucherLedgerEntry]
    narration: str = ""
    voucher_number: str = ""

@router.post("/direct/create-voucher")
async def direct_create_voucher(
    request: DirectCreateVoucherRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a voucher directly in Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        entries = [e.model_dump() for e in request.ledger_entries]
        result = await tally_connector.create_voucher(
            company=request.company,
            voucher_type=request.voucher_type,
            date=request.date,
            ledger_entries=entries,
            narration=request.narration,
            voucher_number=request.voucher_number,
        )
        return result
    except Exception as e:
        logger.error(f"Error creating voucher in Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Import: Import Vouchers (raw XML) ---

class DirectImportVouchersRequest(BaseModel):
    company: str
    voucher_xml: str  # Inner <VOUCHER>...</VOUCHER> XML

@router.post("/direct/import-vouchers")
async def direct_import_vouchers(
    request: DirectImportVouchersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Import voucher XML directly into Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.import_vouchers(
            request.voucher_xml, company=request.company
        )
        return result
    except Exception as e:
        logger.error(f"Error importing vouchers to Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Import: Import Masters (raw XML) ---

class DirectImportMastersRequest(BaseModel):
    company: Optional[str] = None
    master_xml: str  # Inner XML with <LEDGER>/<GROUP>/etc blocks
    dup_strategy: str = "@@DUPCOMBINE"

@router.post("/direct/import-masters")
async def direct_import_masters(
    request: DirectImportMastersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Import master data (ledgers, groups, etc.) directly into Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.import_masters(
            request.master_xml,
            company=request.company,
            dup_strategy=request.dup_strategy,
        )
        return result
    except Exception as e:
        logger.error(f"Error importing masters to Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Execute: Sync ---

@router.post("/direct/execute-sync")
async def direct_execute_sync(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Execute Sync action in Tally."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.execute_action("Sync")
        return result
    except Exception as e:
        logger.error(f"Error executing Sync in Tally: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Generate XML + Direct Push (combined workflow) ---

@router.post("/direct/generate-and-push")
async def direct_generate_and_push(
    files: List[UploadFile] = File(...),
    company: str = Form(...),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Generate Tally XML from uploaded files and push directly to Tally.
    Combines generate-xml + push-to-tally in one step using direct HTTP.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        logger.info(f"Direct generate-and-push for company: {company}")

        # Fetch Tally data directly
        ledger_data = await tally_connector.get_ledgers(company)
        ledger_names = [l["name"] for l in ledger_data]

        try:
            inventory_data = await tally_connector.get_inventory(company)
            inventory_names = [i["name"] for i in inventory_data]
            inventory_names_with_units = [
                {"name": i["name"], "unit": i.get("baseUnits", "Nos")}
                for i in inventory_data
            ]
        except Exception:
            logger.warning("Could not fetch inventory, proceeding without")
            inventory_names = []
            inventory_names_with_units = []

        try:
            godown_data = await tally_connector.get_godowns(company)
            godown_names = [g["name"] for g in godown_data]
        except Exception:
            godown_names = []

        # Process files
        temp_files_info = []
        for file in files:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=os.path.splitext(file.filename)[1]
            ) as tf:
                content = await file.read()
                tf.write(content)
                temp_files_info.append({"path": tf.name, "filename": file.filename})

        try:
            tasks = [
                process_file_for_xml(
                    fi["path"], fi["filename"], ledger_data, company,
                    inventory_names, inventory_names_with_units,
                )
                for fi in temp_files_info
            ]
            results = await asyncio.gather(*tasks)

            push_results = []
            for i, result in enumerate(results):
                filename = temp_files_info[i]["filename"]
                xml = result.get("tally_xml", "")
                if xml:
                    push_result = await tally_connector.push_xml(xml, company=company)
                    push_results.append({
                        "filename": filename,
                        "push_result": push_result,
                        "transactions_count": len(result.get("transactions", [])),
                        "document_type": result.get("document_type"),
                    })
                else:
                    push_results.append({
                        "filename": filename,
                        "push_result": {"success": False, "error": "No XML generated"},
                        "transactions_count": len(result.get("transactions", [])),
                        "document_type": result.get("document_type"),
                    })

            return {
                "success": True,
                "results": push_results,
                "ledgers": ledger_names,
                "inventory": inventory_names,
                "godowns": godown_names,
            }
        finally:
            for fi in temp_files_info:
                try:
                    os.unlink(fi["path"])
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Error in direct generate-and-push: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Sync company folders using direct connection ---

@router.post("/direct/sync-company-folders")
async def direct_sync_company_folders(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Sync folders with Tally companies using direct HTTP connection."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        companies = await tally_connector.get_companies()
        if not companies:
            return JSONResponse({"error": "No companies found in Tally"}, status_code=404)

        created_folders = []
        existing_folders = []

        for company in companies:
            company_name = company.get("name")
            if not company_name:
                continue

            existing_lockr = get_lockr_by_name_and_user(user_data["uid"], company_name)
            if existing_lockr:
                existing_folders.append({"name": company_name, "lockr_id": existing_lockr})
            else:
                new_lockr_id = add_new_lockr(user_data["uid"], company_name)
                if new_lockr_id:
                    created_folders.append({"name": company_name, "lockr_id": new_lockr_id})

        return {
            "status": "success",
            "companies_found": len(companies),
            "created": {"count": len(created_folders), "folders": created_folders},
            "existing": {"count": len(existing_folders), "folders": existing_folders},
        }

    except Exception as e:
        logger.error(f"Error syncing company folders (direct): {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# CREATE STOCK ITEM (Direct HTTP)
# ============================================

class StockItemCreateRequest(BaseModel):
    company: str
    name: str
    parent: str = "Primary"
    base_units: str = "Nos"
    opening_balance: str = "0"
    opening_value: str = "0"
    category: str = ""
    gst_applicable: str = ""


@router.post("/direct/create-stock-item")
async def direct_create_stock_item(
    request: StockItemCreateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a new stock item in Tally via direct HTTP."""
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        result = await tally_connector.create_stock_item(
            company=request.company,
            name=request.name,
            parent=request.parent,
            base_units=request.base_units,
            opening_balance=request.opening_balance,
            opening_value=request.opening_value,
            category=request.category,
            gst_applicable=request.gst_applicable,
        )

        success = (
            result.get("success", False)
            or result.get("created", 0) > 0
            or result.get("combined", 0) > 0
        )

        return {
            "success": success,
            "message": "Stock item created successfully" if success else "Failed to create stock item",
            "stock_item": {"name": request.name, "parent": request.parent, "base_units": request.base_units},
            "error": result.get("error", "") if not success else "",
        }

    except Exception as e:
        logger.error(f"Error creating stock item: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# PUSH VOUCHERS WITH UPSERT (Two-Stage)
# ============================================

class PushVouchersWithUpsertRequest(BaseModel):
    """
    Two-stage upsert request: creates missing masters then pushes vouchers.

    Sales transaction format:
        {date, party_ledger, narration, invoice_number, party_gstin?, place_of_supply?,
         inventory_items: [{item_name, quantity, rate, unit, discount_percentage?, batch_number?}],
         ledger_entries: [{ledger_name, amount, parent_group?}]}

    Purchase transaction format (ledger-only, no inventory entries):
        {date, party_ledger, narration, reference_number?, place_of_supply?,
         amount, gst_percentage?, gst_amount?, total_amount?, debit_ledger?}
      OR the upsert-style format with inventory_items/ledger_entries (normalized internally).
    """
    company: str
    voucher_type: str  # "Sales", "Purchase", "Receipt", "Payment", "Contra"
    transactions: List[Dict[str, Any]]
    bank_ledger: str = ""
    auto_create_ledgers: bool = True
    auto_create_stock_items: bool = True
    batch_size: int = 20


@router.post("/direct/push-vouchers-with-upsert")
async def push_vouchers_with_upsert(
    request: PushVouchersWithUpsertRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Two-stage voucher push:
    1. Automatically create missing ledgers and stock items in Tally
    2. Push the vouchers (Sales, Purchase, Bank)

    This is the recommended endpoint for pushing any voucher type to Tally.
    It handles the "masters must exist before transactions" requirement automatically.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if not request.transactions:
            return JSONResponse({"error": "No transactions provided"}, status_code=400)

        # Verify Tally is reachable
        try:
            company_info = await tally_connector.get_current_company()
            if not company_info.get("success"):
                return JSONResponse(
                    {"error": "Cannot connect to Tally. Ensure Tally Prime is running with XML Server enabled."},
                    status_code=503,
                )
        except Exception as e:
            return JSONResponse(
                {"error": f"Cannot connect to Tally at {tally_connector.base_url}: {str(e)}"},
                status_code=503,
            )

        # Delegate to the orchestrator
        result = await tally_connector.push_voucher_with_upsert(
            company=request.company,
            voucher_type=request.voucher_type,
            transactions=request.transactions,
            bank_ledger=request.bank_ledger,
            auto_create_ledgers=request.auto_create_ledgers,
            auto_create_stock_items=request.auto_create_stock_items,
            batch_size=request.batch_size,
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in push-vouchers-with-upsert: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# ENSURE MASTERS EXIST (Standalone)
# ============================================

class EnsureMastersRequest(BaseModel):
    company: str
    ledgers: List[Dict[str, str]] = []  # [{"name": "...", "parent_group": "Sundry Debtors"}]
    stock_items: List[Dict[str, str]] = []  # [{"name": "...", "parent": "Primary", "base_units": "Nos"}]


@router.post("/direct/ensure-masters")
async def ensure_masters_exist(
    request: EnsureMastersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Ensure ledgers and stock items exist in Tally, creating any that are missing.
    This is the "upsert" stage that should be called before pushing vouchers.

    Returns details about which masters were created, already existed, or failed.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        ledger_results = {}
        stock_results = {}

        if request.ledgers:
            ledger_results = await tally_connector.ensure_ledgers_exist(
                request.company, request.ledgers
            )

        if request.stock_items:
            stock_results = await tally_connector.ensure_stock_items_exist(
                request.company, request.stock_items
            )

        return {
            "success": True,
            "ledgers": ledger_results,
            "stock_items": stock_results,
        }

    except Exception as e:
        logger.error(f"Error ensuring masters exist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# GENERATE SALES/PURCHASE XML (Direct HTTP)
# ============================================

class GenerateInvoiceXmlRequest(BaseModel):
    """
    Generate Tally XML without pushing. See PushVouchersWithUpsertRequest for
    transaction format details per voucher type (Sales vs Purchase).
    """
    company: str
    voucher_type: str = "Sales"  # "Sales" or "Purchase"
    transactions: List[Dict[str, Any]]
    sales_ledger: str = "Sales"
    purchase_ledger: str = "Purchase"
    cgst_ledger: str = "CGST ITC"
    sgst_ledger: str = "SGST ITC"


@router.post("/direct/generate-invoice-xml")
async def generate_invoice_xml(
    request: GenerateInvoiceXmlRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Generate Tally XML for Sales or Purchase vouchers with inventory entries.
    Returns the XML string without pushing it to Tally.
    """
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if not request.transactions:
            return JSONResponse({"error": "No transactions provided"}, status_code=400)

        if request.voucher_type == "Sales":
            xml = tally_connector.build_sales_voucher_xml(request.company, request.transactions, sales_ledger=request.sales_ledger)
        elif request.voucher_type == "Purchase":
            xml = tally_connector.build_purchase_voucher_xml(
                request.company, request.transactions,
                purchase_ledger=request.purchase_ledger,
                cgst_ledger=request.cgst_ledger,
                sgst_ledger=request.sgst_ledger,
            )
        else:
            return JSONResponse(
                {"error": f"Unsupported voucher type: {request.voucher_type}. Use 'Sales' or 'Purchase'."},
                status_code=400,
            )

        if not xml:
            return JSONResponse({"error": "No valid vouchers to generate XML for"}, status_code=400)

        return {
            "success": True,
            "xml": xml,
            "voucher_type": request.voucher_type,
            "voucher_count": len(request.transactions),
        }

    except Exception as e:
        logger.error(f"Error generating invoice XML: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Drafts (purchase / sales / bank_entries) — per-user, per-company snapshots
# of in-progress work so the bookkeeper can resume after closing the tab.
# ---------------------------------------------------------------------------
# Tally Document Library — file upload, lifecycle management, draft CRUD
# ---------------------------------------------------------------------------

class TallySaveDraftRequest(BaseModel):
    flow: str  # 'purchase' | 'sales' | 'bank_entries'
    payload: Dict[str, Any]
    title: Optional[str] = None
    draft_id: Optional[str] = None
    company_name: Optional[str] = None


class TallyUpdateDraftRequest(BaseModel):
    payload: Dict[str, Any]


@router.post("/tally-upload")
async def tally_upload_file(
    file: UploadFile = File(...),
    company: str = Form(...),
    flow: str = Form(...),
    bank_ledger: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Step 1 of the new document library flow.
    Saves the raw file to Cloudflare R2 and creates a draft record
    with status='uploaded'. No extraction happens here.
    """
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")

    if flow not in ("purchase", "sales", "bank_entries"):
        raise HTTPException(status_code=400, detail=f"Invalid flow: {flow}")

    draft_id = str(uuid.uuid4())
    filename = file.filename or "document"
    file_bytes = await file.read()

    # Upload to R2
    r2_key = r2_service.build_r2_key(user_data["uid"], flow, draft_id, filename)
    try:
        file_url = r2_service.upload_to_r2(file_bytes, r2_key)
    except Exception as exc:
        logger.error("R2 upload failed: %s", exc)
        raise HTTPException(status_code=502, detail="File upload to storage failed")

    # Store bank_ledger in payload for bank_entries flow
    initial_payload = {"bank_ledger": bank_ledger} if bank_ledger else {}

    # Create draft record
    new_id = tally_drafts_store.upsert(
        user_id=user_data["uid"],
        company_name=company,
        flow=flow,
        payload=initial_payload,
        title=filename,
        draft_id=draft_id,
        status="uploaded",
        file_url=file_url,
        file_name=filename,
        r2_key=r2_key,
    )
    if not new_id:
        raise HTTPException(status_code=500, detail="Could not create draft record")

    return {
        "draft_id": new_id,
        "file_url": file_url,
        "file_name": filename,
        "status": "uploaded",
        "company": company,
        "flow": flow,
    }


@router.post("/drafts/{draft_id}/extract")
async def tally_draft_extract(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Step 2 of the document library flow.
    Downloads the file from R2 and runs extraction.
    Returns the full extracted data as JSON (blocking, no streaming).
    """
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")

    draft = tally_drafts_store.get_draft(user_id=user_data["uid"], draft_id=draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if not draft.get("r2_key"):
        raise HTTPException(status_code=400, detail="No file associated with this draft")

    # Mark as extracting
    tally_drafts_store.update_status(
        draft_id=draft_id, user_id=user_data["uid"], status="extracting"
    )

    flow = draft["flow"]
    company = draft["company_name"]

    try:
        # Download file from R2
        file_bytes = r2_service.download_from_r2(draft["r2_key"])
        filename = draft.get("file_name", "document")
        suffix = os.path.splitext(filename)[1] or ".pdf"

        # Fetch Tally ledgers + inventory for matching (best-effort)
        ledgers_data, inventory_names_list, inventory_names_with_units = [], [], []
        try:
            ledgers_data = await tally_connector.get_ledgers(company) or []
            inventory_data = await tally_connector.get_inventory(company) or []
            inventory_names_list = [i.get("name", "") for i in inventory_data]
            inventory_names_with_units = [
                {"name": i.get("name", ""), "unit": i.get("baseUnits", "Nos")}
                for i in inventory_data if i.get("name")
            ]
        except Exception:
            pass

        firebase_uid = user_data.get("uid")
        if not ledgers_data and firebase_uid and firebase_uid in manager.electron_connections:
            try:
                ledgers_data = await manager.get_tally_ledgers(firebase_uid, company) or []
            except Exception:
                pass

        if ledgers_data and firebase_uid:
            try:
                tally_cache_store.upsert_ledgers(
                    user_id=firebase_uid, company_name=company, ledgers=ledgers_data
                )
            except Exception:
                pass

        # Final fallback: previously-cached ledger list. Lets extraction match
        # parties to ledgers even when no Tally bridge is online.
        if not ledgers_data and firebase_uid:
            try:
                cached = tally_cache_store.get_cached_ledgers(
                    user_id=firebase_uid, company_name=company
                )
                if cached and cached.get("ledgers"):
                    ledgers_data = cached["ledgers"]
            except Exception:
                pass

        # Write to temp file and extract
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            temp_file.write(file_bytes)
            temp_file.close()

            if flow == "sales":
                result = await _extract_one_sales_invoice(
                    file_bytes=file_bytes,
                    filename=filename,
                    company=company,
                    ledgers_data=ledgers_data,
                    inventory_names_list=inventory_names_list,
                    inventory_names_with_units=inventory_names_with_units,
                )
                extracted_payload = {"invoices": [result], "flow": "sales"}

            elif flow == "purchase":
                result = await _extract_one_purchase_invoice(
                    file_bytes=file_bytes,
                    filename=filename,
                    company=company,
                    ledgers_data=ledgers_data,
                    inventory_names_list=inventory_names_list,
                    inventory_names_with_units=inventory_names_with_units,
                )
                extracted_payload = {"invoices": [result], "flow": "purchase"}

            else:  # bank_entries
                bank_ledger = (draft.get("payload") or {}).get("bank_ledger", "")
                bank_result = await tally_agent(
                    file_path=temp_file.name,
                    company_name=company,
                    available_ledgers=ledgers_data,
                    user_id=user_data.get("uid"),
                )
                transactions = bank_result.get("transactions", [])
                # Attach bank_ledger to each transaction so BankEntriesView can use it
                if bank_ledger:
                    for tx in transactions:
                        tx.setdefault("bank_ledger", bank_ledger)
                extracted_payload = {
                    "transactions": transactions,
                    "bank_ledger": bank_ledger,
                    "flow": "bank_entries",
                }

        finally:
            try:
                os.unlink(temp_file.name)
            except Exception:
                pass

        # Mark the row as extracted (ready for review). It becomes a real
        # 'draft' only when the user clicks Save Draft in the editor.
        tally_drafts_store.update_payload(
            draft_id=draft_id,
            user_id=user_data["uid"],
            payload=extracted_payload,
            set_status="extracted",
        )

        return {
            "draft_id": draft_id,
            "status": "extracted",
            "flow": flow,
            "extracted_data": extracted_payload,
        }

    except Exception as exc:
        logger.exception("Extraction failed for draft %s: %s", draft_id, exc)
        tally_drafts_store.update_status(
            draft_id=draft_id,
            user_id=user_data["uid"],
            status="error",
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}")


@router.post("/drafts")
async def tally_drafts_save(
    body: TallySaveDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    new_id = tally_drafts_store.upsert(
        user_id=user_data["uid"],
        company_name=body.company_name or "",
        flow=body.flow,
        payload=body.payload or {},
        title=body.title,
        draft_id=body.draft_id,
    )
    if not new_id:
        raise HTTPException(status_code=500, detail="Could not persist draft")
    return {"draft_id": new_id}


@router.put("/drafts/{draft_id}")
async def tally_draft_update(
    draft_id: str,
    body: TallyUpdateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Update the payload of an existing draft (save user edits). This is the
    explicit user-driven save path, so we promote 'extracted' to 'draft'."""
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    ok = tally_drafts_store.update_payload(
        draft_id=draft_id,
        user_id=user_data["uid"],
        payload=body.payload,
        set_status="draft",
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.patch("/drafts/{draft_id}/push")
async def tally_draft_mark_pushed(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Mark a draft as pushed to Tally."""
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    ok = tally_drafts_store.mark_pushed(draft_id=draft_id, user_id=user_data["uid"])
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.get("/drafts")
async def tally_drafts_list(
    flow: Optional[str] = None,
    company_name: Optional[str] = None,
    status: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    rows = tally_drafts_store.list_drafts(
        user_id=user_data["uid"],
        company_name=company_name,
        flow=flow,
        status=status,
    )
    return {"drafts": rows}


@router.get("/drafts/{draft_id}")
async def tally_drafts_get(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    row = tally_drafts_store.get_draft(user_id=user_data["uid"], draft_id=draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    return row


def _collect_draft_r2_keys(draft: Dict[str, Any]) -> set:
    """Enumerate every r2 object key associated with a draft.

    Used as the allowlist for the presigned-URL endpoint so a user cannot
    supply an arbitrary key and read someone else's file. Covers both the
    top-level r2_key (bank entries) and per-invoice r2_keys carried in the
    JSONB payload (sales/purchase).
    """
    keys = set()
    top = draft.get("r2_key")
    if top:
        keys.add(top)
    payload = draft.get("payload") or {}
    if isinstance(payload, dict):
        for inv in payload.get("invoices") or []:
            if isinstance(inv, dict) and inv.get("r2_key"):
                keys.add(inv["r2_key"])
    return keys


@router.get("/drafts/{draft_id}/file-url")
async def tally_drafts_get_file_url(
    draft_id: str,
    r2_key: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return a short-lived presigned download URL for a draft's file.

    The stored `file_url` is built at upload time and can break when the
    R2 public-URL config drifts (e.g. the bucket name ends up duplicated in
    the path). Presigned URLs bypass the public URL entirely — they hit the
    S3-compatible endpoint with an embedded signature, so they work whether
    or not the bucket is public.

    `r2_key` is required for sales/purchase drafts (per-invoice files);
    omit it for bank entries (single top-level file).
    """
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")

    draft = tally_drafts_store.get_draft(user_id=user_data["uid"], draft_id=draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    target_key = r2_key or draft.get("r2_key")
    if not target_key:
        raise HTTPException(status_code=404, detail="No file associated with this draft")

    if target_key not in _collect_draft_r2_keys(draft):
        raise HTTPException(status_code=403, detail="Key not associated with this draft")

    try:
        url = r2_service.generate_presigned_download_url(target_key)
    except Exception as exc:
        logger.error("Presigned URL generation failed for draft=%s: %s", draft_id, exc)
        raise HTTPException(status_code=502, detail="Could not generate file URL")

    return {"url": url}


@router.delete("/drafts/{draft_id}")
async def tally_drafts_delete(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    r2_key = tally_drafts_store.delete_draft(user_id=user_data["uid"], draft_id=draft_id)
    if r2_key is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    # Best-effort R2 cleanup
    if r2_key:
        try:
            r2_service.delete_from_r2(r2_key)
        except Exception as exc:
            logger.warning("R2 cleanup failed for draft %s: %s", draft_id, exc)
    return {"deleted": True}


def _collect_referenced_ledgers(flow: str, payload: Dict[str, Any]) -> List[str]:
    """Pull the ledger names a draft will need at push time, across flows."""
    names: List[str] = []
    if not isinstance(payload, dict):
        return names

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            names.append(value.strip())

    if flow in ("sales", "purchase"):
        for inv in payload.get("invoices") or []:
            if not isinstance(inv, dict):
                continue
            add(inv.get("party_ledger"))
            add(inv.get("sales_ledger"))
            add(inv.get("purchase_ledger"))
            for line in inv.get("items") or []:
                if isinstance(line, dict):
                    add(line.get("ledger"))
                    add(line.get("sales_ledger"))
                    add(line.get("purchase_ledger"))
            for tax in inv.get("taxes") or []:
                if isinstance(tax, dict):
                    add(tax.get("ledger"))
    elif flow == "bank_entries":
        add(payload.get("bank_ledger"))
        for tx in payload.get("transactions") or []:
            if isinstance(tx, dict):
                add(tx.get("party_ledger"))
                add(tx.get("bank_ledger"))
    # De-dupe preserving order
    seen: set = set()
    unique: List[str] = []
    for n in names:
        key = n.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(n)
    return unique


@router.post("/validate-draft-pushable")
async def validate_draft_pushable(
    payload: Dict[str, Any],
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Pre-push validation: does the draft's company exist in Tally now, and
    are all the ledgers it references still present? Used by bulk push and the
    'Push' button to surface fixable issues before sending XML."""
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    draft_id = payload.get("draft_id")
    if not draft_id:
        raise HTTPException(status_code=400, detail="draft_id is required")

    draft = tally_drafts_store.get_draft(user_id=user_data["uid"], draft_id=draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    flow = draft.get("flow") or ""
    company = (draft.get("company_name") or "").strip()
    draft_payload = draft.get("payload") or {}
    referenced = _collect_referenced_ledgers(flow, draft_payload)

    uid = user_data["uid"]
    company_ok = True
    company_source = "live"
    available_ledgers: List[str] = []
    ledgers_source = "live"

    # Company presence
    try:
        live_companies = await manager.get_tally_companies(uid)
        names = []
        for c in live_companies or []:
            if isinstance(c, str):
                names.append(c)
            elif isinstance(c, dict):
                names.append(c.get("name") or c.get("company") or "")
        company_ok = company in {n for n in names if n}
        if live_companies:
            try:
                tally_cache_store.upsert_companies(user_id=uid, companies=live_companies)
            except Exception:
                pass
    except Exception:
        # Live fetch failed → fall back to cache (company may still be valid even
        # though we can't push right now; the push button will surface the
        # connection state separately).
        company_source = "cache"
        cached = tally_cache_store.list_cached_companies(user_id=uid)
        company_ok = company in {c["company_name"] for c in cached}

    # Ledger presence
    try:
        live_ledgers = await manager.get_tally_ledgers(uid, company)
        for lg in live_ledgers or []:
            if isinstance(lg, str):
                available_ledgers.append(lg)
            elif isinstance(lg, dict):
                name = lg.get("name") or lg.get("ledger_name") or ""
                if name:
                    available_ledgers.append(name)
        if live_ledgers:
            try:
                tally_cache_store.upsert_ledgers(
                    user_id=uid, company_name=company, ledgers=live_ledgers
                )
            except Exception:
                pass
    except Exception:
        ledgers_source = "cache"
        cached = tally_cache_store.get_cached_ledgers(user_id=uid, company_name=company)
        for lg in (cached or {}).get("ledgers", []) or []:
            if isinstance(lg, str):
                available_ledgers.append(lg)
            elif isinstance(lg, dict):
                name = lg.get("name") or lg.get("ledger_name") or ""
                if name:
                    available_ledgers.append(name)

    available_set = {n.casefold() for n in available_ledgers}
    missing = [n for n in referenced if n.casefold() not in available_set] if available_ledgers else []

    return {
        "ok": company_ok and not missing,
        "company": company,
        "flow": flow,
        "company_ok": company_ok,
        "company_source": company_source,
        "missing_ledgers": missing,
        "ledgers_source": ledgers_source,
        "referenced_ledger_count": len(referenced),
    }