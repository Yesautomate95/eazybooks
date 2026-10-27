"""
Direct HTTP connector to Tally ERP 9 / Tally Prime XML API.

Tally runs an HTTP server (default port 9000) that accepts XML POST requests
and returns XML responses. This module talks directly to Tally — no Electron
middleman needed.

Three request types supported (per Tally XML spec):
  - Export  : Retrieve data (reports, collections, objects, functions)
  - Import  : Push data (masters, vouchers — create/alter/delete)
  - Execute : Run TDL actions (e.g. Sync)

Usage:
    from applicationca.services.tally_connector import tally_connector

    # Check connection
    connected = await tally_connector.check_connection()

    # Get companies
    companies = await tally_connector.get_companies()

    # Push raw XML
    result = await tally_connector.push_xml(xml_string, company="My Co")
"""

import re
import httpx
import xml.etree.ElementTree as ET
import logging
from typing import List, Dict, Optional, Any

from config.settings import settings

logger = logging.getLogger(__name__)


def _xml_escape(text: str) -> str:
    """Escape special XML characters and strip invalid XML characters from text content."""
    if not text:
        return ""
    s = str(text)
    # Remove characters that are invalid in XML 1.0:
    # Valid: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
    s = "".join(
        ch for ch in s
        if ch in ("\x09", "\x0A", "\x0D")
        or ("\x20" <= ch <= "\uD7FF")
        or ("\uE000" <= ch <= "\uFFFD")
        or ("\U00010000" <= ch <= "\U0010FFFF")
    )
    return (
        s
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ── Ledger XML body builder ────────────────────────────────────────
# Shared between create_ledger() and alter_ledger() so the tag set stays in
# lock-step. Emits only the tags whose values are non-empty (alter_mode also
# treats None as "don't emit"), and combines legacy + canonical tag names so
# both Tally.ERP 9 and TallyPrime accept the payload.
#
# Schema reference: ShwetaSoftwares/excel-to-tally-templates
# (Master-LedgerMaster-xml-tags.xml) and help.tallysolutions.com/sample-xml/.

_GST_REG_TYPES = {"Regular", "Composition", "Composite Dealer", "Consumer", "Unregistered", "Unknown"}


def _yn(v: Optional[bool]) -> Optional[str]:
    """Map a tri-state bool to Tally Yes/No, or None to skip the tag."""
    if v is None:
        return None
    return "Yes" if v else "No"


def _build_ledger_body(
    action: str,
    primary_name: str,
    new_name: Optional[str],
    parent_group: Optional[str],
    opening_balance: Optional[str],
    alias: Optional[str],
    mailing_name: Optional[str],
    address: Optional[str],
    address_lines: Optional[List[str]],
    pincode: Optional[str],
    state: Optional[str],
    country: Optional[str],
    contact_person: Optional[str],
    phone: Optional[str],
    mobile: Optional[str],
    fax: Optional[str],
    email: Optional[str],
    email_cc: Optional[str],
    pan: Optional[str],
    gstin: Optional[str],
    gst_registration_type: Optional[str],
    gst_applicable_from: Optional[str],
    bill_wise: Optional[bool],
    bill_credit_period: Optional[str],
    credit_limit: Optional[str],
    bank_name: Optional[str],
    account_number: Optional[str],
    ifsc_code: Optional[str],
    payment_favouring: Optional[str],
    affects_stock: Optional[bool],
    cost_centres_applicable: Optional[bool],
    narration: Optional[str],
    description: Optional[str],
    alter_mode: bool = False,
) -> str:
    """Build the <LEDGER>...</LEDGER> body. In alter_mode, only tags whose
    arguments are not None are emitted; otherwise empty strings are also
    skipped to keep create requests clean."""

    def _has(v) -> bool:
        if v is None:
            return False
        if alter_mode:
            return True
        if isinstance(v, str):
            return v.strip() != ""
        return True

    parts: List[str] = [f'<LEDGER NAME="{_xml_escape(primary_name)}" Action="{action}">']

    if action == "Alter" and new_name and new_name != primary_name:
        # TallyPrime expects rename via NAME.LIST so both old + new are tracked
        parts.append("  <NAME.LIST>")
        parts.append(f"    <NAME>{_xml_escape(new_name)}</NAME>")
        parts.append("  </NAME.LIST>")
    elif action == "Create":
        parts.append(f"  <NAME>{_xml_escape(primary_name)}</NAME>")

    if _has(parent_group):
        parts.append(f"  <PARENT>{_xml_escape(parent_group)}</PARENT>")
    if _has(alias):
        parts.append(f"  <ADDITIONALNAME>{_xml_escape(alias)}</ADDITIONALNAME>")
    if _has(opening_balance):
        parts.append(f"  <OPENINGBALANCE>{_xml_escape(opening_balance)}</OPENINGBALANCE>")
    if _has(description):
        parts.append(f"  <DESCRIPTION>{_xml_escape(description)}</DESCRIPTION>")
    if _has(narration):
        parts.append(f"  <NARRATION>{_xml_escape(narration)}</NARRATION>")

    # Address — accept multi-line list, fall back to single-line `address`
    lines = [a for a in (address_lines or []) if a and a.strip()]
    if not lines and _has(address):
        lines = [address]
    if lines:
        parts.append("  <ADDRESS.LIST>")
        for line in lines:
            parts.append(f"    <ADDRESS>{_xml_escape(line)}</ADDRESS>")
        parts.append("  </ADDRESS.LIST>")

    if _has(pincode):
        parts.append(f"  <PINCODE>{_xml_escape(pincode)}</PINCODE>")
    if _has(state):
        # emit both canonical TallyPrime and legacy ERP 9 names — Tally tolerates both
        parts.append(f"  <STATENAME>{_xml_escape(state)}</STATENAME>")
        parts.append(f"  <LEDSTATENAME>{_xml_escape(state)}</LEDSTATENAME>")
    if _has(country):
        parts.append(f"  <COUNTRYNAME>{_xml_escape(country)}</COUNTRYNAME>")
        parts.append(f"  <COUNTRYOFRESIDENCE>{_xml_escape(country)}</COUNTRYOFRESIDENCE>")

    # Mailing details container — TallyPrime's canonical place for printed name + address-on-record.
    if _has(mailing_name) or lines:
        parts.append("  <LEDMAILINGDETAILS.LIST>")
        if _has(gst_applicable_from):
            parts.append(f"    <APPLICABLEFROM>{_xml_escape(gst_applicable_from)}</APPLICABLEFROM>")
        if _has(mailing_name):
            parts.append(f"    <MAILINGNAME>{_xml_escape(mailing_name)}</MAILINGNAME>")
        if lines:
            parts.append("    <ADDRESS.LIST>")
            for line in lines:
                parts.append(f"      <ADDRESS>{_xml_escape(line)}</ADDRESS>")
            parts.append("    </ADDRESS.LIST>")
        if _has(state):
            parts.append(f"    <STATE>{_xml_escape(state)}</STATE>")
        if _has(country):
            parts.append(f"    <COUNTRY>{_xml_escape(country)}</COUNTRY>")
        if _has(pincode):
            parts.append(f"    <PINCODE>{_xml_escape(pincode)}</PINCODE>")
        parts.append("  </LEDMAILINGDETAILS.LIST>")

    # Contact
    if _has(contact_person):
        parts.append(f"  <LEDGERCONTACT>{_xml_escape(contact_person)}</LEDGERCONTACT>")
    if _has(phone):
        parts.append(f"  <LEDGERPHONE>{_xml_escape(phone)}</LEDGERPHONE>")
    if _has(mobile):
        parts.append(f"  <LEDGERMOBILE>{_xml_escape(mobile)}</LEDGERMOBILE>")
    if _has(fax):
        parts.append(f"  <LEDGERFAX>{_xml_escape(fax)}</LEDGERFAX>")
    if _has(email):
        parts.append(f"  <EMAIL>{_xml_escape(email)}</EMAIL>")
    if _has(email_cc):
        parts.append(f"  <EMAILCC>{_xml_escape(email_cc)}</EMAILCC>")

    # Statutory
    if _has(pan):
        parts.append(f"  <INCOMETAXNUMBER>{_xml_escape(pan)}</INCOMETAXNUMBER>")
    if _has(gstin):
        parts.append(f"  <PARTYGSTIN>{_xml_escape(gstin)}</PARTYGSTIN>")
    if _has(gst_registration_type):
        # Validate against known set; emit as-is regardless (Tally may accept extras)
        if gst_registration_type not in _GST_REG_TYPES:
            logger.warning(
                "Unknown GST registration type %r — sending as-is. Expected one of %s",
                gst_registration_type, sorted(_GST_REG_TYPES),
            )
        parts.append(f"  <GSTREGISTRATIONTYPE>{_xml_escape(gst_registration_type)}</GSTREGISTRATIONTYPE>")
    if _has(gstin) or _has(gst_registration_type):
        # Canonical history container — TallyPrime expects this for newer companies
        parts.append("  <LEDGSTREGDETAILS.LIST>")
        if _has(gst_applicable_from):
            parts.append(f"    <APPLICABLEFROM>{_xml_escape(gst_applicable_from)}</APPLICABLEFROM>")
        if _has(gst_registration_type):
            parts.append(f"    <GSTREGISTRATIONTYPE>{_xml_escape(gst_registration_type)}</GSTREGISTRATIONTYPE>")
        if _has(state):
            parts.append(f"    <STATE>{_xml_escape(state)}</STATE>")
        if _has(gstin):
            parts.append(f"    <GSTIN>{_xml_escape(gstin)}</GSTIN>")
        parts.append("  </LEDGSTREGDETAILS.LIST>")

    # Bill-wise & credit
    bw = _yn(bill_wise)
    if bw is not None:
        parts.append(f"  <ISBILLWISEON>{bw}</ISBILLWISEON>")
    if _has(bill_credit_period):
        parts.append(f"  <BILLCREDITPERIOD>{_xml_escape(bill_credit_period)}</BILLCREDITPERIOD>")
    if _has(credit_limit):
        parts.append(f"  <CREDITLIMIT>{_xml_escape(credit_limit)}</CREDITLIMIT>")

    # Bank (only meaningful for Bank Accounts parent)
    if _has(bank_name) or _has(account_number) or _has(ifsc_code):
        parts.append("  <PAYMENTDETAILS.LIST>")
        if _has(bank_name):
            parts.append(f"    <BANKNAME>{_xml_escape(bank_name)}</BANKNAME>")
        if _has(account_number):
            parts.append(f"    <ACCOUNTNUMBER>{_xml_escape(account_number)}</ACCOUNTNUMBER>")
        if _has(ifsc_code):
            parts.append(f"    <IFSCODE>{_xml_escape(ifsc_code)}</IFSCODE>")
        if _has(payment_favouring):
            parts.append(f"    <PAYMENTFAVOURING>{_xml_escape(payment_favouring)}</PAYMENTFAVOURING>")
        parts.append("  </PAYMENTDETAILS.LIST>")

    # Behaviour flags
    aff = _yn(affects_stock)
    if aff is not None:
        parts.append(f"  <AFFECTSSTOCK>{aff}</AFFECTSSTOCK>")
    cc = _yn(cost_centres_applicable)
    if cc is not None:
        parts.append(f"  <ISCOSTCENTRESON>{cc}</ISCOSTCENTRESON>")

    parts.append("</LEDGER>")
    return "\n".join(parts)



class TallyConnector:
    """Direct HTTP client for Tally's XML API (port 9000)."""

    def __init__(self, base_url: str = None, timeout: int = 30):
        self.base_url = base_url or settings.TALLY_URL
        self.timeout = timeout
        # In-memory ledger cache: { "company:type": { "data": [...], "expires": timestamp } }
        self._ledger_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 300  # 5 minutes

    # ==========================================================
    # Low-level transport
    # ==========================================================

    async def _post_xml(self, xml_payload: str) -> str:
        """POST raw XML to Tally and return raw XML response text."""
        logger.debug(f"Sending XML request to Tally at {self.base_url}")
        logger.debug(f"Request payload:\n{xml_payload[:500]}...")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.base_url,
                content=xml_payload.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
            )
            resp.raise_for_status()
            logger.debug(f"Tally response status: {resp.status_code}")
            return resp.text

    @staticmethod
    def _dump_xml_debug(group_name: str, request_xml: str, response_xml: str):
        """
        Save raw XML request and response to tally_debug_logs/ for debugging.

        Files are written per group name (overwritten on each call):
          tally_debug_logs/Sundry_Creditors_request.xml
          tally_debug_logs/Sundry_Creditors_response.xml
        """
        import os
        debug_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "tally_debug_logs",
        )
        try:
            os.makedirs(debug_dir, exist_ok=True)
            safe_name = group_name.replace(" ", "_").replace("&", "and")
            req_path = os.path.join(debug_dir, f"{safe_name}_request.xml")
            res_path = os.path.join(debug_dir, f"{safe_name}_response.xml")
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(request_xml)
            with open(res_path, "w", encoding="utf-8") as f:
                f.write(response_xml)
            logger.debug(f"Tally debug XML saved for group '{group_name}' in {debug_dir}")
        except Exception as e:
            logger.warning(f"Failed to save Tally debug XML: {e}")

    @staticmethod
    def _sanitize_xml(xml_text: str) -> str:
        """Remove invalid XML character references from Tally responses.

        Tally sometimes includes numeric character references (&#N;) that
        point to control characters invalid in XML 1.0.  Valid char refs are:
        #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF].
        """
        def _valid_char_ref(m: re.Match) -> str:
            full = m.group(0)
            num_str = m.group(1)
            code = int(num_str, 16) if num_str.startswith(('x', 'X')) else int(num_str)
            if code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF \
               or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF:
                return full  # keep valid references
            return ""  # strip invalid references

        return re.sub(r'&#(x?[0-9a-fA-F]+);', _valid_char_ref, xml_text)

    @staticmethod
    def _parse_xml(xml_text: str) -> ET.Element:
        """Parse XML string into an ElementTree root element."""
        return ET.fromstring(TallyConnector._sanitize_xml(xml_text))

    # ==========================================================
    # Connection check
    # ==========================================================

    async def check_connection(self) -> bool:
        """Verify Tally is reachable by fetching the company list."""
        try:
            companies = await self.get_companies()
            return len(companies) > 0
        except Exception as e:
            logger.warning(f"Tally connection check failed: {e}")
            return False

    # ==========================================================
    #  EXPORT — Read data from Tally
    # ==========================================================

    # ---- Export Data (TDL Reports) ----------------------------

    async def export_report(
        self,
        report_name: str,
        company: str = None,
        static_vars: Dict[str, str] = None,
        tdl_message: str = None,
    ) -> str:
        """
        Export a TDL Report from Tally.

        TALLYREQUEST=Export, TYPE=Data, ID=<report_name>

        Returns raw XML response string.
        """
        sv_block = self._build_static_vars(company, static_vars)
        tdl_block = f"<TDL><TDLMESSAGE>{tdl_message}</TDLMESSAGE></TDL>" if tdl_message else ""

        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>{_xml_escape(report_name)}</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {sv_block}
      </STATICVARIABLES>
      {tdl_block}
    </DESC>
  </BODY>
