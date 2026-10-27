"""
XML Response Generators for Mock Tally Server
==============================================
Generates proper Tally-compatible XML responses matching the real Tally XML API format.
"""

from typing import List, Dict, Any
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
import html


def prettify_xml(xml_string: str) -> str:
    """Return a pretty-printed XML string"""
    try:
        dom = minidom.parseString(xml_string)
        return dom.toprettyxml(indent="  ")
    except:
        return xml_string


def escape_xml(text: str) -> str:
    """Escape special XML characters"""
    if not text:
        return ""
    return html.escape(str(text))


# ============================================================================
# COMPANY RESPONSES
# ============================================================================

def generate_companies_xml(companies: List[Dict[str, str]]) -> str:
    """Generate XML response for company list"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')

    for company in companies:
        name = escape_xml(company.get('name', ''))
        xml.append(f'        <COMPANY NAME="{name}">')
        xml.append(f'          <NAME>{name}</NAME>')
        xml.append('        </COMPANY>')

    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


# ============================================================================
# LEDGER RESPONSES
# ============================================================================

def generate_ledgers_xml(ledgers: List[Dict[str, Any]]) -> str:
    """Generate XML response for ledger list"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')

    for ledger in ledgers:
        name = escape_xml(ledger.get('name', ''))
        parent = escape_xml(ledger.get('parent_group', ledger.get('parent', '')))
        balance = ledger.get('opening_balance', '0')

        xml.append(f'        <LEDGER NAME="{name}">')
        xml.append(f'          <NAME>{name}</NAME>')
        xml.append(f'          <PARENT>{parent}</PARENT>')
        xml.append(f'          <OPENINGBALANCE>{balance}</OPENINGBALANCE>')
        xml.append('        </LEDGER>')

    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


def generate_ledger_details_xml(ledger: Dict[str, Any]) -> str:
    """Generate XML response for a single ledger's details"""
    name = escape_xml(ledger.get('name', ''))
    parent = escape_xml(ledger.get('parent_group', ledger.get('parent', '')))
    balance = ledger.get('opening_balance', '0')
    address = escape_xml(ledger.get('address', ''))
    state = escape_xml(ledger.get('state', ''))
    country = escape_xml(ledger.get('country', 'India'))
    mobile = escape_xml(ledger.get('mobile', ''))
    gstin = escape_xml(ledger.get('gstin', ''))

    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')
    xml.append(f'        <LEDGER NAME="{name}">')
    xml.append(f'          <NAME>{name}</NAME>')
    xml.append(f'          <PARENT>{parent}</PARENT>')
    xml.append(f'          <OPENINGBALANCE>{balance}</OPENINGBALANCE>')
    xml.append(f'          <ADDRESS>{address}</ADDRESS>')
    xml.append(f'          <LEDSTATENAME>{state}</LEDSTATENAME>')
    xml.append(f'          <COUNTRYOFRESIDENCE>{country}</COUNTRYOFRESIDENCE>')
    xml.append(f'          <LEDGERMOBILE>{mobile}</LEDGERMOBILE>')
    xml.append(f'          <PARTYGSTIN>{gstin}</PARTYGSTIN>')
    xml.append('        </LEDGER>')
    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


# ============================================================================
# GROUP RESPONSES
# ============================================================================

