"""
Contract tests for the ERP bank-voucher payload builders (QBO / Zoho / Xero).

These assert the *normalized-voucher contract* that all three builders share —
they all take the same input voucher and return ``(entry_type, payload)`` for the
four Tally voucher types (Receipt/Payment/Contra/Journal), normalize the amount,
encode direction via the type, and raise ``BankVoucherError`` on malformed input.

They are deliberately ERP-agnostic (no exact-JSON assertions) so they verify the
shared contract every twin must honor without pinning each ERP's native field
names. Pure-stdlib builders are loaded BY FILE PATH so this runs under the system
interpreter without the (Windows-built) venv or any third-party deps.

Run: ``python tests/test_erp_bank_payload_contract.py``  (also pytest-compatible)
"""

import importlib.util
import os

_BE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MODULES = {
    "qbo": ("applicationca/agents/qbo/bank_payload.py", "build_qbo_bank_payload"),
    "zoho": ("applicationca/agents/zoho/bank_payload.py", "build_zoho_bank_payload"),
    "xero": ("applicationca/agents/xero/bank_payload.py", "build_xero_bank_payload"),
}

_VOUCHER_TYPES = ("Receipt", "Payment", "Contra", "Journal")


def _load(rel_path):
    spec = importlib.util.spec_from_file_location("bp_" + rel_path.replace("/", "_"), os.path.join(_BE, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _base_voucher(vtype):
    return {
        "date": "05/04/2024",
        "narration": "NEFT from ACME",
        "amount": 1234.567,            # must be normalized to 1234.57, abs
        "voucher_type": vtype,
        "party_ledger": "Sales",
        "party_ledger_id": "57",
        "party_ledger_code": "200",     # Xero books line items by AccountCode (ignored by QBO/Zoho)
        "bank_ledger": "HDFC Current A/c",
        "bank_ledger_id": "35",
        "bank_ledger_code": "090",
        "reference": "UTR123",
        "counterparty": {"id": "12", "name": "ACME", "type": "Customer"},
    }


def _check_erp(erp, rel_path, fn_name):
    mod = _load(rel_path)
    build = getattr(mod, fn_name)
    BankVoucherError = mod.BankVoucherError
    entry_map = mod.VOUCHER_TYPE_TO_ENTRY
    results = []

    # 1. All four voucher types build into (entry_type:str, payload:dict).
    payloads = {}
    for vt in _VOUCHER_TYPES:
        et, payload = build(_base_voucher(vt), bank_account_id="35", bank_account_name="HDFC Current A/c")
        assert isinstance(et, str) and et, f"{erp} {vt}: entry_type must be non-empty str, got {et!r}"
        assert et == entry_map[vt], f"{erp} {vt}: entry_type {et!r} != map {entry_map[vt]!r}"
        assert isinstance(payload, dict) and payload, f"{erp} {vt}: payload must be non-empty dict"
        payloads[vt] = (et, payload)
    results.append("4 voucher types build OK -> " + ", ".join(f"{vt}:{payloads[vt][0]}" for vt in _VOUCHER_TYPES))

    # 2. Direction matters: Receipt and Payment must not produce identical payloads.
    assert payloads["Receipt"][1] != payloads["Payment"][1], f"{erp}: Receipt and Payment payloads are identical (direction lost)"
    results.append("Receipt != Payment (direction encoded)")

    # 3. Amount is normalized (abs + rounded to 2dp). Negative input -> positive.
    neg = _base_voucher("Receipt"); neg["amount"] = -1234.567
    _, p_neg = build(neg, bank_account_id="35", bank_account_name="HDFC")
    blob = repr(p_neg)
    assert "1234.57" in blob, f"{erp}: amount not normalized to 1234.57 in {blob[:200]}"
    assert "-1234" not in blob, f"{erp}: negative amount leaked"
    results.append("amount abs+round to 1234.57")

    # 4. Error handling — each must raise BankVoucherError.
    cases = {
        "unknown type": {"voucher_type": "Bogus"},
        "zero amount": {"amount": 0},
        "bad date": {"date": "not-a-date"},
        # No party reference at all (id, code, name) -> every ERP must fail to
        # resolve the counter-account.
        "missing party ref": {"party_ledger_id": None, "party_ledger_code": None, "party_ledger": None},
    }
    for label, override in cases.items():
        v = _base_voucher("Payment"); v.update(override)
        try:
            build(v, bank_account_id="35", bank_account_name="HDFC")
            raise AssertionError(f"{erp}: expected BankVoucherError for {label}, none raised")
        except BankVoucherError:
            pass
    results.append("BankVoucherError raised for: " + ", ".join(cases))
    return results


def test_all_erps():
    for erp, (rel, fn) in _MODULES.items():
        _check_erp(erp, rel, fn)


if __name__ == "__main__":
    failures = 0
    for erp, (rel, fn) in _MODULES.items():
        try:
            for line in _check_erp(erp, rel, fn):
                print(f"  [{erp:4}] {line}")
            print(f"  [{erp:4}] PASS\n")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  [{erp:4}] FAIL: {exc}\n")
    print("=" * 60)
    print("ALL CONTRACT TESTS PASSED" if not failures else f"{failures} ERP(S) FAILED")
    raise SystemExit(1 if failures else 0)