</ENVELOPE>"""
        return await self._post_xml(xml_req)

    # ---- Export Collection -------------------------------------

    async def export_collection(
        self,
        collection_name: str,
        collection_type: str,
        fetch_fields: List[str] = None,
        company: str = None,
        native_methods: List[str] = None,
        tdl_message: str = None,
    ) -> str:
        """
        Export a Collection from Tally.

        TALLYREQUEST=Export, TYPE=Collection, ID=<collection_name>

        If the collection doesn't exist in Tally, provide `collection_type`
        and `native_methods`/`fetch_fields` — they'll be defined in <TDL>.

        Returns raw XML response string.
        """
        sv_block = self._build_static_vars(company)

        # Build the <COLLECTION> TDL definition
        coll_def_parts = [f'<COLLECTION NAME="{_xml_escape(collection_name)}">']
        coll_def_parts.append(f"<TYPE>{_xml_escape(collection_type)}</TYPE>")

        if fetch_fields:
            coll_def_parts.append(f"<FETCH>{','.join(fetch_fields)}</FETCH>")

        if native_methods:
            for method in native_methods:
                coll_def_parts.append(f"<NATIVEMETHOD>{_xml_escape(method)}</NATIVEMETHOD>")

        coll_def_parts.append("</COLLECTION>")
        coll_def = "\n".join(coll_def_parts)

        extra_tdl = tdl_message or ""

        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>{_xml_escape(collection_name)}</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {sv_block}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          {coll_def}
          {extra_tdl}
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
        return await self._post_xml(xml_req)

    # ---- Export Object -----------------------------------------

    async def export_object(
        self,
        object_type: str,
        object_id: str,
        fetch_list: List[str] = None,
        company: str = None,
        local_formula: str = None,
    ) -> str:
        """
        Export a specific Object from Tally.

        TALLYREQUEST=Export, TYPE=Object, SUBTYPE=<object_type>, ID=<object_id>

        Returns raw XML response string.
        """
        sv_block = self._build_static_vars(company)

        fetch_block = ""
        if fetch_list:
            fetch_items = "\n".join(f"<FETCH>{_xml_escape(f)}</FETCH>" for f in fetch_list)
            fetch_block = f"<FETCHLIST>{fetch_items}</FETCHLIST>"

        tdl_block = ""
        if local_formula:
            tdl_block = f"""<TDL>
        <TDLMESSAGE>
          <OBJECT NAME="{_xml_escape(object_type)}" ISINITIALIZE="Yes">
            <LOCALFORMULA>{local_formula}</LOCALFORMULA>
          </OBJECT>
        </TDLMESSAGE>
      </TDL>"""

        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Object</TYPE>
    <SUBTYPE>{_xml_escape(object_type)}</SUBTYPE>
    <ID TYPE="Name">{_xml_escape(object_id)}</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {sv_block}
      </STATICVARIABLES>
      {fetch_block}
      {tdl_block}
    </DESC>
  </BODY>
</ENVELOPE>"""
        return await self._post_xml(xml_req)

    # ---- Export Function ----------------------------------------

    async def export_function(
        self,
        function_name: str,
        params: List[Dict[str, str]] = None,
        tdl_formulas: str = None,
    ) -> str:
        """
        Evaluate a TDL function and return the result.

        TALLYREQUEST=Export, TYPE=Function, ID=<function_name>

        params: list of dicts with 'value' and optional 'type' keys.
        Returns raw XML response string.
        """
        func_param_block = ""
        if params:
            param_items = []
            for p in params:
                ptype = p.get("type", "")
                val = _xml_escape(p["value"])
                if ptype:
                    param_items.append(f'<PARAM TYPE="{_xml_escape(ptype)}">{val}</PARAM>')
                else:
                    param_items.append(f"<PARAM>{val}</PARAM>")
            func_param_block = "<FUNCPARAMLIST>" + "\n".join(param_items) + "</FUNCPARAMLIST>"

        tdl_block = ""
        if tdl_formulas:
            tdl_block = f"<TDL><TDLMESSAGE>{tdl_formulas}</TDLMESSAGE></TDL>"

        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Function</TYPE>
    <ID>{_xml_escape(function_name)}</ID>
  </HEADER>
  <BODY>
    <DESC>
      {func_param_block}
      {tdl_block}
    </DESC>
  </BODY>
