"""
HTTP connector to QuickBooks Desktop (via mock server in dev, via Electron
bridge in production).

QuickBooks Desktop does not expose HTTP natively. Production speaks QBXML to
QB Desktop on the user's machine via either:
  - QB Web Connector (SOAP polling)
  - qbXMLRP2.dll (COM, Windows)

Both bridges are owned by the Electron desktop app. From the backend's
perspective, this connector POSTs QBXML to a URL and gets QBXML back. In dev
that URL is the mock_qb server (localhost:9001). In prod the backend uses
WebSocket to Electron instead — see ConnectionManager.push_to_qb().

Mirrors the shape of TallyConnector for parity.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)


QBXML_HEADER = '<?xml version="1.0" encoding="utf-8"?>\n<?qbxml version="13.0"?>\n'


def build_qbxml(operation_xml: str, on_error: str = "stopOnError") -> str:
    """Wrap an inner *Rq fragment in a QBXML envelope."""
    return (
        f'{QBXML_HEADER}<QBXML>\n'
        f'  <QBXMLMsgsRq onError="{on_error}">\n'
        f'    {operation_xml.strip()}\n'
        f'  </QBXMLMsgsRq>\n'
        f'</QBXML>'
    )


def _strip_namespaces(xml_text: str) -> str:
    return re.sub(r'\sxmlns(:\w+)?="[^"]*"', '', xml_text)


def _findall_text(elem: ET.Element, tag: str) -> List[str]:
    return [e.text or "" for e in elem.iter(tag)]


def _first_text(elem: Optional[ET.Element], tag: str, default: str = "") -> str:
    if elem is None:
        return default
    found = elem.find(tag)
    return (found.text or default) if found is not None else default


class QBConnector:
    """Direct QBXML/HTTP client. Targets mock_qb in dev or an HTTP-fronted
    Electron bridge in prod."""

    def __init__(self, base_url: str = None, timeout: int = 30):
        self.base_url = base_url or settings.QB_URL
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _post(self, qbxml: str) -> str:
        logger.debug(f"QBXML POST -> {self.base_url}")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.base_url,
                content=qbxml.encode("utf-8"),
                headers={"Content-Type": "text/xml"},
            )
            resp.raise_for_status()
            return resp.text

    @staticmethod
    def _parse(xml_text: str) -> ET.Element:
        return ET.fromstring(_strip_namespaces(xml_text))

    @staticmethod
    def _check_status(rs: ET.Element, op: str) -> None:
        code = rs.attrib.get("statusCode", "0")
        if code != "0":
            msg = rs.attrib.get("statusMessage", "Unknown QB error")
            raise RuntimeError(f"{op} failed (statusCode={code}): {msg}")

    # ------------------------------------------------------------------
    # Health / company
    # ------------------------------------------------------------------

    async def check_connection(self) -> bool:
        try:
            await self.get_host_info()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"QB connection check failed: {exc}")
            return False

    async def get_host_info(self) -> Dict[str, Any]:
        xml = build_qbxml('<HostQueryRq requestID="1"/>')
        resp = await self._post(xml)
        root = self._parse(resp)
        rs = root.find(".//HostQueryRs")
        self._check_status(rs, "HostQueryRs")
        host = rs.find("HostRet")
        return {
            "product": _first_text(host, "ProductName"),
            "country": _first_text(host, "Country"),
            "files": _findall_text(host, "CompanyFileName"),
        }

    async def get_companies(self) -> List[Dict[str, Any]]:
        info = await self.get_host_info()
        return [{"name": _filename_to_company(f), "file": f} for f in info["files"]]

    async def get_current_company(self) -> Dict[str, Any]:
        xml = build_qbxml('<CompanyQueryRq requestID="1"/>')
        resp = await self._post(xml)
        rs = self._parse(resp).find(".//CompanyQueryRs")
        self._check_status(rs, "CompanyQueryRs")
        ret = rs.find("CompanyRet")
        return {
            "name": _first_text(ret, "CompanyName"),
            "file": _first_text(ret, "CompanyFileName"),
            "country": _first_text(ret, "Country"),
            "fiscal_year_start": _first_text(ret, "FiscalYearStartMonth"),
        }

    # ------------------------------------------------------------------
    # Master data
    # ------------------------------------------------------------------

    async def get_accounts(self) -> List[Dict[str, Any]]:
        resp = await self._post(build_qbxml('<AccountQueryRq requestID="1"/>'))
        rs = self._parse(resp).find(".//AccountQueryRs")
        self._check_status(rs, "AccountQueryRs")
        return [
            {
                "list_id": _first_text(ret, "ListID"),
                "name": _first_text(ret, "Name"),
                "full_name": _first_text(ret, "FullName"),
                "account_type": _first_text(ret, "AccountType"),
                "balance": _first_text(ret, "Balance", "0.00"),
            }
            for ret in rs.findall("AccountRet")
        ]

    async def get_customers(self) -> List[Dict[str, Any]]:
        resp = await self._post(build_qbxml('<CustomerQueryRq requestID="1"/>'))
        rs = self._parse(resp).find(".//CustomerQueryRs")
        self._check_status(rs, "CustomerQueryRs")
        return [_simple_party(ret) for ret in rs.findall("CustomerRet")]

    async def get_vendors(self) -> List[Dict[str, Any]]:
        resp = await self._post(build_qbxml('<VendorQueryRq requestID="1"/>'))
        rs = self._parse(resp).find(".//VendorQueryRs")
        self._check_status(rs, "VendorQueryRs")
        return [_simple_party(ret) for ret in rs.findall("VendorRet")]

    async def get_items(self) -> List[Dict[str, Any]]:
        resp = await self._post(build_qbxml('<ItemQueryRq requestID="1"/>'))
        rs = self._parse(resp).find(".//ItemQueryRs")
        self._check_status(rs, "ItemQueryRs")
        items: List[Dict[str, Any]] = []
        for ret in list(rs):
            items.append({
                "list_id": _first_text(ret, "ListID"),
                "name": _first_text(ret, "Name"),
                "type": ret.tag.replace("Item", "").replace("Ret", ""),
                "rate": _first_text(ret, "SalesPrice", "0.00"),
            })
        return items

    # ------------------------------------------------------------------
    # Master writes
    # ------------------------------------------------------------------

    async def create_customer(self, name: str, company: str = None, email: str = None) -> Dict[str, Any]:
        inner = "<CustomerAddRq requestID=\"1\"><CustomerAdd>"
        inner += f"<Name>{_escape(name)}</Name>"
        if company:
            inner += f"<CompanyName>{_escape(company)}</CompanyName>"
        if email:
            inner += f"<Email>{_escape(email)}</Email>"
        inner += "</CustomerAdd></CustomerAddRq>"
        rs = self._parse(await self._post(build_qbxml(inner))).find(".//CustomerAddRs")
        self._check_status(rs, "CustomerAddRs")
        ret = rs.find("CustomerRet")
        return {"list_id": _first_text(ret, "ListID"), "name": _first_text(ret, "Name")}

    async def create_vendor(self, name: str, company: str = None, email: str = None) -> Dict[str, Any]:
        inner = "<VendorAddRq requestID=\"1\"><VendorAdd>"
        inner += f"<Name>{_escape(name)}</Name>"
        if company:
            inner += f"<CompanyName>{_escape(company)}</CompanyName>"
        if email:
            inner += f"<Email>{_escape(email)}</Email>"
        inner += "</VendorAdd></VendorAddRq>"
        rs = self._parse(await self._post(build_qbxml(inner))).find(".//VendorAddRs")
        self._check_status(rs, "VendorAddRs")
        ret = rs.find("VendorRet")
        return {"list_id": _first_text(ret, "ListID"), "name": _first_text(ret, "Name")}

    async def create_account(self, name: str, account_type: str) -> Dict[str, Any]:
        inner = (
            "<AccountAddRq requestID=\"1\"><AccountAdd>"
            f"<Name>{_escape(name)}</Name>"
            f"<AccountType>{_escape(account_type)}</AccountType>"
            "</AccountAdd></AccountAddRq>"
        )
        rs = self._parse(await self._post(build_qbxml(inner))).find(".//AccountAddRs")
        self._check_status(rs, "AccountAddRs")
        ret = rs.find("AccountRet")
        return {"list_id": _first_text(ret, "ListID"), "name": _first_text(ret, "Name")}

    # ------------------------------------------------------------------
    # Transactions: invoice (sales) and bill (purchase)
    # ------------------------------------------------------------------

    async def push_invoice(self, invoice_xml: str) -> Dict[str, Any]:
        """invoice_xml is an <InvoiceAddRq>...</InvoiceAddRq> fragment."""
        rs = self._parse(await self._post(build_qbxml(invoice_xml))).find(".//InvoiceAddRs")
        self._check_status(rs, "InvoiceAddRs")
        ret = rs.find("InvoiceRet")
        return {"txn_id": _first_text(ret, "TxnID"), "ref_number": _first_text(ret, "RefNumber")}

    async def push_bill(self, bill_xml: str) -> Dict[str, Any]:
        """bill_xml is a <BillAddRq>...</BillAddRq> fragment."""
        rs = self._parse(await self._post(build_qbxml(bill_xml))).find(".//BillAddRs")
        self._check_status(rs, "BillAddRs")
        ret = rs.find("BillRet")
        return {"txn_id": _first_text(ret, "TxnID"), "ref_number": _first_text(ret, "RefNumber")}

    async def push_qbxml(self, qbxml: str) -> str:
        """Forward an already-built QBXML envelope and return raw response."""
        return await self._post(qbxml)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _simple_party(ret: ET.Element) -> Dict[str, Any]:
    return {
        "list_id": _first_text(ret, "ListID"),
        "name": _first_text(ret, "Name"),
        "company": _first_text(ret, "CompanyName"),
        "email": _first_text(ret, "Email"),
        "balance": _first_text(ret, "Balance", "0.00"),
    }


def _filename_to_company(path: str) -> str:
    if not path:
        return ""
    base = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0]


def _escape(text: str) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


qb_connector = QBConnector()
