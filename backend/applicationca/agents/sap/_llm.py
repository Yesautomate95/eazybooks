"""Async OpenAI helper for the SAP agents — re-exports the shared, ERP-agnostic
helper used by the QBO agents."""

from applicationca.agents.qbo._llm import make_llm_async  # noqa: F401