</ENVELOPE>"""
        return await self._post_xml(xml_req)

    # ==========================================================
    #  High-level Export helpers (commonly used collections)
    # ==========================================================

    async def get_companies(self) -> List[Dict[str, str]]:
        """Fetch all open companies from Tally."""
        raw = await self.export_collection(
            collection_name="CompanyList",
            collection_type="Company",
            fetch_fields=["NAME"],
        )
        root = self._parse_xml(raw)
        companies = []
        for comp in root.iter("COMPANY"):
            name = comp.findtext("NAME") or comp.get("NAME", "")
            if name:
                companies.append({"name": name.strip()})
        return companies

    async def get_ledgers(self, company: str) -> List[Dict[str, str]]:
        """Fetch all ledgers for a company."""
        raw = await self.export_collection(
            collection_name="AllLedgers",
            collection_type="Ledger",
            fetch_fields=["NAME", "PARENT", "OPENINGBALANCE", "CLOSINGBALANCE"],
            company=company,
        )
        root = self._parse_xml(raw)
        ledgers = []
        for ledger in root.iter("LEDGER"):
            name = ledger.findtext("NAME") or ledger.get("NAME", "")
            if name:
                ledgers.append({
                    "name": name.strip(),
                    "parent": (ledger.findtext("PARENT") or "").strip(),
                    "openingBalance": (ledger.findtext("OPENINGBALANCE") or "0").strip(),
                    "closingBalance": (ledger.findtext("CLOSINGBALANCE") or "0").strip(),
                })
        return ledgers

    async def get_groups(self, company: str) -> List[Dict[str, str]]:
        """Fetch all groups for a company."""
        raw = await self.export_collection(
            collection_name="AllGroups",
            collection_type="Group",
            fetch_fields=["NAME", "PARENT"],
            company=company,
        )
        root = self._parse_xml(raw)
        groups = []
        for group in root.iter("GROUP"):
            name = group.findtext("NAME") or group.get("NAME", "")
            if name:
                groups.append({
                    "name": name.strip(),
                    "parent": (group.findtext("PARENT") or "").strip(),
                })
        return groups

    async def get_inventory(self, company: str) -> List[Dict[str, str]]:
        """Fetch all stock items for a company."""
        raw = await self.export_collection(
            collection_name="AllStockItems",
            collection_type="StockItem",
            fetch_fields=[
                "NAME", "PARENT", "BASEUNITS",
                "OPENINGBALANCE", "OPENINGVALUE",
                "CLOSINGBALANCE", "CLOSINGVALUE",
            ],
            company=company,
        )
        root = self._parse_xml(raw)
        items = []
        for item in root.iter("STOCKITEM"):
            name = item.findtext("NAME") or item.get("NAME", "")
            if name:
                items.append({
                    "name": name.strip(),
                    "parent": (item.findtext("PARENT") or "").strip(),
                    "baseUnits": (item.findtext("BASEUNITS") or "Nos").strip(),
                    "openingBalance": (item.findtext("OPENINGBALANCE") or "0").strip(),
                    "openingValue": (item.findtext("OPENINGVALUE") or "0").strip(),
                    "closingBalance": (item.findtext("CLOSINGBALANCE") or "0").strip(),
                    "closingValue": (item.findtext("CLOSINGVALUE") or "0").strip(),
                })
        return items

    async def get_godowns(self, company: str) -> List[Dict[str, str]]:
        """Fetch all godowns for a company."""
        raw = await self.export_collection(
            collection_name="AllGodowns",
            collection_type="Godown",
            fetch_fields=["NAME", "PARENT"],
            company=company,
        )
        root = self._parse_xml(raw)
        godowns = []
        for godown in root.iter("GODOWN"):
            name = godown.findtext("NAME") or godown.get("NAME", "")
            if name:
                godowns.append({
                    "name": name.strip(),
                    "parent": (godown.findtext("PARENT") or "").strip(),
                })
        return godowns

    async def get_ledgers_by_group(
        self, company: str, group_name: str
    ) -> List[Dict[str, str]]:
        """
        Fetch all ledgers (leaf nodes only) under a specific parent group tree
        (including arbitrarily nested sub-groups).

        Uses Tally's ``<TYPE>Ledger</TYPE>`` with ``<CHILDOF>`` directive to
        ensure only actual ledger objects are returned (not groups/subgroups).
        CHILDOF handles recursive nesting automatically.

        Fetches Name, Parent, GUID, ClosingBalance, and PartyGSTIN via NATIVEMETHOD.
        """
        import time

        cache_key = f"{company or ''}:group:{group_name}"
        cached = self._ledger_cache.get(cache_key)
        if cached and cached["expires"] > time.time():
            return cached["data"]

        escaped_group = _xml_escape(group_name)

        # Use <TYPE>Ledger</TYPE> to ensure only actual ledgers are returned (not groups).
        # CHILDOF + BELONGSTO=Yes traverses the full hierarchy recursively,
        # including ledgers under sub-groups at any depth.
        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST>
    <TYPE>COLLECTION</TYPE>
    <ID>LedgersUnderGroup</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {self._build_static_vars(company)}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="LedgersUnderGroup">
            <TYPE>Ledger</TYPE>
            <CHILDOF>{escaped_group}</CHILDOF>
            <BELONGSTO>Yes</BELONGSTO>
            <NATIVEMETHOD>Name</NATIVEMETHOD>
            <NATIVEMETHOD>Parent</NATIVEMETHOD>
            <NATIVEMETHOD>GUID</NATIVEMETHOD>
            <NATIVEMETHOD>ClosingBalance</NATIVEMETHOD>
            <NATIVEMETHOD>PartyGSTIN</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""

        raw = await self._post_xml(xml_req)

        # Dump raw XML request + response to tally_debug_logs/ for debugging
        self._dump_xml_debug(group_name, xml_req, raw)

        root = self._parse_xml(raw)
        ledgers = []
        for ledger in root.iter("LEDGER"):
            name = ledger.findtext("NAME") or ledger.get("NAME", "")
            if name:
                ledgers.append({
                    "name": name.strip(),
                    "parent": (ledger.findtext("PARENT") or "").strip(),
                    "guid": (ledger.findtext("GUID") or "").strip(),
                    "closing_balance": (ledger.findtext("CLOSINGBALANCE") or "0").strip(),
                    "gstin": (ledger.findtext("PARTYGSTIN") or "").strip(),
                })

        logger.info(f"LedgersUnderGroup '{group_name}': fetched {len(ledgers)} ledgers (leaf nodes only)")

        self._ledger_cache[cache_key] = {
            "data": ledgers,
            "expires": time.time() + self._cache_ttl,
        }
        return ledgers

    async def get_grouped_ledgers_for_suggestion(
        self, company: str
    ) -> Dict[str, Any]:
        """
        Fetch all ledgers organized into three categories for AI ledger
        suggestion and dropdown population.

        Categories (each group is fetched via ``CHILD OF`` recursively):
          - party:    Sundry Debtors, Sundry Creditors
          - purchase: Direct Expenses, Indirect Expenses, Purchase Accounts
          - sales:    Direct Incomes, Indirect Incomes, Sales Accounts
        """
        PARTY_GROUPS = ["Sundry Debtors", "Sundry Creditors"]
        PURCHASE_GROUPS = ["Direct Expenses", "Indirect Expenses", "Purchase Accounts"]
        SALES_GROUPS = ["Direct Incomes", "Indirect Incomes", "Sales Accounts"]

        all_groups = PARTY_GROUPS + PURCHASE_GROUPS + SALES_GROUPS

        import asyncio
        tasks = {g: self.get_ledgers_by_group(company, g) for g in all_groups}
        fetched = await asyncio.gather(*tasks.values(), return_exceptions=True)

        # Build result dict keyed by snake_case group name
        group_data: Dict[str, List] = {}
        for group_name, data in zip(tasks.keys(), fetched):
            if isinstance(data, Exception):
                logger.warning(f"Failed to fetch ledgers for group '{group_name}': {data}")
                data = []
            group_data[group_name] = data

        # Combine groups into categories
        party_ledgers: Dict[str, List] = {}
        for g in PARTY_GROUPS:
            party_ledgers[g.lower().replace(" ", "_")] = group_data.get(g, [])

        purchase_ledgers: Dict[str, List] = {}
        for g in PURCHASE_GROUPS:
            purchase_ledgers[g.lower().replace(" ", "_")] = group_data.get(g, [])

        sales_ledgers: Dict[str, List] = {}
        for g in SALES_GROUPS:
            sales_ledgers[g.lower().replace(" ", "_")] = group_data.get(g, [])

        return {
            "party_ledgers": party_ledgers,
            "purchase_ledgers": purchase_ledgers,
            "sales_ledgers": sales_ledgers,
        }

    async def get_voucher_types(self, company: str) -> List[Dict[str, str]]:
        """Fetch all voucher types for a company."""
        raw = await self.export_collection(
            collection_name="VoucherTypesColl",
            collection_type="VoucherType",
            fetch_fields=["NAME", "PARENT"],
            company=company,
        )
        root = self._parse_xml(raw)
        types = []
        for vt in root.iter("VOUCHERTYPE"):
            name = vt.findtext("NAME") or vt.get("NAME", "")
            if name:
                types.append({
                    "name": name.strip(),
                    "parent": (vt.findtext("PARENT") or "").strip(),
                })
        return types

    async def get_ledger_details(
        self, company: str, ledger_name: str
    ) -> Dict[str, Any]:
        """Fetch detailed info for a single ledger (Object export)."""
        raw = await self.export_object(
            object_type="Ledger",
            object_id=ledger_name,
            fetch_list=[
                "Name", "Parent", "OpeningBalance",
                "Address", "LedgerPhone", "LedStateName",
                "CountryOfResidence", "PartyGSTIN",
            ],
            company=company,
        )
        root = self._parse_xml(raw)

        # Check status
        status = root.findtext(".//STATUS")
        if status == "0":
            return {"success": False, "error": "Ledger not found"}

        ledger_el = root.find(".//LEDGER")
        if ledger_el is None:
            return {"success": False, "error": "No ledger data in response"}

        return {
            "success": True,
            "ledger": {
                "name": (ledger_el.findtext("NAME") or ledger_name).strip(),
                "parent_group": (ledger_el.findtext("PARENT") or "").strip(),
                "opening_balance": (ledger_el.findtext("OPENINGBALANCE") or "0").strip(),
                "address": (ledger_el.findtext(".//ADDRESS") or "").strip(),
                "mobile": (ledger_el.findtext("LEDGERPHONE") or "").strip(),
                "state": (ledger_el.findtext("LEDSTATENAME") or "").strip(),
                "country": (ledger_el.findtext("COUNTRYOFRESIDENCE") or "").strip(),
                "gstin": (ledger_el.findtext("PARTYGSTIN") or "").strip(),
            },
        }

    async def get_trial_balance(self, company: str = None) -> str:
        """Export Trial Balance report from Tally. Returns raw XML."""
        return await self.export_report(
            report_name="Trial Balance",
            company=company,
            static_vars={"EXPLODEFLAG": "Yes"},
        )

    # ==========================================================
    #  IMPORT — Push data to Tally
    # ==========================================================

    async def import_masters(
        self,
        tally_message_xml: str,
        company: str = None,
        dup_strategy: str = "@@DUPCOMBINE",
    ) -> Dict[str, Any]:
        """
        Import master data (ledgers, groups, stock items, etc.) into Tally.

        TALLYREQUEST=Import, TYPE=Data, ID=All Masters

        tally_message_xml: The inner XML, e.g. <LEDGER>...</LEDGER> blocks.
                           Will be wrapped in <TALLYMESSAGE>.
        dup_strategy: @@DUPCOMBINE, @@DUPMODIFY, or @@DUPIGNORECOMBINE
        """
        sv_block = self._build_static_vars(company)

        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Import</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>All Masters</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <IMPORTDUPS>{_xml_escape(dup_strategy)}</IMPORTDUPS>
        {sv_block}
      </STATICVARIABLES>
    </DESC>
    <DATA>
      <TALLYMESSAGE xmlns:UDF="TallyUDF">
        {tally_message_xml}
      </TALLYMESSAGE>
    </DATA>
  </BODY>
</ENVELOPE>"""
        raw = await self._post_xml(xml_req)
        logger.info(f"Tally master import raw response ({len(raw)} bytes):\n{raw}")
        return self._parse_import_response(raw)

    async def import_vouchers(
        self,
        tally_message_xml: str,
        company: str = None,
    ) -> Dict[str, Any]:
        """
        Import voucher data into Tally.

        TALLYREQUEST=Import, TYPE=Data, ID=Vouchers

        tally_message_xml: The inner XML containing <VOUCHER> blocks.
        """
        sv_block = self._build_static_vars(company)

        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Import</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>Vouchers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        {sv_block}
      </STATICVARIABLES>
    </DESC>
    <DATA>
      <TALLYMESSAGE xmlns:UDF="TallyUDF">
        {tally_message_xml}
      </TALLYMESSAGE>
    </DATA>
  </BODY>
