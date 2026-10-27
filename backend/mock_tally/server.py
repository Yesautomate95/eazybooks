"""
Mock Tally Server
=================
A comprehensive mock implementation of Tally's XML HTTP API.
Listens on port 9000 (same as real Tally) and responds to all standard Tally XML requests.

Usage:
    python -m mock_tally.server

    Or with uvicorn directly:
    uvicorn mock_tally.server:app --host 0.0.0.0 --port 9000 --reload
"""

import re
import logging
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .sample_data import (
    COMPANIES, LEDGERS, GROUPS, INVENTORY, GODOWNS, VOUCHER_TYPES,
    find_ledger, add_ledger, update_ledger, add_group, import_voucher,
    get_ledgers_by_company
)
from .xml_generators import (
    generate_companies_xml,
    generate_ledgers_xml,
    generate_ledger_details_xml,
    generate_groups_xml,
    generate_inventory_xml,
    generate_godowns_xml,
    generate_import_success_xml,
    generate_import_error_xml,
    generate_error_xml,
    generate_voucher_types_xml,
    generate_stock_groups_xml,
    generate_units_xml,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mock_tally")

# Create FastAPI app
app = FastAPI(
    title="Mock Tally Server",
    description="A mock implementation of Tally's XML HTTP API for development on macOS",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track statistics
stats = {
    "requests_handled": 0,
    "companies_fetched": 0,
    "ledgers_fetched": 0,
    "ledgers_created": 0,
    "ledgers_updated": 0,
    "groups_fetched": 0,
    "groups_created": 0,
    "inventory_fetched": 0,
    "godowns_fetched": 0,
    "vouchers_imported": 0,
    "errors": 0,
    "start_time": datetime.now().isoformat()
}


# ============================================================================
# XML REQUEST PARSER
# ============================================================================

def parse_xml_request(xml_body: str) -> dict:
    """
    Parse Tally XML request to determine the operation type and parameters.
    Returns a dict with operation info.
    """
    result = {
        "operation": "unknown",
        "collection": None,
        "company": None,
        "data": {},
        "raw": xml_body
    }

    # Normalize the XML
    xml_lower = xml_body.lower()

    # Determine if it's an Export (read) or Import (write) request
    if "<tallyrequest>export" in xml_lower:
        result["operation"] = "export"
    elif "<tallyrequest>import data" in xml_lower or "<tallyrequest>import" in xml_lower:
        result["operation"] = "import"

    # Extract collection name
    collection_patterns = [
        r'<id>([^<]+)</id>',
        r'<collection\s+name="([^"]+)"',
        r'<collection\s+name=\'([^\']+)\'',
    ]
    for pattern in collection_patterns:
        match = re.search(pattern, xml_body, re.IGNORECASE)
        if match:
            result["collection"] = match.group(1).strip()
            break

    # Extract company name
    company_patterns = [
        r'<svcurrentcompany>([^<]+)</svcurrentcompany>',
        r'<currentcompany>([^<]+)</currentcompany>',
    ]
    for pattern in company_patterns:
        match = re.search(pattern, xml_body, re.IGNORECASE)
        if match:
            result["company"] = match.group(1).strip()
            break

    # For import operations, extract additional data
    if result["operation"] == "import":
        # Check what type of master/voucher is being created
        if "<ledger" in xml_lower:
            result["type"] = "ledger"
            if 'action="create"' in xml_lower or "action='create'" in xml_lower:
                result["action"] = "create"
            elif 'action="alter"' in xml_lower or "action='alter'" in xml_lower:
                result["action"] = "alter"

            # Extract ledger data
            result["data"] = extract_ledger_data(xml_body)

        elif "<stockitem" in xml_lower:
            result["type"] = "stockitem"
            result["action"] = "create"
            # Extract basic stock item data
            stock_data = {}
            for key, pattern in [
                ("name", r'<name>([^<]+)</name>'),
                ("parent", r'<parent>([^<]+)</parent>'),
                ("base_units", r'<baseunits>([^<]+)</baseunits>'),
            ]:
                match = re.search(pattern, xml_body, re.IGNORECASE)
                if match:
                    stock_data[key] = match.group(1).strip()
            result["data"] = stock_data

        elif "<group" in xml_lower:
            result["type"] = "group"
            result["action"] = "create"
            result["data"] = extract_group_data(xml_body)

        elif "<voucher" in xml_lower:
            result["type"] = "voucher"
            result["action"] = "create"

    # Detect collection type for exports
    if result["collection"]:
        collection_lower = result["collection"].lower()
        if "company" in collection_lower:
            result["type"] = "companies"
        elif "ledger" in collection_lower:
            result["type"] = "ledgers"
        elif "group" in collection_lower and "stock" not in collection_lower:
            result["type"] = "groups"
        elif "stock" in collection_lower and "group" in collection_lower:
            result["type"] = "stock_groups"
        elif "stock" in collection_lower or "inventory" in collection_lower:
            result["type"] = "inventory"
        elif "godown" in collection_lower:
            result["type"] = "godowns"
        elif "voucher" in collection_lower and "type" in collection_lower:
            result["type"] = "voucher_types"
        elif "unit" in collection_lower:
            result["type"] = "units"

    return result


def _xml_unescape(text: str) -> str:
    """Reverse XML escaping so stored values match what real Tally keeps in memory."""
    return (text
            .replace("&apos;", "'")
            .replace("&quot;", '"')
            .replace("&gt;", ">")
            .replace("&lt;", "<")
            .replace("&amp;", "&"))


def extract_ledger_data(xml_body: str) -> dict:
    """Extract ledger data from XML.

    Recognises both the legacy minimal tag set (NAME/PARENT/ADDRESS/
    COUNTRYOFRESIDENCE/LEDSTATENAME/LEDGERMOBILE/PARTYGSTIN/OPENINGBALANCE)
    and the expanded TallyPrime ledger schema we now emit. New tags are
    optional — the mock just stores them on the ledger record so dev round-
    trips show them populated.
    """
    data = {}

    # Single-value leaf tags. The first match wins.
    patterns = {
        # Identity
        "name": r'<name>([^<]+)</name>',
        "parent": r'<parent>([^<]+)</parent>',
        "alias": r'<additionalname>([^<]*)</additionalname>',
        "mailing_name": r'<mailingname>([^<]*)</mailingname>',
        "opening_balance": r'<openingbalance>([^<]*)</openingbalance>',
        "description": r'<description>([^<]*)</description>',
        "narration": r'<narration>([^<]*)</narration>',
        # Address (single-line `address` falls back to the first ADDRESS.LIST child)
        "pincode": r'<pincode>([^<]*)</pincode>',
        # State / country — accept either canonical or legacy tag names
        "state": r'<(?:statename|ledstatename)>([^<]*)</(?:statename|ledstatename)>',
        "country": r'<(?:countryname|countryofresidence)>([^<]*)</(?:countryname|countryofresidence)>',
        # Contact
        "contact_person": r'<ledgercontact>([^<]*)</ledgercontact>',
        "phone": r'<ledgerphone>([^<]*)</ledgerphone>',
        "mobile": r'<ledgermobile>([^<]*)</ledgermobile>',
        "fax": r'<ledgerfax>([^<]*)</ledgerfax>',
        "email": r'<email>([^<]*)</email>',
        "email_cc": r'<emailcc>([^<]*)</emailcc>',
        # Statutory
        "pan": r'<incometaxnumber>([^<]*)</incometaxnumber>',
        "gstin": r'<partygstin>([^<]*)</partygstin>',
        "gst_registration_type": r'<gstregistrationtype>([^<]*)</gstregistrationtype>',
        # Bill-wise / credit
        "bill_wise": r'<isbillwiseon>([^<]*)</isbillwiseon>',
        "bill_credit_period": r'<billcreditperiod>([^<]*)</billcreditperiod>',
        "credit_limit": r'<creditlimit>([^<]*)</creditlimit>',
        # Bank (inside PAYMENTDETAILS.LIST)
        "bank_name": r'<bankname>([^<]*)</bankname>',
        "account_number": r'<accountnumber>([^<]*)</accountnumber>',
        "ifsc_code": r'<ifscode>([^<]*)</ifscode>',
        "payment_favouring": r'<paymentfavouring>([^<]*)</paymentfavouring>',
        # Behaviour
        "affects_stock": r'<affectsstock>([^<]*)</affectsstock>',
        "cost_centres_applicable": r'<iscostcentreson>([^<]*)</iscostcentreson>',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, xml_body, re.IGNORECASE)
        if match:
            data[key] = _xml_unescape(match.group(1).strip())

    # Multi-line address — collect every ADDRESS child of the top-level
    # ADDRESS.LIST (not the one inside LEDMAILINGDETAILS.LIST).
    addr_list_match = re.search(
        r'<address\.list[^>]*>(.*?)</address\.list>', xml_body, re.IGNORECASE | re.DOTALL,
    )
    if addr_list_match:
        addr_block = addr_list_match.group(1)
        lines = [_xml_unescape(m.group(1).strip()) for m in re.finditer(r'<address>([^<]*)</address>', addr_block, re.IGNORECASE)]
        if lines:
            data["address_lines"] = lines
            data["address"] = lines[0]  # backward-compat single-line view

    # Handle NAME attribute on the LEDGER tag itself (used in alter operations)
    name_attr_match = re.search(r'<ledger[^>]+name="([^"]+)"', xml_body, re.IGNORECASE)
    if name_attr_match:
        data["current_name"] = name_attr_match.group(1).strip()

    # Handle NAME.LIST for new name in alter
    name_list_match = re.search(r'<name\.list>.*?<name>([^<]+)</name>.*?</name\.list>', xml_body, re.IGNORECASE | re.DOTALL)
    if name_list_match:
        data["new_name"] = name_list_match.group(1).strip()

    return data


def extract_group_data(xml_body: str) -> dict:
    """Extract group data from XML"""
    data = {}

    patterns = {
        "name": r'<name>([^<]+)</name>',
        "parent": r'<parent>([^<]+)</parent>',
        "nature": r'<nature>([^<]*)</nature>',
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, xml_body, re.IGNORECASE)
        if match:
            data[key] = match.group(1).strip()

    return data


def extract_ledger_filter(xml_body: str) -> Optional[str]:
    """Extract ledger name filter from XML request"""
    # Pattern for FilterByName
    pattern = r'\$\$FilterByName:\$Name:"([^"]+)"'
    match = re.search(pattern, xml_body)
    if match:
        return match.group(1).strip()
    return None


# ============================================================================
# MAIN REQUEST HANDLER
# ============================================================================

@app.post("/")
async def handle_tally_request(request: Request):
    """
    Main endpoint that handles all Tally XML API requests.
    Parses the XML, determines the operation, and returns appropriate response.
    """
    global stats
    stats["requests_handled"] += 1

    # Read raw body
    body = await request.body()
    xml_body = body.decode('utf-8', errors='ignore')

    logger.info(f"Received request ({len(xml_body)} bytes)")
    logger.debug(f"Request body:\n{xml_body[:500]}...")

    # Parse the request
    parsed = parse_xml_request(xml_body)
    logger.info(f"Parsed request: operation={parsed['operation']}, type={parsed.get('type')}, collection={parsed.get('collection')}")

    try:
        # Handle based on operation type
        if parsed["operation"] == "export":
            response_xml = handle_export_request(parsed)
        elif parsed["operation"] == "import":
            response_xml = handle_import_request(parsed)
        else:
            # Default: try to detect and handle
            response_xml = handle_unknown_request(parsed)

        logger.info(f"Sending response ({len(response_xml)} bytes)")

        return Response(
            content=response_xml,
            media_type="application/xml",
            headers={"X-Mock-Tally": "1.0.0"}
        )

    except Exception as e:
        stats["errors"] += 1
        logger.error(f"Error handling request: {str(e)}")
        return Response(
            content=generate_error_xml(str(e)),
            media_type="application/xml",
            status_code=200  # Tally always returns 200
        )


def handle_export_request(parsed: dict) -> str:
    """Handle export (read) operations"""
    global stats

    request_type = parsed.get("type", "").lower()
    collection = (parsed.get("collection") or "").lower()

    # Companies
    if request_type == "companies" or "company" in collection:
        stats["companies_fetched"] += 1
        logger.info("Returning companies list")
        return generate_companies_xml(COMPANIES)

    # Ledgers
    if request_type == "ledgers" or "ledger" in collection:
        # Check if it's a filtered request for specific ledger
        ledger_filter = extract_ledger_filter(parsed.get("raw", ""))
        if ledger_filter:
            ledger = find_ledger(ledger_filter)
            if ledger:
                logger.info(f"Returning ledger details for: {ledger_filter}")
                return generate_ledger_details_xml(ledger)
            else:
                logger.warning(f"Ledger not found: {ledger_filter}")
                return generate_ledgers_xml([])

        stats["ledgers_fetched"] += 1
        company = parsed.get("company")
        logger.info(f"Returning ledgers list for company: {company or 'all'}")
        return generate_ledgers_xml(get_ledgers_by_company(company))

    # Groups
    if request_type == "groups" or ("group" in collection and "stock" not in collection):
        stats["groups_fetched"] += 1
        logger.info("Returning groups list")
        return generate_groups_xml(GROUPS)

    # Stock Groups
    if request_type == "stock_groups" or ("stock" in collection and "group" in collection):
        logger.info("Returning stock groups list")
        return generate_stock_groups_xml()

    # Inventory / Stock Items
    if request_type == "inventory" or "stock" in collection or "inventory" in collection:
        stats["inventory_fetched"] += 1
        logger.info("Returning inventory list")
        return generate_inventory_xml(INVENTORY)

    # Godowns
    if request_type == "godowns" or "godown" in collection:
        stats["godowns_fetched"] += 1
        logger.info("Returning godowns list")
        return generate_godowns_xml(GODOWNS)

    # Voucher Types
    if request_type == "voucher_types" or "vouchertype" in collection:
        logger.info("Returning voucher types list")
        return generate_voucher_types_xml(VOUCHER_TYPES)

    # Units
    if request_type == "units" or "unit" in collection:
        logger.info("Returning units list")
        return generate_units_xml()

    # Unknown collection - return empty but valid response
    logger.warning(f"Unknown collection type: {collection}")
    return generate_companies_xml([])


def handle_import_request(parsed: dict) -> str:
    """Handle import (write) operations"""
    global stats

    import_type = parsed.get("type", "")
    action = parsed.get("action", "create")
    data = parsed.get("data", {})

    logger.info(f"Import request: type={import_type}, action={action}, data={data}")

    # Create Ledger
    if import_type == "ledger" and action == "create":
        stats["ledgers_created"] += 1
        # Preserve every parsed field so dev round-trips show the new schema
        # populated. Legacy callers that only look for name/parent/etc.
        # continue to work because those keys are still set.
        ledger_data = {
            "name": data.get("name", ""),
            "parent_group": data.get("parent", "Sundry Debtors"),
            "opening_balance": data.get("opening_balance", "0"),
            "address": data.get("address", ""),
            "address_lines": data.get("address_lines", []),
            "pincode": data.get("pincode", ""),
            "state": data.get("state", ""),
            "country": data.get("country", "India"),
            "alias": data.get("alias", ""),
            "mailing_name": data.get("mailing_name", ""),
            "contact_person": data.get("contact_person", ""),
            "phone": data.get("phone", ""),
            "mobile": data.get("mobile", ""),
            "fax": data.get("fax", ""),
            "email": data.get("email", ""),
            "email_cc": data.get("email_cc", ""),
            "pan": data.get("pan", ""),
            "gstin": data.get("gstin", ""),
            "gst_registration_type": data.get("gst_registration_type", ""),
            "bill_wise": data.get("bill_wise", ""),
            "bill_credit_period": data.get("bill_credit_period", ""),
            "credit_limit": data.get("credit_limit", ""),
            "bank_name": data.get("bank_name", ""),
            "account_number": data.get("account_number", ""),
            "ifsc_code": data.get("ifsc_code", ""),
            "payment_favouring": data.get("payment_favouring", ""),
            "affects_stock": data.get("affects_stock", ""),
            "cost_centres_applicable": data.get("cost_centres_applicable", ""),
            "narration": data.get("narration", ""),
            "description": data.get("description", ""),
        }

        if add_ledger(ledger_data):
            logger.info(f"Created ledger: {ledger_data['name']}")
            return generate_import_success_xml(created=1)
        else:
            logger.warning(f"Ledger already exists: {ledger_data['name']}")
            return generate_import_success_xml(altered=1)

    # Alter/Update Ledger
    if import_type == "ledger" and action == "alter":
        stats["ledgers_updated"] += 1
        current_name = data.get("current_name", "")

        update_data = {
            "name": data.get("new_name", data.get("name", current_name)),
            "parent_group": data.get("parent", ""),
            "opening_balance": data.get("opening_balance", "0"),
            "address": data.get("address", ""),
            "state": data.get("state", ""),
            "country": data.get("country", ""),
            "mobile": data.get("mobile", ""),
            "gstin": data.get("gstin", ""),
        }

        if update_ledger(current_name, update_data):
            logger.info(f"Updated ledger: {current_name} -> {update_data['name']}")
            return generate_import_success_xml(altered=1)
        else:
            logger.warning(f"Ledger not found for update: {current_name}")
            return generate_import_error_xml(f"Ledger '{current_name}' not found")

    # Create Group
    if import_type == "group":
        stats["groups_created"] += 1
        group_data = {
            "name": data.get("name", ""),
            "parent": data.get("parent", ""),
            "nature": data.get("nature", ""),
        }

        if add_group(group_data):
            logger.info(f"Created group: {group_data['name']}")
            return generate_import_success_xml(created=1)
        else:
            logger.warning(f"Group already exists: {group_data['name']}")
            return generate_import_success_xml(altered=1)

    # Create Stock Item
    if import_type == "stockitem" and action == "create":
        stats.setdefault("stock_items_created", 0)
        stats["stock_items_created"] += 1
        logger.info(f"Created stock item: {data.get('name', '')}")
        return generate_import_success_xml(created=1)

    # Voucher Import
    if import_type == "voucher":
        stats["vouchers_imported"] += 1
        result = import_voucher(parsed.get("raw", ""))
        logger.info(f"Imported voucher: created={result['created']}, exceptions={result['exceptions']}")
        return generate_import_success_xml(
            created=result["created"],
            altered=result["altered"],
            deleted=result["deleted"],
            exceptions=result["exceptions"],
            last_voucher_id=result["lastVoucherNumber"],
            error_message=result["error"]
        )

    # Unknown import type - still return success
    logger.warning(f"Unknown import type: {import_type}")
    return generate_import_success_xml(created=1)


def handle_unknown_request(parsed: dict) -> str:
    """Handle requests that couldn't be parsed"""
    logger.warning("Handling unknown request type - returning companies as default")
    return generate_companies_xml(COMPANIES)


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@app.get("/")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "running",
        "server": "Mock Tally Server",
        "version": "1.0.0",
        "tally_version": "TallyPrime (Mock)",
        "stats": stats
    }


@app.get("/stats")
async def get_stats():
    """Get server statistics"""
    return stats


@app.get("/ledgers")
async def list_ledgers():
    """Debug endpoint to list all ledgers"""
    return {"count": len(LEDGERS), "ledgers": LEDGERS}


@app.get("/companies")
async def list_companies():
    """Debug endpoint to list all companies"""
    return {"count": len(COMPANIES), "companies": COMPANIES}


@app.get("/groups")
async def list_groups():
    """Debug endpoint to list all groups"""
    return {"count": len(GROUPS), "groups": GROUPS}


@app.get("/inventory")
async def list_inventory():
    """Debug endpoint to list all inventory items"""
    return {"count": len(INVENTORY), "inventory": INVENTORY}


@app.get("/godowns")
async def list_godowns():
    """Debug endpoint to list all godowns"""
    return {"count": len(GODOWNS), "godowns": GODOWNS}


@app.post("/reset")
async def reset_data():
    """Reset all data to initial state"""
    global stats
    # Reload sample data
    from . import sample_data
    import importlib
    importlib.reload(sample_data)

    stats = {
        "requests_handled": 0,
        "companies_fetched": 0,
        "ledgers_fetched": 0,
        "ledgers_created": 0,
        "ledgers_updated": 0,
        "groups_fetched": 0,
        "groups_created": 0,
        "inventory_fetched": 0,
        "godowns_fetched": 0,
        "vouchers_imported": 0,
        "errors": 0,
        "start_time": datetime.now().isoformat()
    }

    logger.info("Data reset to initial state")
    return {"status": "reset", "message": "All data reset to initial state"}


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Run the mock Tally server"""
    import uvicorn

    print("""
╔══════════════════════════════════════════════════════════════════╗
║                    MOCK TALLY SERVER                              ║
║                                                                    ║
║  A development server that simulates Tally's XML HTTP API         ║
║                                                                    ║
║  Endpoints:                                                        ║
║    POST /        - Main Tally XML API (same as real Tally)        ║
║    GET  /        - Health check                                    ║
║    GET  /stats   - Server statistics                               ║
║    GET  /ledgers - List all ledgers (debug)                       ║
║    POST /reset   - Reset data to initial state                    ║
║                                                                    ║
║  Running on: http://localhost:9000                                 ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "mock_tally.server:app",
        host="0.0.0.0",
        port=9000,
        reload=True,
        log_level="info"
    )


if __name__ == "__main__":
    main()
