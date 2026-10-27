"""One-shot script: add Depends(require_permission(...)) to existing FastAPI routes.

Run from the backend repo root. Idempotent — re-running doesn't duplicate the
decorator argument because the regex only matches the simple
`@router.METHOD("/path")` form.
"""
import re
from pathlib import Path

ROOT = Path("applicationca/contoller")

# Mapping per file: dict[exact_path_in_decorator] -> permission_key
# Paths must match the literal string inside the parentheses.
ROUTES = {
    # ── Identity / docs / chat (already applied in first pass; safe to re-run)
    "file_controller.py": {
        "/upload/": "documents.create",
        "/upload_db/": "documents.create",
        "/query/": "documents.read",
        "/search-suggested-files": "documents.read",
        "/view-file/": "documents.read",
        "/delete-file/": "documents.delete",
        "/smart-upload/": "documents.create",
        "/search-documents/": "documents.read",
    },
    "chat_controller.py": {
        "/get-all-chats": "chat.use",
        "/chat": "advisor.use",
        "/chat/langgraph": "advisor.use",
        "/chat/langgraph/advance": "advisor.use",
    },
    "vector.py": {"/search": "documents.read"},
    "database_controller.py": {
        # /add-user intentionally left ungated — that's the user-signup glue
        "/create-locker": "documents.create",
        "/create-client": "clients.create",
        "/create-chat": "chat.use",
        "/get-lockers": "documents.read",
        "/get-clients": "clients.read",
        "/get-files-by-locker": "documents.read",
        "/get-files-by-client": "documents.read",
        "/get-files-by-user": "documents.read",
        "/get-files-by-chat": "documents.read",
        "/get-file-info": "documents.read",
        "/get-chats-by-locker": "chat.use",
        "/get-chats-by-client": "chat.use",
        "/get-chats-by-user": "chat.use",
        "/get-chat-info": "chat.use",
        "/get-all-user-chats": "chat.use",
        "/get-user-dashboard": "clients.read",
        "/get-lockers-with-details": "documents.read",
        "/get-clients-with-details": "clients.read",
        "/delete-locker": "documents.delete",
        "/delete-client": "clients.delete",
    },
    # ── Tally workspace ──────────────────────────────────────────────
    "tally_controller.py": {
        "/check-tally-connection": "tally.read",
        "/sync-company-folders": "tally.sync",
        "/get-companies": "tally.read",
        "/get-cached-companies": "tally.read",
        "/get-cached-ledgers": "tally.read",
        "/get-inventory": "tally.read",
        "/get-ledgers": "tally.read",
        "/get-groups": "tally.read",
        "/get-godowns": "tally.read",
        "/get-unique-companies": "tally.read",
        "/get-ledger-details": "tally.read",
        "/ledgers/grouped": "tally.read",
        "/process": "tally.push",
        "/generate-xml": "tally.push",
        "/upload-sales-invoice": "tally.push",
        "/suggest-ledger": "tally.read",
        "/extract-bill": "tally.push",
        "/generate-sales-voucher-xml": "tally.push",
        "/ai-suggest-fields": "tally.read",
        "/push-to-tally": "tally.push",
        "/create-ledger": "tally.push",
        "/create-inventory": "tally.push",
        "/edit-ledger": "tally.push",
        "/create-group": "tally.push",
        "/tally-chat/sync": "chat.use",
        "/tally-chat": "chat.use",
        "/tally-chat/history": "chat.use",
        "/mapping-rules": "tally.push",
        "/mapping-rules/{rule_id}": "tally.push",
        "/mapping-rules/apply": "tally.push",
        "/mapping-rules/statistics": "tally.read",
        "/mapping-rules/suggestions": "tally.read",
        "/bank-statement/upload": "tally.push",
        "/bank-statement/preview/{batch_id}": "tally.read",
        "/bank-statement/transactions": "tally.push",
        "/bank-statement/check-duplicates": "tally.read",
        "/purchase-invoice/upload": "tally.push",
    },
    "qb_controller.py": {
        "/check-connection": "quickbooks.read",
        "/status": "quickbooks.read",
        "/get-companies": "quickbooks.read",
        "/get-accounts": "quickbooks.read",
        "/get-customers": "quickbooks.read",
        "/get-vendors": "quickbooks.read",
        "/get-items": "quickbooks.read",
        "/create-customer": "quickbooks.push",
        "/create-vendor": "quickbooks.push",
        "/create-account": "quickbooks.push",
        "/create-item": "quickbooks.push",
        "/alias": "quickbooks.push",
        "/create-batch": "quickbooks.push",
        "/preview-create-account": "quickbooks.read",
        "/push-invoice": "quickbooks.push",
        "/push-bill": "quickbooks.push",
        "/extract-invoice": "quickbooks.push",
        "/extract-bill": "quickbooks.push",
        "/bank/list-accounts": "quickbooks.read",
        "/bank/extract-and-match": "quickbooks.push",
        "/bank/manual-match": "quickbooks.push",
        "/bank/resolve-pending": "quickbooks.push",
        "/bank/save": "quickbooks.push",
        "/bank/reports": "quickbooks.read",
        "/bank/reports/{report_id}": "quickbooks.read",
        "/bank-entries/extract": "quickbooks.push",
        "/bank-entries/push-one": "quickbooks.push",
        "/bank-entries/open-bills": "quickbooks.read",
        "/bank-entries/open-invoices": "quickbooks.read",
        "/drafts": "quickbooks.read",
        "/drafts/{draft_id}": "quickbooks.read",
        "/chat": "chat.use",
        "/chat/history": "chat.use",
        "/brief/refresh": "quickbooks.push",
        "/brief": "quickbooks.read",
    },
    "qbo_oauth_controller.py": {
        # /callback is the OAuth redirect target — left public.
        "/connect": "quickbooks.connect",
        "/status": "quickbooks.read",
        "/disconnect": "quickbooks.connect",
    },
    "zoho_controller.py": {
        "/check-connection": "zoho.read",
        "/get-companies": "zoho.read",
        "/get-accounts": "zoho.read",
        "/get-customers": "zoho.read",
        "/get-vendors": "zoho.read",
        "/get-items": "zoho.read",
        "/create-customer": "zoho.push",
        "/create-vendor": "zoho.push",
        "/create-account": "zoho.push",
        "/create-item": "zoho.push",
        "/alias": "zoho.push",
        "/create-batch": "zoho.push",
        "/preview-create-account": "zoho.read",
        "/extract-invoice": "zoho.push",
        "/extract-bill": "zoho.push",
        "/push-invoice": "zoho.push",
        "/push-bill": "zoho.push",
        "/bank/list-accounts": "zoho.read",
        "/bank/extract-and-match": "zoho.push",
        "/bank/manual-match": "zoho.push",
        "/bank/resolve-pending": "zoho.push",
        "/bank/save": "zoho.push",
        "/bank/reports": "zoho.read",
        "/bank/reports/{report_id}": "zoho.read",
        "/bank-entries/extract": "zoho.push",
        "/bank-entries/push-one": "zoho.push",
        "/bank-entries/open-bills": "zoho.read",
        "/bank-entries/open-invoices": "zoho.read",
        "/drafts": "zoho.read",
        "/drafts/{draft_id}": "zoho.read",
        "/chat": "chat.use",
        "/chat/history": "chat.use",
        "/brief/refresh": "zoho.push",
        "/brief": "zoho.read",
    },
    "zoho_oauth_controller.py": {
        "/connect": "zoho.connect",
        "/status": "zoho.read",
        "/disconnect": "zoho.connect",
    },
    "xero_controller.py": {
        "/check-connection": "xero.read",
        "/get-companies": "xero.read",
        "/get-accounts": "xero.read",
        "/get-customers": "xero.read",
        "/get-vendors": "xero.read",
        "/get-items": "xero.read",
        "/create-customer": "xero.push",
        "/create-vendor": "xero.push",
        "/create-account": "xero.push",
        "/create-item": "xero.push",
        "/alias": "xero.push",
        "/create-batch": "xero.push",
        "/preview-create-account": "xero.read",
        "/extract-invoice": "xero.push",
        "/extract-bill": "xero.push",
        "/push-invoice": "xero.push",
        "/push-bill": "xero.push",
        "/bank/list-accounts": "xero.read",
        "/bank/extract-and-match": "xero.push",
        "/bank/manual-match": "xero.push",
        "/bank/resolve-pending": "xero.push",
        "/bank/save": "xero.push",
        "/bank/reports": "xero.read",
        "/bank/reports/{report_id}": "xero.read",
        "/bank-entries/extract": "xero.push",
        "/bank-entries/push-one": "xero.push",
        "/bank-entries/open-bills": "xero.read",
        "/bank-entries/open-invoices": "xero.read",
        "/drafts": "xero.read",
        "/drafts/{draft_id}": "xero.read",
        "/chat": "chat.use",
        "/chat/history": "chat.use",
        "/brief/refresh": "xero.push",
        "/brief": "xero.read",
    },
    "xero_oauth_controller.py": {
        "/connect": "xero.connect",
        "/status": "xero.read",
        "/disconnect": "xero.connect",
    },
    "mis_controller.py": {
        "/financial-summary": "reports.read",
        "/ledgers": "reports.read",
        "/monthly-trends": "reports.read",
        "/inventory": "reports.read",
        "/voucher-distribution": "reports.read",
        "/gst-summary": "reports.read",
        "/cost-center": "reports.read",
    },
    "bank_recon_controller.py": {
        "/bank-recon/bank-ledgers": "bookkeeping.read",
        "/bank-recon/next-report-name": "bookkeeping.read",
        "/bank-recon/reports": "bookkeeping.read",
        "/bank-recon/reports/{report_id}": "bookkeeping.read",
        "/bank-recon/reports/{report_id}/process": "bookkeeping.update",
        "/bank-recon/reports/{report_id}/transactions": "bookkeeping.read",
        "/bank-recon/reports/{report_id}/matches": "bookkeeping.read",
        "/bank-recon/matches/{match_id}": "bookkeeping.update",
        "/bank-recon/matches/bulk": "bookkeeping.update",
        "/bank-recon/reports/{report_id}/manual-match": "bookkeeping.update",
        "/bank-recon/reports/{report_id}/summary": "bookkeeping.read",
        "/bank-recon/reports/{report_id}/save": "bookkeeping.update",
        "/bank-recon/reports/{report_id}/download": "reports.export",
    },
}