</ENVELOPE>"""
        raw = await self._post_xml(xml_req)
        return self._parse_import_response(raw)

    async def push_xml(self, xml_data: str, company: str = None) -> Dict[str, Any]:
        """
        Push arbitrary XML to Tally.

        If xml_data already contains a full <ENVELOPE>, it is sent as-is.
        Otherwise it's wrapped in an import envelope (ID=All Masters).
        """
        if "<ENVELOPE" in xml_data.upper():
            payload = xml_data
        else:
            payload = self._wrap_import_envelope(xml_data, company)

        logger.info(f"Pushing XML to Tally ({len(payload)} bytes)")
        logger.info(f"XML payload (first 2000 chars):\n{payload[:2000]}")
        raw = await self._post_xml(payload)
        logger.info(f"Tally raw response ({len(raw)} bytes):\n{raw}")
        return self._parse_import_response(raw)

    # ---- Convenience import helpers ----------------------------

    async def create_ledger(
        self,
        company: str,
        name: str,
        parent_group: str,
        opening_balance: str = "0",
        # ── Identity ──
        alias: str = "",
        mailing_name: str = "",
        # ── Address ──
        address: str = "",  # single-line legacy; if address_lines provided, this is ignored
        address_lines: Optional[List[str]] = None,
        pincode: str = "",
        state: str = "",
        country: str = "India",
        # ── Contact ──
        contact_person: str = "",
        phone: str = "",
        mobile: str = "",
        fax: str = "",
        email: str = "",
        email_cc: str = "",
        # ── Statutory ──
        pan: str = "",
        gstin: str = "",
        gst_registration_type: str = "",
        gst_applicable_from: str = "",  # YYYYMMDD
        # ── Bill-wise / Credit ──
        bill_wise: Optional[bool] = None,
        bill_credit_period: str = "",
        credit_limit: str = "",
        # ── Bank (only for Bank Accounts parent group) ──
        bank_name: str = "",
        account_number: str = "",
        ifsc_code: str = "",
        payment_favouring: str = "",
        # ── Behaviour flags ──
        affects_stock: Optional[bool] = None,
        cost_centres_applicable: Optional[bool] = None,
        # ── Notes ──
        narration: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        """Create a new ledger in Tally.

        Tags are emitted conditionally — empty values are skipped to avoid
        ambiguous behaviour where Tally treats empty tags differently from
        missing tags. Legacy tag names (LEDSTATENAME, COUNTRYOFRESIDENCE) are
        emitted alongside canonical names (STATENAME, COUNTRYNAME) for
        backward compatibility with existing ledgers.

        Schema reference: TallyPrime master ledger import. See
        github.com/ShwetaSoftwares/excel-to-tally-templates Master-LedgerMaster-xml-tags.xml
        and help.tallysolutions.com/sample-xml/.
        """
        body = _build_ledger_body(
            action="Create",
            primary_name=name,
            new_name=None,
            parent_group=parent_group,
            opening_balance=opening_balance,
            alias=alias,
            mailing_name=mailing_name,
            address=address,
            address_lines=address_lines,
            pincode=pincode,
            state=state,
            country=country,
            contact_person=contact_person,
            phone=phone,
            mobile=mobile,
            fax=fax,
            email=email,
            email_cc=email_cc,
            pan=pan,
            gstin=gstin,
            gst_registration_type=gst_registration_type,
            gst_applicable_from=gst_applicable_from,
            bill_wise=bill_wise,
            bill_credit_period=bill_credit_period,
            credit_limit=credit_limit,
            bank_name=bank_name,
            account_number=account_number,
            ifsc_code=ifsc_code,
            payment_favouring=payment_favouring,
            affects_stock=affects_stock,
            cost_centres_applicable=cost_centres_applicable,
            narration=narration,
            description=description,
        )
        return await self.import_masters(body, company=company)

    async def alter_ledger(
        self,
        company: str,
        current_name: str,
        new_name: Optional[str] = None,
        parent_group: Optional[str] = None,
        opening_balance: Optional[str] = None,
        alias: Optional[str] = None,
        mailing_name: Optional[str] = None,
        address: Optional[str] = None,
        address_lines: Optional[List[str]] = None,
        pincode: Optional[str] = None,
        state: Optional[str] = None,
        country: Optional[str] = None,
        contact_person: Optional[str] = None,
        phone: Optional[str] = None,
        mobile: Optional[str] = None,
        fax: Optional[str] = None,
        email: Optional[str] = None,
        email_cc: Optional[str] = None,
        pan: Optional[str] = None,
        gstin: Optional[str] = None,
        gst_registration_type: Optional[str] = None,
        gst_applicable_from: Optional[str] = None,
        bill_wise: Optional[bool] = None,
        bill_credit_period: Optional[str] = None,
        credit_limit: Optional[str] = None,
        bank_name: Optional[str] = None,
        account_number: Optional[str] = None,
        ifsc_code: Optional[str] = None,
        payment_favouring: Optional[str] = None,
        affects_stock: Optional[bool] = None,
        cost_centres_applicable: Optional[bool] = None,
        narration: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Alter an existing ledger in Tally.

        Only fields explicitly passed (non-None) are emitted. The ledger is
        identified by ``current_name``; pass ``new_name`` to rename.
        """
        body = _build_ledger_body(
            action="Alter",
            primary_name=current_name,
            new_name=new_name,
            parent_group=parent_group,
            opening_balance=opening_balance,
            alias=alias,
            mailing_name=mailing_name,
            address=address,
            address_lines=address_lines,
            pincode=pincode,
            state=state,
            country=country,
            contact_person=contact_person,
            phone=phone,
            mobile=mobile,
            fax=fax,
            email=email,
            email_cc=email_cc,
            pan=pan,
            gstin=gstin,
            gst_registration_type=gst_registration_type,
            gst_applicable_from=gst_applicable_from,
            bill_wise=bill_wise,
            bill_credit_period=bill_credit_period,
            credit_limit=credit_limit,
            bank_name=bank_name,
            account_number=account_number,
            ifsc_code=ifsc_code,
            payment_favouring=payment_favouring,
            affects_stock=affects_stock,
            cost_centres_applicable=cost_centres_applicable,
            narration=narration,
            description=description,
            alter_mode=True,
        )
        return await self.import_masters(body, company=company)

    async def delete_ledger(self, company: str, ledger_name: str) -> Dict[str, Any]:
        """Delete a ledger from Tally."""
        ledger_xml = f'<LEDGER NAME="{_xml_escape(ledger_name)}" Action="Delete"></LEDGER>'
        return await self.import_masters(ledger_xml, company=company)

    async def create_group(
        self, company: str, name: str, parent_group: str
    ) -> Dict[str, Any]:
        """Create a new group in Tally."""
        group_xml = f"""<GROUP NAME="{_xml_escape(name)}" Action="Create">
  <NAME>{_xml_escape(name)}</NAME>
  <PARENT>{_xml_escape(parent_group)}</PARENT>
</GROUP>"""
        return await self.import_masters(group_xml, company=company)

    async def create_stock_item(
        self,
        company: str,
        name: str,
        parent: str = "Primary",
        base_units: str = "Nos",
        opening_balance: str = "0",
        opening_value: str = "0",
        category: str = "",
        gst_applicable: str = "",
    ) -> Dict[str, Any]:
        """Create a new stock item in Tally."""
        optional_tags = []
        if category:
            optional_tags.append(f"  <CATEGORY>{_xml_escape(category)}</CATEGORY>")
        if gst_applicable:
            optional_tags.append(f"  <GSTAPPLICABLE>{_xml_escape(gst_applicable)}</GSTAPPLICABLE>")
        optional_block = "\n".join(optional_tags)

        stock_xml = f"""<STOCKITEM NAME="{_xml_escape(name)}" Action="Create">
  <NAME>{_xml_escape(name)}</NAME>
  <PARENT>{_xml_escape(parent)}</PARENT>
  <BASEUNITS>{_xml_escape(base_units)}</BASEUNITS>
  <OPENINGBALANCE>{_xml_escape(opening_balance)}</OPENINGBALANCE>
  <OPENINGVALUE>{_xml_escape(opening_value)}</OPENINGVALUE>
  {optional_block}
</STOCKITEM>"""
        return await self.import_masters(stock_xml, company=company)

    async def create_voucher(
        self,
        company: str,
        voucher_type: str,
        date: str,
        ledger_entries: List[Dict[str, Any]],
        narration: str = "",
        voucher_number: str = "",
    ) -> Dict[str, Any]:
        """
        Create a voucher in Tally.

        date: YYYYMMDD format (e.g. "20080402")
        ledger_entries: list of dicts with keys:
            - ledger_name (str)
            - amount (str, negative = debit)
            - is_deemed_positive (str, "Yes"/"No")
        """
        entries_xml = ""
        for entry in ledger_entries:
            entries_xml += f"""<ALLLEDGERENTRIES.LIST>
  <LEDGERNAME>{_xml_escape(entry['ledger_name'])}</LEDGERNAME>
  <ISDEEMEDPOSITIVE>{_xml_escape(entry.get('is_deemed_positive', 'No'))}</ISDEEMEDPOSITIVE>
  <AMOUNT>{_xml_escape(str(entry['amount']))}</AMOUNT>
</ALLLEDGERENTRIES.LIST>
"""

        vch_num_tag = f"<VOUCHERNUMBER>{_xml_escape(voucher_number)}</VOUCHERNUMBER>" if voucher_number else ""

        voucher_xml = f"""<VOUCHER VCHTYPE="{_xml_escape(voucher_type)}" ACTION="Create">
  <DATE>{_xml_escape(date)}</DATE>
  <NARRATION>{_xml_escape(narration)}</NARRATION>
  <VOUCHERTYPENAME>{_xml_escape(voucher_type)}</VOUCHERTYPENAME>
  {vch_num_tag}
  {entries_xml}
</VOUCHER>"""
        return await self.import_vouchers(voucher_xml, company=company)

    async def alter_voucher(
        self,
        company: str,
        voucher_type: str,
        date: str,
        tag_name: str,
        tag_value: str,
        ledger_entries: List[Dict[str, Any]] = None,
        narration: str = None,
    ) -> Dict[str, Any]:
        """
        Alter a voucher in Tally using TAGNAME/TAGVALUE identification.

        date: "DD-Mon-YYYY" format (e.g. "02-Apr-2008")
        tag_name: identifier field ("Voucher Number", "MASTER ID", etc.)
        tag_value: value of the identifier
        """
        parts = [
            f'<VOUCHER DATE="{_xml_escape(date)}" '
            f'TAGNAME="{_xml_escape(tag_name)}" '
            f'TAGVALUE="{_xml_escape(tag_value)}" '
            f'ACTION="Alter" '
            f'VCHTYPE="{_xml_escape(voucher_type)}">'
        ]
        if narration is not None:
            parts.append(f"<NARRATION>{_xml_escape(narration)}</NARRATION>")
        if ledger_entries:
            for entry in ledger_entries:
                parts.append(f"""<ALLLEDGERENTRIES.LIST>
  <LEDGERNAME>{_xml_escape(entry['ledger_name'])}</LEDGERNAME>
  <ISDEEMEDPOSITIVE>{_xml_escape(entry.get('is_deemed_positive', 'No'))}</ISDEEMEDPOSITIVE>
  <AMOUNT>{_xml_escape(str(entry['amount']))}</AMOUNT>
</ALLLEDGERENTRIES.LIST>""")
        parts.append("</VOUCHER>")
        return await self.import_vouchers("\n".join(parts), company=company)

    async def cancel_voucher(
        self, company: str, voucher_type: str, date: str,
        tag_name: str, tag_value: str, narration: str = "",
    ) -> Dict[str, Any]:
        """Cancel a voucher in Tally."""
        voucher_xml = (
            f'<VOUCHER DATE="{_xml_escape(date)}" '
            f'TAGNAME="{_xml_escape(tag_name)}" '
            f'TAGVALUE="{_xml_escape(tag_value)}" '
            f'VCHTYPE="{_xml_escape(voucher_type)}" ACTION="Cancel">'
            f"<NARRATION>{_xml_escape(narration)}</NARRATION>"
            f"</VOUCHER>"
        )
        return await self.import_vouchers(voucher_xml, company=company)

    async def delete_voucher(
        self, company: str, voucher_type: str, date: str,
        tag_name: str, tag_value: str,
    ) -> Dict[str, Any]:
        """Delete a voucher from Tally."""
        voucher_xml = (
            f'<VOUCHER DATE="{_xml_escape(date)}" '
            f'TAGNAME="{_xml_escape(tag_name)}" '
            f'TAGVALUE="{_xml_escape(tag_value)}" '
            f'VCHTYPE="{_xml_escape(voucher_type)}" ACTION="Delete">'
            f"</VOUCHER>"
        )
        return await self.import_vouchers(voucher_xml, company=company)

    # ==========================================================
    #  EXECUTE — Run TDL actions
    # ==========================================================

    async def execute_action(self, action_name: str = "Sync") -> Dict[str, Any]:
        """
        Execute a TDL action in Tally.

        TALLYREQUEST=Execute, TYPE=TDLAction, ID=<action_name>
        """
        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Execute</TALLYREQUEST>
    <TYPE>TDLAction</TYPE>
    <ID>{_xml_escape(action_name)}</ID>
  </HEADER>
