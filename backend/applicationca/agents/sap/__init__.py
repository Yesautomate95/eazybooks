"""SAP S/4HANA Cloud agent pipelines (extract → match → build → post).

Mirrors applicationca/agents/zoho/ and applicationca/agents/xero/. Each module
here is a STUB to be filled in following the Zoho pipeline as the template, with
two SAP-specific changes:
  * the matcher resolves SAP GL accounts / business partners (via sap_aliases +
    sap_connector list_*), and
  * the built payload is a GL journal entry (header + debit/credit items) rather
    than a Zoho/Xero invoice — see sap_connector.post_journal_entry.
"""
