"""
QBXML response generators for mock QuickBooks Desktop server.

QBXML envelope:
    <?xml version="1.0" encoding="utf-8"?>
    <?qbxml version="13.0"?>
    <QBXML>
      <QBXMLMsgsRs>
        <{Operation}Rs requestID="..." statusCode="0" statusSeverity="Info" statusMessage="Status OK">
          ...
        </{Operation}Rs>
      </QBXMLMsgsRs>
    </QBXML>
"""

from typing import List, Dict
from xml.sax.saxutils import escape


HEADER = '<?xml version="1.0" encoding="utf-8"?>\n<?qbxml version="13.0"?>\n'


def _envelope(inner: str) -> str:
    return f"{HEADER}<QBXML>\n  <QBXMLMsgsRs>\n{inner}\n  </QBXMLMsgsRs>\n</QBXML>"


def _rs_open(op: str, request_id: str, status_code: int = 0, severity: str = "Info", message: str = "Status OK") -> str:
    return (
        f'    <{op}Rs requestID="{escape(request_id or "0")}" '
        f'statusCode="{status_code}" statusSeverity="{severity}" '
        f'statusMessage="{escape(message)}">'
    )


def _rs_close(op: str) -> str:
    return f"    </{op}Rs>"


def host_query_response(request_id: str, companies: List[Dict]) -> str:
    """HostQueryRs - returns connected company files."""
    files = "\n".join(
        f"      <CompanyFileName>{escape(c['file'])}</CompanyFileName>" for c in companies
    )
    inner = (
        _rs_open("HostQuery", request_id)
        + "\n      <HostRet>\n"
        + "        <ProductName>QuickBooks Premier 2023 (Mock)</ProductName>\n"
        + "        <MajorVersion>33</MajorVersion>\n"
        + "        <Country>US</Country>\n"
        + f"{files}\n"
        + "      </HostRet>\n"
        + _rs_close("HostQuery")
    )
    return _envelope(inner)


def company_query_response(request_id: str, company: Dict) -> str:
    inner = (
        _rs_open("CompanyQuery", request_id)
        + "\n      <CompanyRet>\n"
        + f"        <CompanyName>{escape(company['name'])}</CompanyName>\n"
        + f"        <CompanyFileName>{escape(company['file'])}</CompanyFileName>\n"
        + f"        <Country>{escape(company.get('country', 'US'))}</Country>\n"
        + f"        <FiscalYearStartMonth>{escape(company.get('fiscal_year_start', 'January'))}</FiscalYearStartMonth>\n"
        + "      </CompanyRet>\n"
        + _rs_close("CompanyQuery")
    )
    return _envelope(inner)


def account_query_response(request_id: str, accounts: List[Dict]) -> str:
    rets = "\n".join(
        "      <AccountRet>\n"
        f"        <ListID>{escape(a['list_id'])}</ListID>\n"
        f"        <Name>{escape(a['name'])}</Name>\n"
        f"        <FullName>{escape(a['name'])}</FullName>\n"
        f"        <AccountType>{escape(a['account_type'])}</AccountType>\n"
        f"        <Balance>{escape(str(a.get('balance', '0.00')))}</Balance>\n"
        "        <IsActive>true</IsActive>\n"
        "      </AccountRet>"
        for a in accounts
    )
    inner = _rs_open("AccountQuery", request_id) + "\n" + rets + "\n" + _rs_close("AccountQuery")
    return _envelope(inner)


def customer_query_response(request_id: str, customers: List[Dict]) -> str:
    rets = "\n".join(
        "      <CustomerRet>\n"
        f"        <ListID>{escape(c['list_id'])}</ListID>\n"
        f"        <Name>{escape(c['name'])}</Name>\n"
        f"        <FullName>{escape(c['name'])}</FullName>\n"
        f"        <CompanyName>{escape(c.get('company', c['name']))}</CompanyName>\n"
        f"        <Email>{escape(c.get('email', ''))}</Email>\n"
        f"        <Balance>{escape(str(c.get('balance', '0.00')))}</Balance>\n"
        "        <IsActive>true</IsActive>\n"
        "      </CustomerRet>"
        for c in customers
    )
    inner = _rs_open("CustomerQuery", request_id) + "\n" + rets + "\n" + _rs_close("CustomerQuery")
    return _envelope(inner)


def vendor_query_response(request_id: str, vendors: List[Dict]) -> str:
    rets = "\n".join(
        "      <VendorRet>\n"
        f"        <ListID>{escape(v['list_id'])}</ListID>\n"
        f"        <Name>{escape(v['name'])}</Name>\n"
        f"        <CompanyName>{escape(v.get('company', v['name']))}</CompanyName>\n"
        f"        <Email>{escape(v.get('email', ''))}</Email>\n"
        f"        <Balance>{escape(str(v.get('balance', '0.00')))}</Balance>\n"
        "        <IsActive>true</IsActive>\n"
        "      </VendorRet>"
        for v in vendors
    )
    inner = _rs_open("VendorQuery", request_id) + "\n" + rets + "\n" + _rs_close("VendorQuery")
    return _envelope(inner)


def item_query_response(request_id: str, items: List[Dict]) -> str:
    rets = "\n".join(
        "      <ItemServiceRet>\n"
        f"        <ListID>{escape(i['list_id'])}</ListID>\n"
        f"        <Name>{escape(i['name'])}</Name>\n"
        f"        <Type>{escape(i.get('type', 'Service'))}</Type>\n"
        f"        <SalesPrice>{escape(str(i.get('rate', '0.00')))}</SalesPrice>\n"
        f"        <IncomeAccountRef><FullName>{escape(i.get('income_account', 'Sales'))}</FullName></IncomeAccountRef>\n"
        "        <IsActive>true</IsActive>\n"
        "      </ItemServiceRet>"
        for i in items
    )
    inner = _rs_open("ItemQuery", request_id) + "\n" + rets + "\n" + _rs_close("ItemQuery")
    return _envelope(inner)


def add_response(op: str, request_id: str, list_id: str, name: str) -> str:
    """Generic *AddRs for create-customer / create-vendor / create-account / create-item."""
    inner = (
        _rs_open(op, request_id)
        + f"\n      <{op[:-3]}Ret>\n"
        + f"        <ListID>{escape(list_id)}</ListID>\n"
        + f"        <Name>{escape(name)}</Name>\n"
        + f"      </{op[:-3]}Ret>\n"
        + _rs_close(op)
    )
    return _envelope(inner)


def invoice_add_response(request_id: str, txn_id: str, ref_number: str) -> str:
    inner = (
        _rs_open("InvoiceAdd", request_id)
        + "\n      <InvoiceRet>\n"
        + f"        <TxnID>{escape(txn_id)}</TxnID>\n"
        + f"        <RefNumber>{escape(ref_number)}</RefNumber>\n"
        + "      </InvoiceRet>\n"
        + _rs_close("InvoiceAdd")
    )
    return _envelope(inner)


def bill_add_response(request_id: str, txn_id: str, ref_number: str) -> str:
    inner = (
        _rs_open("BillAdd", request_id)
        + "\n      <BillRet>\n"
        + f"        <TxnID>{escape(txn_id)}</TxnID>\n"
        + f"        <RefNumber>{escape(ref_number)}</RefNumber>\n"
        + "      </BillRet>\n"
        + _rs_close("BillAdd")
    )
    return _envelope(inner)


def error_response(op: str, request_id: str, message: str, status_code: int = 3000) -> str:
    inner = _rs_open(op, request_id, status_code=status_code, severity="Error", message=message) + _rs_close(op)
    return _envelope(inner)