</ENVELOPE>"""
        raw = await self._post_xml(xml_req)
        return self._parse_status_response(raw)

    # ==========================================================
    #  Raw / pass-through
    # ==========================================================

    async def send_raw_xml(self, xml_payload: str) -> Dict[str, Any]:
        """Send any raw XML to Tally, auto-detect response type."""
        raw = await self._post_xml(xml_payload)
        try:
            root = self._parse_xml(raw)
            # Import response?
            if root.find(".//IMPORTRESULT") is not None:
                return self._parse_import_response(raw)
            # Status response?
            status = root.findtext(".//STATUS")
            if status is not None:
                return self._parse_status_response(raw)
            # Generic export response
            return {"success": True, "raw": raw}
        except ET.ParseError:
            return {"success": False, "error": "Invalid XML from Tally", "raw": raw}

    # ==========================================================
    #  Response parsers
    # ==========================================================

    def _parse_import_response(self, raw_xml: str) -> Dict[str, Any]:
        """
        Parse Tally's import response.

        Success response has <IMPORTRESULT> or <RESPONSE> with CREATED/ALTERED counts.
        Failure may have <LINEERROR> or STATUS=0.
        """
        try:
            root = self._parse_xml(raw_xml)

            # Check for <IMPORTRESULT> (voucher imports) or <RESPONSE> (master
            # imports) — either can BE the parsed root itself (Tally often
            # replies with no enclosing <ENVELOPE>), where ".//TAG" finds
            # nothing since it only searches descendants, not the root.
            if root.tag in ("IMPORTRESULT", "RESPONSE"):
                result = root
            else:
                result = root.find(".//IMPORTRESULT")
                if result is None:
                    result = root.find(".//RESPONSE")

            if result is not None:
                # Tally uses ERRORS, some versions/mocks use EXCEPTIONS — check both
                errors = int(result.findtext("ERRORS", "0"))
                exceptions = int(result.findtext("EXCEPTIONS", "0"))
                total_errors = errors + exceptions
                created = int(result.findtext("CREATED", "0"))
                altered = int(result.findtext("ALTERED", "0"))

                # LASTVCHID (real Tally) or LASTCREATEDVCHID (some versions)
                lastvchid = result.findtext("LASTVCHID", "")
                if not lastvchid:
                    lastvchid = root.findtext(".//LASTCREATEDVCHID", "")

                # A LINEERROR can sit alongside CREATED/ALTERED/EXCEPTIONS in
                # this same response (e.g. master imports) — without reading
                # it, a failed create only ever surfaces as "Unknown error".
                line_error = root.findtext(".//LINEERROR")

                logger.info(
                    f"Tally import result: created={created}, altered={altered}, "
                    f"errors={errors}, exceptions={exceptions}, lastvchid={lastvchid}"
                    + (f", line_error={line_error}" if line_error else "")
                )

                response = {
                    "success": total_errors == 0 and (created > 0 or altered > 0),
                    "created": created,
                    "altered": altered,
                    "deleted": int(result.findtext("DELETED", "0")),
                    "lastvchid": lastvchid,
                    "lastmid": result.findtext("LASTMID", ""),
                    "combined": int(result.findtext("COMBINED", "0")),
                    "ignored": int(result.findtext("IGNORED", "0")),
                    "errors": total_errors,
                    "raw": raw_xml,
                }
                if line_error:
                    response["error"] = line_error
                return response

            # Check for <LINEERROR>
            line_error = root.findtext(".//LINEERROR")
            if line_error:
                logger.warning(f"Tally import line error: {line_error}")
                return {"success": False, "error": line_error, "raw": raw_xml}

            # Check STATUS in header
            status = root.findtext(".//STATUS")
            if status == "0":
                # Extract error details from STATUS.LIST if present
                desc = root.findtext(".//STATUS.LIST/STATUS/DESC") or "Request failed"
                code = root.findtext(".//STATUS.LIST/STATUS/CODE") or ""
                logger.warning(f"Tally import failed: status=0, desc={desc}, code={code}")
                return {"success": False, "error": desc, "code": code, "raw": raw_xml}

            # No IMPORTRESULT found — this is unexpected for an import request
            logger.warning(
                f"Tally import response had no IMPORTRESULT tag. "
                f"Raw response (first 500 chars): {raw_xml[:500]}"
            )
            return {
                "success": False,
                "error": "Unexpected response from Tally — no import result found",
                "created": 0,
                "altered": 0,
                "errors": 0,
                "raw": raw_xml,
            }

        except ET.ParseError:
            logger.error(f"Failed to parse Tally XML response: {raw_xml[:500]}")
            return {"success": False, "error": "Invalid XML response from Tally", "raw": raw_xml}

    def _parse_status_response(self, raw_xml: str) -> Dict[str, Any]:
        """Parse a simple status response (for Execute actions)."""
        try:
            root = self._parse_xml(raw_xml)
            status = root.findtext(".//STATUS")
            return {
                "success": status == "1",
                "status": status,
                "raw": raw_xml,
            }
        except ET.ParseError:
            return {"success": False, "error": "Invalid XML from Tally", "raw": raw_xml}

    # ==========================================================
    #  High-level helpers for bank voucher integration
    # ==========================================================

    async def get_current_company(self) -> Dict[str, Any]:
        """Fetch the currently active company name from Tally."""
        xml_req = """<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Function</TYPE>
    <ID>$$CurrentCompany</ID>
  </HEADER>
  <BODY>
    <DESC></DESC>
  </BODY>
</ENVELOPE>"""
        try:
            raw = await self._post_xml(xml_req)
            root = self._parse_xml(raw)
            result = (root.findtext(".//RESULT") or "").strip()
            return {"success": bool(result), "company": result}
        except Exception as e:
            return {"success": False, "company": "", "error": str(e)}

    async def get_filtered_ledgers(
        self, company: str = None, filter_type: str = "all"
    ) -> List[Dict[str, str]]:
        """
        Fetch ledgers from Tally with optional filtering.

        filter_type: "party" (Sundry Debtors/Creditors + Expenses),
                     "bank" (Bank/Cash accounts),
                     "all" (no filter)
        """
        import time

        cache_key = f"{company or ''}:{filter_type}"
        cached = self._ledger_cache.get(cache_key)
        if cached and cached["expires"] > time.time():
            return cached["data"]

        if filter_type == "party":
            xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>FilteredLedgers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        {self._build_static_vars(company)}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="FilteredLedgers" ISMODIFY="No">
            <SOURCECOLLECTION>Ledger</SOURCECOLLECTION>
            <FETCH>Name</FETCH>
            <FETCH>Parent</FETCH>
            <FILTER>PartyExpenseFilter</FILTER>
          </COLLECTION>
          <SYSTEM TYPE="Formulae" NAME="PartyExpenseFilter" ISMODIFY="No">
            $IsLedOfGrp:$Name:$GroupSundryCreditors OR $IsLedOfGrp:$Name:$GroupSundryDebtors OR $IsLedOfGrp:$Name:$GroupIndirectExpenses OR $IsLedOfGrp:$Name:$GroupDirectExpenses
          </SYSTEM>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
        elif filter_type == "bank":
            xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>FilteredLedgers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        {self._build_static_vars(company)}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="FilteredLedgers" ISMODIFY="No">
            <SOURCECOLLECTION>Ledger</SOURCECOLLECTION>
            <FETCH>Name</FETCH>
            <FETCH>Parent</FETCH>
            <FILTER>BankCashFilter</FILTER>
          </COLLECTION>
          <SYSTEM TYPE="Formulae" NAME="BankCashFilter" ISMODIFY="No">
            $IsLedOfGrp:$Name:$GroupBank OR $IsLedOfGrp:$Name:$GroupBankOD OR $IsLedOfGrp:$Name:$GroupCash
          </SYSTEM>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
        else:
            # Fetch all ledgers
            ledgers = await self.get_ledgers(company)
            self._ledger_cache[cache_key] = {
                "data": ledgers,
                "expires": time.time() + self._cache_ttl,
            }
            return ledgers

        raw = await self._post_xml(xml_req)
        root = self._parse_xml(raw)
        ledgers = []

        # Parse LEDGER elements
        for ledger in root.iter("LEDGER"):
            name = ledger.findtext("NAME") or ledger.get("NAME", "")
            if name:
                ledgers.append({
                    "name": name.strip(),
                    "parent": (ledger.findtext("PARENT") or "").strip(),
                })

        # Also check for COLLECTION > NAME patterns
        if not ledgers:
            for collection in root.iter("COLLECTION"):
                for name_el in collection.iter("NAME"):
                    if name_el.text and name_el.text.strip():
                        ledgers.append({"name": name_el.text.strip(), "parent": ""})

        self._ledger_cache[cache_key] = {
            "data": ledgers,
            "expires": time.time() + self._cache_ttl,
        }
        return ledgers

    def invalidate_ledger_cache(self, company: str = None):
        """Clear the ledger cache, optionally for a specific company."""
        if company:
            keys_to_delete = [k for k in self._ledger_cache if k.startswith(f"{company}:")]
            for k in keys_to_delete:
                del self._ledger_cache[k]
        else:
            self._ledger_cache.clear()

    async def get_recent_vouchers(
        self,
        company: str,
        from_date: str,
        to_date: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent vouchers from Tally for duplicate checking.

        from_date, to_date: YYYYMMDD format.
        """
        sv_block = self._build_static_vars(company)
        xml_req = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>RecentVouchers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {sv_block}
        <SVFROMDATE>{_xml_escape(from_date)}</SVFROMDATE>
        <SVTODATE>{_xml_escape(to_date)}</SVTODATE>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="RecentVouchers" ISMODIFY="No">
            <TYPE>Voucher</TYPE>
            <NATIVEMETHOD>Date</NATIVEMETHOD>
            <NATIVEMETHOD>Narration</NATIVEMETHOD>
            <NATIVEMETHOD>Amount</NATIVEMETHOD>
            <NATIVEMETHOD>VoucherTypeName</NATIVEMETHOD>
            <NATIVEMETHOD>VoucherNumber</NATIVEMETHOD>
            <NATIVEMETHOD>Reference</NATIVEMETHOD>
            <NATIVEMETHOD>PartyLedgerName</NATIVEMETHOD>
            <NATIVEMETHOD>MasterID</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
        raw = await self._post_xml(xml_req)
        root = self._parse_xml(raw)
        vouchers = []
        for vch in root.iter("VOUCHER"):
            vouchers.append({
                "date": (vch.findtext("DATE") or "").strip(),
                "narration": (vch.findtext("NARRATION") or "").strip(),
                "amount": (vch.findtext("AMOUNT") or "0").strip(),
                "voucher_type": (vch.findtext("VOUCHERTYPENAME") or "").strip(),
                "voucher_number": (vch.findtext("VOUCHERNUMBER") or "").strip(),
                # Sales push stores invoice # in VOUCHERNUMBER; Purchase push
                # stores it in REFERENCE — expose both so the duplicate-check
                # match can probe either field.
                "reference": (vch.findtext("REFERENCE") or "").strip(),
                "party_ledger": (vch.findtext("PARTYLEDGERNAME") or "").strip(),
                "master_id": (vch.findtext("MASTERID") or "").strip(),
            })
        return vouchers

    def build_bank_vouchers_xml(
        self,
        company: str,
        bank_ledger: str,
        transactions: List[Dict[str, Any]],
    ) -> str:
        """
        Build Tally XML for bank vouchers (Receipt/Payment/Contra) using the
        proven working format.

        Each transaction dict should have:
            date, narration, amount, voucher_type, party_ledger,
            optional: reference, voucher_number
        """
        voucher_blocks = []
        counter = 0
        skipped_count = 0
        all_tally_dates = []  # Collect dates to compute financial year

        for tx in transactions:
            voucher_type = tx.get("voucher_type", "Receipt")
            date_str = tx.get("date", "")
            try:
                amount = abs(float(tx.get("amount", 0)))
            except (ValueError, TypeError):
                logger.warning(f"Skipping voucher: invalid amount '{tx.get('amount')}' for party '{tx.get('party_ledger')}'")
                skipped_count += 1
                continue
            narration = tx.get("narration", "")
            party_ledger = tx.get("party_ledger", "")
            tx_bank_ledger = tx.get("bank_ledger") or bank_ledger

            if amount == 0 or not party_ledger:
                logger.warning(
                    f"Skipping voucher in XML build: amount={amount}, "
                    f"party_ledger='{party_ledger}', date='{date_str}'"
                )
                skipped_count += 1
                continue

            # Convert date to YYYYMMDD (Tally's expected format)
            tally_date = self._convert_date(date_str)
            if not tally_date or not re.match(r'^\d{8}$', tally_date):
                logger.warning(
                    f"Skipping voucher: invalid/empty date '{date_str}' -> '{tally_date}' "
                    f"for party '{party_ledger}'"
                )
                skipped_count += 1
                continue
            counter += 1
            all_tally_dates.append(tally_date)

            if voucher_type == "Receipt":
                # Money IN: Debit Bank, Credit Party
                debit_ledger = _xml_escape(tx_bank_ledger)
                credit_ledger = _xml_escape(party_ledger)
            elif voucher_type == "Payment":
                # Money OUT: Debit Party, Credit Bank
                debit_ledger = _xml_escape(party_ledger)
                credit_ledger = _xml_escape(tx_bank_ledger)
            else:
                # Contra: Debit party(destination), Credit Bank(source)
                debit_ledger = _xml_escape(party_ledger)
                credit_ledger = _xml_escape(tx_bank_ledger)

            # Build optional tags only when non-empty (avoid blank lines in XML)
            optional_tags = []
            vch_num = tx.get("voucher_number", "")
            if vch_num:
                optional_tags.append(f"          <VOUCHERNUMBER>{_xml_escape(vch_num)}</VOUCHERNUMBER>")
            ref = tx.get("reference", "")
            if ref:
                optional_tags.append(f"          <REFERENCE>{_xml_escape(ref)}</REFERENCE>")
            optional_block = "\n" + "\n".join(optional_tags) if optional_tags else ""

            voucher_xml = f"""        <VOUCHER DATE="{tally_date}" VCHTYPE="{_xml_escape(voucher_type)}" ACTION="Create" OBJVIEW="Accounting Voucher View">
          <DATE>{tally_date}</DATE>
          <VOUCHERTYPENAME>{_xml_escape(voucher_type)}</VOUCHERTYPENAME>{optional_block}
          <PARTYLEDGERNAME>{_xml_escape(party_ledger)}</PARTYLEDGERNAME>
          <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
          <ISVOUCHERENTRY>Yes</ISVOUCHERENTRY>
          <EFFECTIVEDATE>{tally_date}</EFFECTIVEDATE>
          <NARRATION>{_xml_escape(narration)}</NARRATION>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>{debit_ledger}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{amount:.2f}</AMOUNT>
          </ALLLEDGERENTRIES.LIST>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>{credit_ledger}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <AMOUNT>{amount:.2f}</AMOUNT>
          </ALLLEDGERENTRIES.LIST>
        </VOUCHER>"""
            voucher_blocks.append(voucher_xml)

        if not voucher_blocks:
            logger.warning(
                f"build_bank_vouchers_xml: All {len(transactions)} transactions were "
                f"filtered out (skipped={skipped_count}). No XML generated."
            )
            return ""

        logger.info(
            f"build_bank_vouchers_xml: Built {len(voucher_blocks)} vouchers "
            f"(skipped={skipped_count}) for company='{company}'"
        )
        logger.info(f"build_bank_vouchers_xml: tally_dates={all_tally_dates}")

        # Compute financial year period from transaction dates
        # Tally requires SVFROMDATE/SVTODATE to know which period to use;
        # without them, Tally uses the currently loaded period and may reject
        # vouchers whose dates fall outside it ("Voucher date is missing").
        sv_from, sv_to = self._compute_financial_year(all_tally_dates)
        logger.info(f"build_bank_vouchers_xml: SVFROMDATE={sv_from}, SVTODATE={sv_to}")
        extra_vars = {}
        if sv_from and sv_to:
            extra_vars["SVFROMDATE"] = sv_from
            extra_vars["SVTODATE"] = sv_to
        sv_block = self._build_static_vars(company, extra_vars=extra_vars)

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Import</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>Vouchers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        {sv_block}
      </STATICVARIABLES>
    </DESC>
    <DATA>
      <TALLYMESSAGE xmlns:UDF="TallyUDF">
{chr(10).join(voucher_blocks)}
      </TALLYMESSAGE>
    </DATA>
  </BODY>