# Some paths have multiple decorators in the same file (different HTTP methods).
# Map "METHOD /path" -> permission_key to override the default in ROUTES.
METHOD_OVERRIDES = {
    "bank_recon_controller.py": {
        "POST /bank-recon/reports": "bookkeeping.create",
        "PATCH /bank-recon/reports/{report_id}": "bookkeeping.update",
        "DELETE /bank-recon/reports/{report_id}": "bookkeeping.delete",
        "DELETE /bank-recon/matches/{match_id}": "bookkeeping.update",
    },
    "qb_controller.py": {
        "DELETE /bank/reports/{report_id}": "quickbooks.push",
        "POST /drafts": "quickbooks.push",
        "DELETE /drafts/{draft_id}": "quickbooks.push",
        "DELETE /chat/history": "chat.use",
    },
    "zoho_controller.py": {
        "DELETE /bank/reports/{report_id}": "zoho.push",
        "POST /drafts": "zoho.push",
        "DELETE /drafts/{draft_id}": "zoho.push",
        "DELETE /chat/history": "chat.use",
    },
    "xero_controller.py": {
        "DELETE /bank/reports/{report_id}": "xero.push",
        "POST /drafts": "xero.push",
        "DELETE /drafts/{draft_id}": "xero.push",
        "DELETE /chat/history": "chat.use",
    },
    "tally_controller.py": {
        "DELETE /tally-chat/history": "chat.use",
        "POST /mapping-rules": "tally.push",
        "PUT /mapping-rules/{rule_id}": "tally.push",
        "DELETE /mapping-rules/{rule_id}": "tally.push",
    },
}

