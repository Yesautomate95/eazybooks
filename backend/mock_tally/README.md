# Mock Tally Server

A comprehensive mock implementation of Tally's XML HTTP API for development on macOS (or any non-Windows system).

## Features

- **Full XML API Support**: Simulates all standard Tally XML API operations
- **Realistic Indian Business Data**: Pre-populated with realistic companies, ledgers, inventory, etc.
- **Read Operations**: Get companies, ledgers, groups, inventory, godowns, voucher types
- **Write Operations**: Create/update ledgers, create groups, import vouchers
- **Statistics**: Track API usage for debugging
- **Debug Endpoints**: JSON endpoints to inspect data

## Quick Start

### Option 1: Run as Python Module

```bash
cd tallyAutomation-backend
python -m mock_tally
```

### Option 2: Run with Uvicorn

```bash
cd tallyAutomation-backend
uvicorn mock_tally.server:app --host 0.0.0.0 --port 9000 --reload
```

### Option 3: Run the convenience script

```bash
cd tallyAutomation-backend
./run_mock_tally.sh
```

## Endpoints

### Main Tally API (XML)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/` | Main Tally XML API endpoint |

### Debug Endpoints (JSON)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check & server info |
| GET | `/stats` | Server statistics |
| GET | `/ledgers` | List all ledgers |
| GET | `/companies` | List all companies |
| GET | `/groups` | List all groups |
| GET | `/inventory` | List all inventory items |
| GET | `/godowns` | List all godowns |
| POST | `/reset` | Reset data to initial state |

## Supported Tally Operations

### Export (Read) Operations

- **CompanyList** - Get list of companies
- **AllLedgers** - Get all ledgers
- **LedgerDetails** - Get specific ledger details (with filter)
- **AllGroups** - Get account groups
- **AllStockItems** - Get inventory/stock items
- **AllGodowns** - Get warehouse locations
- **VoucherTypes** - Get voucher types
- **Units** - Get units of measure
- **StockGroups** - Get stock item groups

### Import (Write) Operations

- **Create Ledger** - Add new ledger
- **Alter Ledger** - Update existing ledger
- **Create Group** - Add new account group
- **Import Voucher** - Import sales/purchase/receipt/payment vouchers

## Sample Data

The mock server includes realistic Indian business data:

### Companies (7)
- Sharma Enterprises Pvt Ltd
- Gupta Trading Co
- Patel Industries
- Kumar Electronics
- Mehta Textiles
- Agarwal & Sons
- Demo Company (Educational)

### Ledgers (90+)
- **Bank Accounts**: HDFC, ICICI, SBI, Axis, Kotak, BOB
- **Sundry Debtors**: Reliance, TCS, Infosys, Wipro, HCL, etc.
- **Sundry Creditors**: L&T, Tata Steel, JSW, Asian Paints, etc.
- **Sales/Purchase Accounts**: With GST variants (5%, 12%, 18%)
- **Expenses**: Rent, Salary, Electricity, Professional Fees, etc.
- **Duties & Taxes**: CGST, SGST, IGST (Payable & Input)
- **Fixed Assets**: Furniture, Computers, Vehicles, Buildings

### Inventory (20+)
- Electronics: Laptops, Desktops, Monitors, Printers
- Raw Materials: Steel, Aluminium, Copper, Plastics
- Office Supplies: Paper, Cartridges, Stationery
- Furniture: Chairs, Desks, Cabinets

### Godowns (9)
- Main Warehouse (Mumbai)
- Regional warehouses in Pune, Delhi NCR, Bengaluru, Chennai
- Showroom Stock, Defective Goods

## Testing with cURL

### Check Connection
```bash
curl -X POST http://localhost:9000 \
  -H "Content-Type: text/xml" \
  -d '<?xml version="1.0"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>CompanyList</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="CompanyList">
            <TYPE>Company</TYPE>
            <FETCH>NAME</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>'
```

### Get Ledgers
```bash
curl -X POST http://localhost:9000 \
  -H "Content-Type: text/xml" \
  -d '<?xml version="1.0"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>AllLedgers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        <SVCURRENTCOMPANY>Sharma Enterprises Pvt Ltd</SVCURRENTCOMPANY>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="AllLedgers">
            <TYPE>Ledger</TYPE>
            <FETCH>NAME,PARENT,OPENINGBALANCE</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>'
```

### Create Ledger
```bash
curl -X POST http://localhost:9000 \
  -H "Content-Type: text/xml" \
  -d '<?xml version="1.0"?>
<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>All Masters</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE>
          <LEDGER Action="Create">
            <NAME>Test Customer</NAME>
            <PARENT>Sundry Debtors</PARENT>
            <ADDRESS>123 Test Street</ADDRESS>
            <LEDSTATENAME>Maharashtra</LEDSTATENAME>
            <COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE>
            <PARTYGSTIN>27XXXXX1234X1ZX</PARTYGSTIN>
          </LEDGER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>'
```

## Integration with Your App

### Update your .env.development

The mock server runs on port 9000, same as real Tally. Your app should work without changes if it's configured to connect to `localhost:9000`.

### For the Electron WebSocket flow

The mock server only handles direct HTTP requests. For development without the Electron app:

1. Run the mock Tally server on port 9000
2. Modify your backend to call the mock server directly instead of going through WebSocket
3. Or create a simple bridge that forwards WebSocket requests to HTTP

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Your Development Setup                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐ │
│  │   Frontend   │────▶│   Backend    │────▶│  Mock Tally      │ │
│  │   React      │     │   FastAPI    │     │  Server          │ │
│  │   :3000      │     │   :8000      │     │  :9000           │ │
│  └──────────────┘     └──────────────┘     └──────────────────┘ │
│                                                                  │
│  All running locally on macOS - no Windows/VM required!         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Files

```
mock_tally/
├── __init__.py          # Package initialization
├── __main__.py          # Entry point for python -m mock_tally
├── server.py            # FastAPI server with all endpoints
├── sample_data.py       # Realistic Indian business data
├── xml_generators.py    # XML response generators
└── README.md            # This file
```

## License

Part of the EazyBooks project. For internal development use.