def generate_groups_xml(groups: List[Dict[str, Any]]) -> str:
    """Generate XML response for group list"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')

    for group in groups:
        name = escape_xml(group.get('name', ''))
        parent = escape_xml(group.get('parent', ''))
        is_primary = 'Yes' if group.get('isPrimary', False) else 'No'

        xml.append(f'        <GROUP NAME="{name}">')
        xml.append(f'          <NAME>{name}</NAME>')
        xml.append(f'          <PARENT>{parent}</PARENT>')
        xml.append(f'          <ISPRIMARYGROUP>{is_primary}</ISPRIMARYGROUP>')
        xml.append('        </GROUP>')

    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


# ============================================================================
# INVENTORY / STOCK ITEM RESPONSES
# ============================================================================

def generate_inventory_xml(items: List[Dict[str, Any]]) -> str:
    """Generate XML response for stock items / inventory"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')

    for item in items:
        name = escape_xml(item.get('name', ''))
        parent = escape_xml(item.get('parent', ''))
        category = escape_xml(item.get('category', ''))
        base_units = escape_xml(item.get('baseUnits', 'Nos'))
        opening_bal = item.get('openingBalance', 0)
        opening_val = item.get('openingValue', 0)
        closing_bal = item.get('closingBalance', 0)
        closing_val = item.get('closingValue', 0)

        xml.append(f'        <STOCKITEM NAME="{name}">')
        xml.append(f'          <NAME>{name}</NAME>')
        xml.append(f'          <PARENT>{parent}</PARENT>')
        xml.append(f'          <CATEGORY>{category}</CATEGORY>')
        xml.append(f'          <BASEUNITS>{base_units}</BASEUNITS>')
        xml.append(f'          <OPENINGBALANCE>{opening_bal} {base_units}</OPENINGBALANCE>')
        xml.append(f'          <OPENINGVALUE>{opening_val}</OPENINGVALUE>')
        xml.append(f'          <CLOSINGBALANCE>{closing_bal} {base_units}</CLOSINGBALANCE>')
        xml.append(f'          <CLOSINGVALUE>{closing_val}</CLOSINGVALUE>')
        xml.append('        </STOCKITEM>')

    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


# ============================================================================
# GODOWN / WAREHOUSE RESPONSES
# ============================================================================

def generate_godowns_xml(godowns: List[Dict[str, Any]]) -> str:
    """Generate XML response for godowns / warehouses"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')

    for godown in godowns:
        name = escape_xml(godown.get('name', ''))
        parent = escape_xml(godown.get('parent', ''))
        address = escape_xml(godown.get('address', ''))
        pincode = escape_xml(godown.get('pincode', ''))

        xml.append(f'        <GODOWN NAME="{name}">')
        xml.append(f'          <NAME>{name}</NAME>')
        xml.append(f'          <PARENT>{parent}</PARENT>')
        xml.append(f'          <ADDRESS>{address}</ADDRESS>')
        xml.append(f'          <PINCODE>{pincode}</PINCODE>')
        xml.append('        </GODOWN>')

    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


# ============================================================================
# IMPORT RESPONSES (CREATE/UPDATE OPERATIONS)
# ============================================================================

def generate_import_success_xml(
    created: int = 1,
    altered: int = 0,
    deleted: int = 0,
    exceptions: int = 0,
    last_voucher_id: int = 0,
    error_message: str = None
) -> str:
    """Generate XML response for successful import operations"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append(f'    <STATUS>{0 if exceptions > 0 else 1}</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <IMPORTRESULT>')
    xml.append(f'        <CREATED>{created}</CREATED>')
    xml.append(f'        <ALTERED>{altered}</ALTERED>')
    xml.append(f'        <DELETED>{deleted}</DELETED>')
    xml.append(f'        <LASTVCHID>{last_voucher_id}</LASTVCHID>')
    xml.append(f'        <LASTMID>0</LASTMID>')
    xml.append(f'        <COMBINED>0</COMBINED>')
    xml.append(f'        <IGNORED>0</IGNORED>')
    xml.append(f'        <ERRORS>{exceptions}</ERRORS>')
    if error_message:
        xml.append(f'        <LINEERROR>{escape_xml(error_message)}</LINEERROR>')
    xml.append('      </IMPORTRESULT>')
    xml.append('    </DATA>')
    xml.append('    <DESC>')
    xml.append('      <CMPINFOEX>')
    xml.append('        <IDINFO>')
    xml.append(f'          <LASTCREATEDVCHID>{last_voucher_id}</LASTCREATEDVCHID>')
    xml.append('        </IDINFO>')
    xml.append('      </CMPINFOEX>')
    xml.append('    </DESC>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


def generate_import_error_xml(error_message: str) -> str:
    """Generate XML response for failed import operations"""
    return generate_import_success_xml(
        created=0,
        altered=0,
        deleted=0,
        exceptions=1,
        last_voucher_id=0,
        error_message=error_message
    )


# ============================================================================
# CONNECTION CHECK RESPONSE
# ============================================================================

def generate_connection_response_xml() -> str:
    """Generate XML response for connection check"""
    return generate_companies_xml([{"name": "Connection Test Successful"}])


# ============================================================================
# ERROR RESPONSE
# ============================================================================

def generate_error_xml(error_message: str) -> str:
    """Generate XML response for errors"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>0</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append(f'      <ERROR>{escape_xml(error_message)}</ERROR>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


# ============================================================================
# VOUCHER TYPE RESPONSES
# ============================================================================

def generate_voucher_types_xml(voucher_types: List[str]) -> str:
    """Generate XML response for voucher types"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')

    for vtype in voucher_types:
        name = escape_xml(vtype)
        xml.append(f'        <VOUCHERTYPE NAME="{name}">')
        xml.append(f'          <NAME>{name}</NAME>')
        xml.append('        </VOUCHERTYPE>')

    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


# ============================================================================
# STOCK GROUP RESPONSES
# ============================================================================

def generate_stock_groups_xml() -> str:
    """Generate XML response for stock groups"""
    stock_groups = [
        {"name": "Primary", "parent": ""},
        {"name": "Electronics", "parent": "Primary"},
        {"name": "Raw Materials", "parent": "Primary"},
        {"name": "Office Supplies", "parent": "Primary"},
        {"name": "Furniture", "parent": "Primary"},
        {"name": "Laptops", "parent": "Electronics"},
        {"name": "Desktops", "parent": "Electronics"},
        {"name": "Monitors", "parent": "Electronics"},
        {"name": "Accessories", "parent": "Electronics"},
        {"name": "Printers", "parent": "Electronics"},
        {"name": "Steel", "parent": "Raw Materials"},
        {"name": "Aluminium", "parent": "Raw Materials"},
        {"name": "Copper", "parent": "Raw Materials"},
        {"name": "Plastics", "parent": "Raw Materials"},
        {"name": "Paper", "parent": "Office Supplies"},
        {"name": "Cartridges", "parent": "Office Supplies"},
        {"name": "Stationery", "parent": "Office Supplies"},
        {"name": "Chairs", "parent": "Furniture"},
        {"name": "Tables", "parent": "Furniture"},
        {"name": "Storage", "parent": "Furniture"},
    ]

    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')

    for group in stock_groups:
        name = escape_xml(group['name'])
        parent = escape_xml(group['parent'])
        xml.append(f'        <STOCKGROUP NAME="{name}">')
        xml.append(f'          <NAME>{name}</NAME>')
        xml.append(f'          <PARENT>{parent}</PARENT>')
        xml.append('        </STOCKGROUP>')

    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)