</ENVELOPE>"""
        return xml

    # ==========================================================
    #  Upsert helpers — ensure masters exist before pushing
    # ==========================================================

    async def ensure_ledgers_exist(
        self,
        company: str,
        required_ledgers: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Check which ledgers are missing in Tally and create them.

        required_ledgers: list of {"name": "...", "parent_group": "Sundry Debtors"}
        Returns {"created": [...], "failed": [...], "already_exists": [...]}
        """
        if not required_ledgers:
            return {"created": [], "failed": [], "already_exists": []}

        try:
            existing = await self.get_ledgers(company)
            existing_names = {l["name"].strip().lower() for l in existing}
        except Exception as e:
            logger.warning(f"Cannot fetch ledgers for upsert check: {e}")
            existing_names = set()

        # Real companies often rename Tally's default primary groups (e.g. a
        # migrated chart of accounts prefixes them with its own numeric codes:
        # "4401030001 Duties & Taxes" instead of the bare "Duties & Taxes").
        # A hardcoded guess like "Duties & Taxes" then fails with "Group
        # ... does not exist!" even though an equivalent group is right there
        # under a longer name — resolve to the real name before creating.
        try:
            existing_groups = await self.get_groups(company)
            group_names_lower = {g["name"].strip().lower(): g["name"].strip() for g in existing_groups}
        except Exception as e:
            logger.warning(f"Cannot fetch groups for upsert check: {e}")
            group_names_lower = {}

        def _resolve_group(guessed: str) -> str:
            guessed_lower = guessed.strip().lower()
            if guessed_lower in group_names_lower:
                return group_names_lower[guessed_lower]
            # Several renamed/split variants can all contain the guess (e.g.
            # "...Sundry Creditors", "Sundry Creditors -MSME", "... -Non MSME")
            # — the shortest match is the closest to the generic category.
            matches = [
                real_name for lower_name, real_name in group_names_lower.items()
                if guessed_lower in lower_name
            ]
            return min(matches, key=len) if matches else guessed

        created = []
        failed = []
        already_exists = []

        for ledger in required_ledgers:
            name = ledger.get("name", "").strip()
            if not name:
                continue
            if name.lower() in existing_names:
                already_exists.append(name)
                continue

            parent_group = _resolve_group(ledger.get("parent_group", "Sundry Debtors"))
            try:
                result = await self.create_ledger(
                    company=company,
                    name=name,
                    parent_group=parent_group,
                    state=ledger.get("state", ""),
                    country=ledger.get("country", "India"),
                    gstin=ledger.get("gstin", ""),
                )
                success = (
                    result.get("success", False)
                    or result.get("created", 0) > 0
                    or result.get("combined", 0) > 0
                )
                if success:
                    created.append(name)
                    existing_names.add(name.lower())
                else:
                    failed.append({"name": name, "error": result.get("error", "Unknown error")})
            except Exception as e:
                failed.append({"name": name, "error": str(e)})

        if created:
            self.invalidate_ledger_cache(company)

        logger.info(
            f"ensure_ledgers_exist: created={len(created)}, "
            f"already_exists={len(already_exists)}, failed={len(failed)}"
        )
        return {"created": created, "failed": failed, "already_exists": already_exists}

    async def ensure_stock_items_exist(
        self,
        company: str,
        required_items: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Check which stock items are missing in Tally and create them.

        required_items: list of {"name": "...", "parent": "Primary", "base_units": "Nos"}
        Returns {"created": [...], "failed": [...], "already_exists": [...]}
        """
        if not required_items:
            return {"created": [], "failed": [], "already_exists": []}

        try:
            existing = await self.get_inventory(company)
            existing_names = {i["name"].strip().lower() for i in existing}
        except Exception as e:
            logger.warning(f"Cannot fetch inventory for upsert check: {e}")
            existing_names = set()

        created = []
        failed = []
        already_exists = []

        for item in required_items:
            name = item.get("name", "").strip()
            if not name:
                continue
            if name.lower() in existing_names:
                already_exists.append(name)
                continue

            try:
                result = await self.create_stock_item(
                    company=company,
                    name=name,
                    parent=item.get("parent", "Primary"),
                    base_units=item.get("base_units", item.get("unit", "Nos")),
                )
                success = (
                    result.get("success", False)
                    or result.get("created", 0) > 0
                    or result.get("combined", 0) > 0
                )
                if success:
                    created.append(name)
                    existing_names.add(name.lower())
                else:
                    failed.append({"name": name, "error": result.get("error", "Unknown error")})
            except Exception as e:
                failed.append({"name": name, "error": str(e)})

        logger.info(
            f"ensure_stock_items_exist: created={len(created)}, "
            f"already_exists={len(already_exists)}, failed={len(failed)}"
        )
        return {"created": created, "failed": failed, "already_exists": already_exists}

    # ==========================================================
    #  Invoice voucher XML builders (Sales / Purchase)
    # ==========================================================

    def build_sales_voucher_xml(
        self,
        company: str,
        transactions: List[Dict[str, Any]],
        sales_ledger: str = "Sales",
    ) -> str:
        """
        Build Tally XML for Sales vouchers with inventory entries.
        Matches the proven XML structure from sales.py.

        Each transaction dict should have:
            date, party_ledger, narration, invoice_number,
            inventory_items: [{item_name, quantity, rate, unit, amount, discount_percentage, batch_number}],
            ledger_entries: [{ledger_name, amount}]  (tax/charge ledgers only, positive amounts)
        Optional: party_gstin, place_of_supply
        """
        esc_company = _xml_escape(company)
        voucher_blocks = []
        skipped_count = 0
        all_tally_dates = []

        for tx in transactions:
            date_str = tx.get("date", "")
            party_ledger = tx.get("party_ledger", "").strip()
            narration = tx.get("narration", "")
            invoice_number = tx.get("invoice_number", tx.get("voucher_number", ""))
            inventory_items = tx.get("inventory_items", [])
            ledger_entries = tx.get("ledger_entries", [])
            party_gstin = tx.get("party_gstin", "")
            place_of_supply = tx.get("place_of_supply", "")

            if not party_ledger:
                logger.warning("Skipping Sales voucher: missing party_ledger")
                skipped_count += 1
                continue

            tally_date = self._convert_date(date_str)
            if not tally_date or not re.match(r'^\d{8}$', tally_date):
                logger.warning(f"Skipping Sales voucher: invalid date '{date_str}'")
                skipped_count += 1
                continue
            all_tally_dates.append(tally_date)

            esc_party = _xml_escape(party_ledger)

            # Calculate total amount (inventory + tax/charges)
            total_amount = 0.0
            for item in inventory_items:
                try:
                    qty = float(item.get("quantity", 0))
                    rate = float(item.get("rate", 0))
                    total_amount += qty * rate
                except (ValueError, TypeError):
                    pass

            for entry in ledger_entries:
                try:
                    amt = float(entry.get("amount", 0))
                    if amt > 0:
                        total_amount += amt
                except (ValueError, TypeError):
                    pass

            # Start voucher
            vch_xml = f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
     <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
      <DATE>{tally_date}</DATE>
      <PARTYNAME>{esc_party}</PARTYNAME>
      <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
      <VOUCHERNUMBER>{_xml_escape(invoice_number)}</VOUCHERNUMBER>
      <PARTYLEDGERNAME>{esc_party}</PARTYLEDGERNAME>
      <EFFECTIVEDATE>{tally_date}</EFFECTIVEDATE>
      <ISINVOICE>Yes</ISINVOICE>
      <HASDISCOUNTS>Yes</HASDISCOUNTS>"""

            if party_gstin:
                vch_xml += f"""
      <PARTYGSTIN>{_xml_escape(party_gstin)}</PARTYGSTIN>"""
            if place_of_supply:
                vch_xml += f"""
      <PLACEOFSUPPLY>{_xml_escape(place_of_supply)}</PLACEOFSUPPLY>"""

            if narration:
                vch_xml += f"""
      <NARRATION>{_xml_escape(narration)}</NARRATION>"""

            # Party ledger entry (debit: ISDEEMEDPOSITIVE=Yes, negative amount)
            vch_xml += f"""
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>{esc_party}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <LEDGERFROMITEM>No</LEDGERFROMITEM>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
       <AMOUNT>-{total_amount:.2f}</AMOUNT>
      </LEDGERENTRIES.LIST>"""

            # Tax/charge ledger entries (credit: ISDEEMEDPOSITIVE=No, positive amount)
            for entry in ledger_entries:
                le_name = entry.get("ledger_name", entry.get("ledgerName", "")).strip()
                if not le_name:
                    continue
                try:
                    amt = float(entry.get("amount", 0))
                except (ValueError, TypeError):
                    continue
                if amt > 0:
                    vch_xml += f"""
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>{_xml_escape(le_name)}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <LEDGERFROMITEM>No</LEDGERFROMITEM>
       <ISPARTYLEDGER>No</ISPARTYLEDGER>
       <AMOUNT>{amt:.2f}</AMOUNT>
      </LEDGERENTRIES.LIST>"""

            # Inventory entries
            for item in inventory_items:
                item_name = item.get("item_name", item.get("stockItemName", "")).strip()
                if not item_name:
                    continue
                try:
                    qty = float(item.get("quantity", 0))
                    rate = float(item.get("rate", 0))
                except (ValueError, TypeError):
                    continue
                amount = qty * rate
                unit = item.get("unit", "Nos")
                discount_pct = float(item.get("discount_percentage", 0))
                batch_number = item.get("batch_number", "Primary Batch") or "Primary Batch"
                godown = item.get("godown", "Main Location") or "Main Location"

                vch_xml += f"""
      <ALLINVENTORYENTRIES.LIST>
       <STOCKITEMNAME>{_xml_escape(item_name)}</STOCKITEMNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <RATE>{rate:.2f}/{_xml_escape(unit)}</RATE>"""

                if discount_pct > 0:
                    vch_xml += f"""
       <DISCOUNT>{discount_pct}</DISCOUNT>"""

                vch_xml += f"""
       <AMOUNT>{amount:.2f}</AMOUNT>
       <ACTUALQTY>{qty} {_xml_escape(unit)}</ACTUALQTY>
       <BILLEDQTY>{qty} {_xml_escape(unit)}</BILLEDQTY>
       <BATCHALLOCATIONS.LIST>
        <GODOWNNAME>{_xml_escape(godown)}</GODOWNNAME>
        <BATCHNAME>{_xml_escape(batch_number)}</BATCHNAME>
        <AMOUNT>{amount:.2f}</AMOUNT>
        <ACTUALQTY>{qty} {_xml_escape(unit)}</ACTUALQTY>
        <BILLEDQTY>{qty} {_xml_escape(unit)}</BILLEDQTY>
       </BATCHALLOCATIONS.LIST>
       <ACCOUNTINGALLOCATIONS.LIST>
        <LEDGERNAME>{_xml_escape(sales_ledger)}</LEDGERNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <AMOUNT>{amount:.2f}</AMOUNT>
       </ACCOUNTINGALLOCATIONS.LIST>
      </ALLINVENTORYENTRIES.LIST>"""

            vch_xml += """
     </VOUCHER>
    </TALLYMESSAGE>"""
            voucher_blocks.append(vch_xml)

        if not voucher_blocks:
            logger.warning(
                f"build_sales_voucher_xml: "
                f"All {len(transactions)} transactions filtered out"
            )
            return ""

        # Tally requires SVFROMDATE/SVTODATE to know which period to use;
        # without them, Tally uses the currently loaded period and may reject
        # vouchers whose dates fall outside it ("Voucher date is missing").
        sv_from, sv_to = self._compute_financial_year(all_tally_dates)
        extra_vars = {}
        if sv_from and sv_to:
            extra_vars["SVFROMDATE"] = sv_from
            extra_vars["SVTODATE"] = sv_to
        sv_block = self._build_static_vars(company, extra_vars=extra_vars)

        xml = f"""<ENVELOPE>
 <HEADER>
  <TALLYREQUEST>Import Data</TALLYREQUEST>
 </HEADER>
 <BODY>
  <IMPORTDATA>
   <REQUESTDESC>
    <REPORTNAME>Vouchers</REPORTNAME>
    <STATICVARIABLES>
     {sv_block}
    </STATICVARIABLES>
   </REQUESTDESC>
   <REQUESTDATA>{"".join(voucher_blocks)}
   </REQUESTDATA>
  </IMPORTDATA>
 </BODY>
</ENVELOPE>"""
        logger.info(
            f"build_sales_voucher_xml: "
            f"Built {len(voucher_blocks)} vouchers (skipped={skipped_count})"
        )
        return xml

    def build_purchase_voucher_xml(
        self,
        company: str,
        transactions: List[Dict[str, Any]],
        purchase_ledger: str = "Purchase",
        cgst_ledger: str = "CGST ITC",
        sgst_ledger: str = "SGST ITC",
    ) -> str:
        """
        Build Tally XML for Purchase vouchers (ledger-only, no inventory entries).
        Matches the proven XML structure from purchase.py with GST ITC split.

        Each transaction dict can use either format:

        Format A (direct):
            date, party_ledger, narration, reference_number, place_of_supply,
            amount, gst_percentage, gst_amount, total_amount, debit_ledger

        Format B (upsert-style, normalized internally):
            date, party_ledger, narration, invoice_number,
            inventory_items: [{item_name, quantity, rate, unit, amount}],
            ledger_entries: [{ledger_name, amount, parent_group}]
        """
        esc_company = _xml_escape(company)
        voucher_blocks = []
        skipped_count = 0
        all_tally_dates = []

        for tx in transactions:
            date_str = tx.get("date", "")
            party_ledger = tx.get("party_ledger", "").strip()
            narration = tx.get("narration", tx.get("description", "Purchase Transaction"))
            reference_number = tx.get("reference_number", tx.get("invoice_number", ""))
            place_of_supply = tx.get("place_of_supply", "")
            debit_ledger = tx.get("debit_ledger", purchase_ledger)
            credit_ledger = tx.get("credit_ledger", party_ledger)
            tx_cgst_ledger = tx.get("cgst_ledger", cgst_ledger)
            tx_sgst_ledger = tx.get("sgst_ledger", sgst_ledger)

            if not party_ledger:
                logger.warning("Skipping Purchase voucher: missing party_ledger")
                skipped_count += 1
                continue

            tally_date = self._convert_date(date_str)
            if not tally_date or not re.match(r'^\d{8}$', tally_date):
                logger.warning(f"Skipping Purchase voucher: invalid date '{date_str}'")
                skipped_count += 1
                continue
            all_tally_dates.append(tally_date)

            # Normalize amounts: support both Format A and Format B
            amount = 0.0
            gst_percentage = 0.0
            gst_amount = 0.0

            if "amount" in tx:
                # Format A: direct fields
                try:
                    amount = float(tx.get("amount", 0))
                except (TypeError, ValueError):
                    amount = 0.0
                try:
                    gst_percentage = float(tx.get("gst_percentage", 0))
                except (TypeError, ValueError):
                    gst_percentage = 0.0
                try:
                    gst_amount = float(tx.get("gst_amount", 0))
                except (TypeError, ValueError):
                    gst_amount = 0.0
            else:
                # Format B: sum inventory items into amount, extract GST from ledger entries
                for item in tx.get("inventory_items", []):
                    try:
                        item_amt = float(item.get("amount", float(item.get("quantity", 0)) * float(item.get("rate", 0))))
                        amount += abs(item_amt)
                    except (TypeError, ValueError):
                        pass
                for le in tx.get("ledger_entries", []):
                    le_name = le.get("ledger_name", le.get("ledgerName", "")).strip().lower()
                    try:
                        le_amt = float(le.get("amount", 0))
                    except (TypeError, ValueError):
                        le_amt = 0.0
                    if "cgst" in le_name or "sgst" in le_name or "igst" in le_name:
                        gst_amount += abs(le_amt)
                    elif "purchase" in le_name:
                        debit_ledger = le.get("ledger_name", le.get("ledgerName", debit_ledger))

            try:
                total_amount = float(tx.get("total_amount", amount + gst_amount))
            except (TypeError, ValueError):
                total_amount = amount + gst_amount

            esc_description = _xml_escape(narration)
            esc_reference = _xml_escape(reference_number)
            esc_place = _xml_escape(place_of_supply)
            esc_credit = _xml_escape(credit_ledger)
            esc_debit = _xml_escape(debit_ledger)

            vch_xml = f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
     <VOUCHER DATE="{tally_date}" VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Accounting Voucher View">
      <DATE>{tally_date}</DATE>
      <NARRATION>{esc_description}</NARRATION>
      <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
      <VOUCHERNUMBER>{esc_reference}</VOUCHERNUMBER>
      <PARTYLEDGERNAME>{esc_credit}</PARTYLEDGERNAME>
      <REFERENCE>{esc_reference}</REFERENCE>
      <EFFECTIVEDATE>{tally_date}</EFFECTIVEDATE>"""

            if esc_place:
                vch_xml += f"""
      <STATENAME>{esc_place}</STATENAME>"""

            # Track total debit to ensure the voucher balances
            total_debit = amount

            # Debit ledger entry (Purchase account)
            # Tally XML sign convention: DEBIT = ISDEEMEDPOSITIVE=Yes + NEGATIVE amount.
            # (Previously this and the tax entries shipped with No/positive — Tally
            # then displayed the purchase ledger as "To Purchase" on the credit
            # side and the vendor as "By Vendor" on the debit side, inverting
            # the books. See the working bank_entries XML for the canonical
            # debit/credit pattern.)
            vch_xml += f"""
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{esc_debit}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-{amount:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>"""

            # Tax ledger entries (input GST = ITC asset on a purchase = DEBIT).
            tax_entries = tx.get("tax_entries", [])
            if tax_entries:
                for te in tax_entries:
                    te_name = te.get("ledger_name", "").strip()
                    try:
                        te_amt = float(te.get("amount", 0))
                    except (TypeError, ValueError):
                        te_amt = 0.0
                    if te_name and te_amt > 0:
                        total_debit += te_amt
                        vch_xml += f"""
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{_xml_escape(te_name)}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-{te_amt:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>"""
            elif gst_amount > 0:
                # Fallback: split evenly into CGST + SGST
                half_gst = gst_amount / 2
                total_debit += gst_amount
                vch_xml += f"""
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{_xml_escape(tx_cgst_ledger)}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-{half_gst:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{_xml_escape(tx_sgst_ledger)}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-{half_gst:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>"""

            # TDS Payable deduction (optional — reduces net payable to vendor)
            tds_ledger_name = tx.get("tds_ledger", "").strip()
            try:
                tds_deduction = float(tx.get("tds_amount", 0))
            except (TypeError, ValueError):
                tds_deduction = 0.0
            if not tds_ledger_name:
                tds_deduction = 0.0

            # Credit ledger entry (vendor/party) — CREDIT = No / positive.
            # vendor_credit = sum of debits minus TDS so the voucher balances.
            vendor_credit = round(total_debit - tds_deduction, 2)
            vch_xml += f"""
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{esc_credit}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <AMOUNT>{vendor_credit:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>"""

            # TDS Payable ledger entry — also a CREDIT (liability owed to govt).
            if tds_ledger_name and tds_deduction > 0:
                vch_xml += f"""
      <ALLLEDGERENTRIES.LIST>
       <LEDGERNAME>{_xml_escape(tds_ledger_name)}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <AMOUNT>{tds_deduction:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>"""

            vch_xml += """
     </VOUCHER>
    </TALLYMESSAGE>"""
            voucher_blocks.append(vch_xml)

        if not voucher_blocks:
            logger.warning(
                f"build_purchase_voucher_xml: "
                f"All {len(transactions)} transactions filtered out"
            )
            return ""

        # Tally requires SVFROMDATE/SVTODATE to know which period to use;
        # without them, Tally uses the currently loaded period and may reject
        # vouchers whose dates fall outside it ("Voucher date is missing").
        sv_from, sv_to = self._compute_financial_year(all_tally_dates)
        extra_vars = {}
        if sv_from and sv_to:
            extra_vars["SVFROMDATE"] = sv_from
            extra_vars["SVTODATE"] = sv_to
        sv_block = self._build_static_vars(company, extra_vars=extra_vars)

        xml = f"""<ENVELOPE>
 <HEADER>
  <TALLYREQUEST>Import Data</TALLYREQUEST>
 </HEADER>
 <BODY>
  <IMPORTDATA>
   <REQUESTDESC>
    <REPORTNAME>Vouchers</REPORTNAME>
    <STATICVARIABLES>
     {sv_block}
    </STATICVARIABLES>
   </REQUESTDESC>
   <REQUESTDATA>{"".join(voucher_blocks)}
   </REQUESTDATA>
  </IMPORTDATA>
 </BODY>
</ENVELOPE>"""
        logger.info(
            f"build_purchase_voucher_xml: "
            f"Built {len(voucher_blocks)} vouchers (skipped={skipped_count})"
        )
        return xml

    # ==========================================================
    #  Orchestrator — upsert masters then push vouchers
    # ==========================================================

    async def push_voucher_with_upsert(
        self,
        company: str,
        voucher_type: str,
        transactions: List[Dict[str, Any]],
        bank_ledger: str = "",
        auto_create_ledgers: bool = True,
        auto_create_stock_items: bool = True,
        batch_size: int = 20,
    ) -> Dict[str, Any]:
        """
        Two-stage upsert: ensure all referenced ledgers and stock items exist,
        then push vouchers to Tally.

        voucher_type: "Sales", "Purchase", "Receipt", "Payment", "Contra"
        """
        upsert_results = {"ledgers": {}, "stock_items": {}}

        # --- Stage 1: Extract required masters from transactions ---
        required_ledgers_map: Dict[str, str] = {}  # name -> parent_group
        required_items_map: Dict[str, Dict[str, str]] = {}  # name -> {parent, base_units}

        is_purchase = voucher_type == "Purchase"

        for tx in transactions:
            # Party ledger
            party = tx.get("party_ledger", "").strip()
            if party and party not in required_ledgers_map:
                if is_purchase:
                    default_group = tx.get("party_ledger_group", "Sundry Creditors")
                else:
                    default_group = tx.get("party_ledger_group", "Sundry Debtors")
                required_ledgers_map[party] = default_group

            # Bank ledger (for Receipt/Payment/Contra)
            tx_bank = tx.get("bank_ledger", bank_ledger).strip()
            if tx_bank and tx_bank not in required_ledgers_map:
                required_ledgers_map[tx_bank] = "Bank Accounts"

            # Ledger entries (tax, sales/purchase accounts, etc.)
            for le in tx.get("ledger_entries", []):
                le_name = le.get("ledger_name", le.get("ledgerName", "")).strip()
                if le_name and le_name not in required_ledgers_map:
                    required_ledgers_map[le_name] = le.get("parent_group", "Indirect Expenses")

            # Purchase-specific: ensure debit ledger and GST ITC ledgers exist
            if is_purchase:
                debit_ledger = tx.get("debit_ledger", "Purchase")
                if debit_ledger and debit_ledger not in required_ledgers_map:
                    required_ledgers_map[debit_ledger] = "Purchase Accounts"

                # Check if GST is present (from direct fields or ledger entries)
                has_gst = False
                try:
                    if float(tx.get("gst_amount", 0)) > 0:
                        has_gst = True
                except (TypeError, ValueError):
                    pass
                if not has_gst:
                    for le in tx.get("ledger_entries", []):
                        le_name = le.get("ledger_name", le.get("ledgerName", "")).strip().lower()
                        if "cgst" in le_name or "sgst" in le_name or "igst" in le_name:
                            has_gst = True
                            break

                if has_gst:
                    if "CGST ITC" not in required_ledgers_map:
                        required_ledgers_map["CGST ITC"] = "Duties & Taxes"
                    if "SGST ITC" not in required_ledgers_map:
                        required_ledgers_map["SGST ITC"] = "Duties & Taxes"

            # Inventory items (only for non-Purchase voucher types)
            if not is_purchase:
                for item in tx.get("inventory_items", []):
                    item_name = item.get("item_name", item.get("stockItemName", "")).strip()
                    if item_name and item_name not in required_items_map:
                        required_items_map[item_name] = {
                            "name": item_name,
                            "parent": item.get("parent", "Primary"),
                            "base_units": item.get("unit", "Nos"),
                        }

        # --- Stage 1b: Upsert missing masters ---
        if auto_create_ledgers and required_ledgers_map:
            required_ledgers = [
                {"name": name, "parent_group": group}
                for name, group in required_ledgers_map.items()
            ]
            upsert_results["ledgers"] = await self.ensure_ledgers_exist(
                company, required_ledgers
            )

        if auto_create_stock_items and required_items_map:
            required_items = list(required_items_map.values())
            upsert_results["stock_items"] = await self.ensure_stock_items_exist(
                company, required_items
            )

        # --- Stage 2: Build and push voucher XML ---
        if voucher_type in ("Receipt", "Payment", "Contra"):
            xml = self.build_bank_vouchers_xml(company, bank_ledger, transactions)
        elif voucher_type == "Sales":
            xml = self.build_sales_voucher_xml(company, transactions)
        elif voucher_type == "Purchase":
            xml = self.build_purchase_voucher_xml(company, transactions)
        else:
            return {
                "success": False,
                "error": f"Unsupported voucher type: {voucher_type}",
                "upsert": upsert_results,
            }

        if not xml:
            return {
                "success": False,
                "error": "No valid vouchers to push (all filtered out)",
                "upsert": upsert_results,
            }

        # Push in batches by splitting XML isn't practical for a single envelope,
        # so we push the entire XML at once
        try:
            push_result = await self.push_xml(xml, company=company)
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to push to Tally: {str(e)}",
                "upsert": upsert_results,
            }

        return {
            "success": push_result.get("success", False),
            "created": push_result.get("created", 0),
            "altered": push_result.get("altered", 0),
            "errors": push_result.get("errors", 0),
            "combined": push_result.get("combined", 0),
            "ignored": push_result.get("ignored", 0),
            "lastvchid": push_result.get("lastvchid", ""),
            "line_errors": [],
            "upsert": upsert_results,
            "error": push_result.get("error", ""),
        }

    @staticmethod
    def _convert_date(date_str: str) -> str:
        """Convert various date formats to Tally's YYYYMMDD format (e.g. '20080402')."""
        if not date_str:
            return ""
        date_str = str(date_str).strip()

        # Already YYYYMMDD (8 digits)
        if re.match(r'^\d{8}$', date_str):
            return date_str

        # YYYY-MM-DD
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            return date_str.replace("-", "")

        # DD/MM/YYYY
        if "/" in date_str:
            parts = date_str.split("/")
            if len(parts) == 3:
                dd, mm, yyyy = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if len(yyyy) == 2:
                    yyyy = "20" + yyyy
                return f"{yyyy}{mm.zfill(2)}{dd.zfill(2)}"

        # DD-MM-YYYY or YYYY-MM-DD
        if "-" in date_str:
            parts = date_str.split("-")
            if len(parts) == 3:
                p0, p1, p2 = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if len(p0) == 4:
                    # YYYY-MM-DD
                    return f"{p0}{p1.zfill(2)}{p2.zfill(2)}"
                else:
                    # DD-MM-YYYY
                    if len(p2) == 2:
                        p2 = "20" + p2
                    return f"{p2}{p1.zfill(2)}{p0.zfill(2)}"

        # DD.MM.YYYY
        if "." in date_str:
            parts = date_str.split(".")
            if len(parts) == 3:
                dd, mm, yyyy = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if len(yyyy) == 2:
                    yyyy = "20" + yyyy
                return f"{yyyy}{mm.zfill(2)}{dd.zfill(2)}"

        # Fallback: try Python's datetime parsing for other formats
        try:
            from datetime import datetime
            for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
                try:
                    dt = datetime.strptime(date_str, fmt)
                    return dt.strftime("%Y%m%d")
                except ValueError:
                    continue
        except Exception:
            pass

        logger.warning(f"_convert_date: unrecognized format '{date_str}', returning as-is")
        return date_str

    # ==========================================================
    #  Internal helpers
    # ==========================================================

    @staticmethod
    def _compute_financial_year(tally_dates: List[str]) -> tuple:
        """
        Given a list of YYYYMMDD date strings, return (SVFROMDATE, SVTODATE)
        covering the Indian financial year (1-Apr to 31-Mar) that spans all dates.

        Returns ("", "") if the list is empty.
        """
        if not tally_dates:
            return ("", "")

        min_date = min(tally_dates)
        max_date = max(tally_dates)

        # Determine the financial year start for the earliest date
        # Indian FY: April 1 to March 31
        # Date format is YYYYMMDD, so year is [0:4], month is [4:6]
        min_year = int(min_date[:4])
        min_month = int(min_date[4:6])
        if min_month < 4:
            # Jan-Mar belongs to previous year's FY (e.g., Jan 2025 → FY starting Apr 2024)
            fy_start_year = min_year - 1
        else:
            fy_start_year = min_year

        # Determine the financial year end for the latest date
        max_year = int(max_date[:4])
        max_month = int(max_date[4:6])
        if max_month < 4:
            fy_end_year = max_year
        else:
            fy_end_year = max_year + 1

        sv_from = f"{fy_start_year}0401"   # 1st April of start FY
        sv_to = f"{fy_end_year}0331"       # 31st March of end FY

        return (sv_from, sv_to)

    @staticmethod
    def _build_static_vars(
        company: str = None,
        extra_vars: Dict[str, str] = None,
    ) -> str:
        """Build static variable XML tags for SVCURRENTCOMPANY and extras."""
        parts = []
        if company:
            parts.append(f"<SVCURRENTCOMPANY>{_xml_escape(company)}</SVCURRENTCOMPANY>")
        if extra_vars:
            for k, v in extra_vars.items():
                parts.append(f"<{k}>{_xml_escape(v)}</{k}>")
        return "\n".join(parts)

    def _wrap_import_envelope(self, inner_xml: str, company: str = None) -> str:
        """Wrap raw XML in a Tally import envelope (All Masters)."""
        sv_block = self._build_static_vars(company)
        sv_section = f"<STATICVARIABLES>{sv_block}</STATICVARIABLES>" if sv_block else ""

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Import</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>All Masters</ID>
  </HEADER>
  <BODY>
    <DESC>
      {sv_section}
    </DESC>
    <DATA>
      <TALLYMESSAGE xmlns:UDF="TallyUDF">
        {inner_xml}
      </TALLYMESSAGE>
    </DATA>
  </BODY>
</ENVELOPE>"""


# ------------------------------------------------------------------
# Global singleton
# ------------------------------------------------------------------
tally_connector = TallyConnector()
