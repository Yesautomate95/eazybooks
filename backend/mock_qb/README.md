# Mock QuickBooks Desktop Server

Mock implementation of **QBXML over HTTP** for local development, mirroring the
`mock_tally` package layout. Listens on port **9001** (Tally mock uses 9000).

## Run

```bash
python -m mock_qb
```

## What this is

Real QuickBooks Desktop does not expose an HTTP endpoint. Production talks to
QB Desktop via:

- **QB Web Connector** (SOAP-based, polling) — recommended for cloud apps
- **qbXMLRP2.dll** (COM, Windows only) — for in-process apps

The Electron desktop app bridges the cloud backend (WebSocket) to local QB
Desktop on the user's machine. This mock substitutes for that bridge in dev so
the backend can speak QBXML without QB Desktop installed.

## Supported QBXML ops

- `HostQueryRq` — list connected company files
- `CompanyQueryRq` — current company info
- `AccountQueryRq` — chart of accounts
- `CustomerQueryRq` / `VendorQueryRq` / `ItemQueryRq`
- `CustomerAddRq` / `VendorAddRq` / `AccountAddRq`
- `InvoiceAddRq` / `BillAddRq`

## Debug endpoints

- `GET /` — service info + stats
- `GET /stats` — request counts
- `POST /select-company` — `{"name": "Acme Trading LLC"}` switches active file