# ============================================================================
# UNITS OF MEASURE RESPONSES
# ============================================================================

def generate_units_xml() -> str:
    """Generate XML response for units of measure"""
    units = [
        {"name": "Nos", "formalName": "Numbers"},
        {"name": "Kg", "formalName": "Kilograms"},
        {"name": "Gm", "formalName": "Grams"},
        {"name": "Mtr", "formalName": "Meters"},
        {"name": "Ltr", "formalName": "Liters"},
        {"name": "Box", "formalName": "Boxes"},
        {"name": "Pcs", "formalName": "Pieces"},
        {"name": "Ream", "formalName": "Reams"},
        {"name": "Dz", "formalName": "Dozens"},
        {"name": "Set", "formalName": "Sets"},
        {"name": "Pair", "formalName": "Pairs"},
        {"name": "Sqft", "formalName": "Square Feet"},
        {"name": "Sqm", "formalName": "Square Meters"},
    ]

    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<ENVELOPE>')
    xml.append('  <HEADER>')
    xml.append('    <VERSION>1</VERSION>')
    xml.append('    <STATUS>1</STATUS>')
    xml.append('  </HEADER>')
    xml.append('  <BODY>')
    xml.append('    <DATA>')
    xml.append('      <COLLECTION>')

    for unit in units:
        name = escape_xml(unit['name'])
        formal = escape_xml(unit['formalName'])
        xml.append(f'        <UNIT NAME="{name}">')
        xml.append(f'          <NAME>{name}</NAME>')
        xml.append(f'          <FORMALNAME>{formal}</FORMALNAME>')
        xml.append('        </UNIT>')

    xml.append('      </COLLECTION>')
    xml.append('    </DATA>')
    xml.append('  </BODY>')
    xml.append('</ENVELOPE>')

    return '\n'.join(xml)