IMPORT_LINE = "from applicationca.rbac import require_permission"
# Matches @router.METHOD("/path") with no extra args (i.e. not yet gated).
ROUTE_DECORATOR_RE = re.compile(
    r'(@router\.(get|post|put|patch|delete)\()(["\'])(?P<path>[^"\']+)(\3)(\s*\))'
)


def ensure_import(text: str) -> str:
    """Insert `from applicationca.rbac import require_permission` and make
    sure `Depends` is part of the fastapi import — the gated decorator uses
    `Depends(require_permission(...))` so both must be present or the module
    fails to load at startup.
    """
    if IMPORT_LINE not in text:
        lines = text.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines):
            if (
                line.startswith("from applicationca.")
                or line.startswith("from utils.")
                or line.startswith("from config.")
            ):
                insert_at = i + 1
        if insert_at == 0:
            for i, line in enumerate(lines):
                if line.startswith("from fastapi"):
                    insert_at = i + 1
                    break
        text = "".join(lines[:insert_at] + [IMPORT_LINE + "\n"] + lines[insert_at:])

    # Ensure `Depends` is in the fastapi import. Handles the common
    # `from fastapi import APIRouter, Query` shape — splices Depends in
    # alphabetically after APIRouter if not already present.
    fastapi_re = re.compile(r'^(from fastapi import )([^\n]+)$', re.MULTILINE)
    def add_depends(m):
        names = [n.strip() for n in m.group(2).split(",") if n.strip()]
        if "Depends" in names:
            return m.group(0)
        names.append("Depends")
        names = sorted(set(names))
        return m.group(1) + ", ".join(names)
    text = fastapi_re.sub(add_depends, text, count=1)
    return text


def transform_routes(text: str, path_map: dict, method_overrides: dict) -> tuple[str, int]:
    count = 0

    def replace(m):
        nonlocal count
        method = m.group(2).upper()
        path = m.group("path")
        decorator_open = m.group(1)
        quote = m.group(3)
        close = m.group(6)
        perm = method_overrides.get(f"{method} {path}") or path_map.get(path)
        if perm is None:
            return m.group(0)
        new_arg = f', dependencies=[Depends(require_permission("{perm}"))]'
        count += 1
        return f"{decorator_open}{quote}{path}{quote}{new_arg}{close}"

    new_text = ROUTE_DECORATOR_RE.sub(replace, text)
    return new_text, count


def main():
    for filename, mapping in ROUTES.items():
        path = ROOT / filename
        if not path.exists():
            print(f"skipping {filename} (not present)")
            continue
        text = path.read_text()
        original = text
        text = ensure_import(text)
        text, n = transform_routes(
            text, mapping, METHOD_OVERRIDES.get(filename, {})
        )
        if text != original:
            path.write_text(text)
        print(f"{filename}: {n} route(s) gated")


if __name__ == "__main__":
    main()
