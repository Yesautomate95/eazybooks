import os
from dotenv import load_dotenv

# Load .env file early
load_dotenv()


def _sap_env(name: str, *aliases: str) -> str:
    """Read a SAP setting and reject the placeholder values from `.env` templates."""
    for key in (name, *aliases):
        value = os.environ.get(key, "").strip()
        lowered = value.lower()
        if value and not any(
            marker in lowered
            for marker in ("your_", "actual_", "real_", "real-", "replace_", "<", ">")
        ):
            return value
    return ""


class _Settings:
    """Centralized configuration - reads all values from environment variables."""

    # --- AWS ---
    @property
    def AWS_ACCESS_KEY_ID(self) -> str:
        return os.environ.get("AWS_ACCESS_KEY_ID", "")

    @property
    def AWS_SECRET_ACCESS_KEY(self) -> str:
        return os.environ.get("AWS_SECRET_ACCESS_KEY", "")

    @property
    def AWS_DEFAULT_REGION(self) -> str:
        return os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")

    # --- OpenAI ---
    @property
    def OPENAI_API_KEY(self) -> str:
        return os.environ.get("OPENAI_API_KEY", "")

    @property
    def OPENAI_MODEL(self) -> str:
        return os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

    # --- Pinecone ---
    @property
    def PINECONE_API_KEY(self) -> str:
        return os.environ.get("PINECONE_API_KEY", "")

    @property
    def PINECONE_INDEX_NAME(self) -> str:
        return os.environ.get("PINECONE_INDEX_NAME", "aureus")

    # --- Database ---
    @property
    def DATABASE_URL(self) -> str:
        return os.environ.get("DATABASE_URL", "")

    # --- JWT ---
    @property
    def JWT_SECRET(self) -> str:
        return os.environ.get("JWT_SECRET", "")

    # --- Tavily ---
    @property
    def TAVILY_API_KEY(self) -> str:
        return os.environ.get("TAVILY_API_KEY", "")

    # --- OpenRouter ---
    @property
    def OPENROUTER_API_KEY(self) -> str:
        return os.environ.get("OPENROUTER_API_KEY", "")

    # --- Anthropic / Claude ---
    @property
    def ANTHROPIC_API_KEY(self) -> str:
        return os.environ.get("ANTHROPIC_API_KEY", "")

    @property
    def EXTRACTION_PROVIDER(self) -> str:
        """Extraction provider: 'openai', 'claude', or 'gemini'"""
        return os.environ.get("EXTRACTION_PROVIDER", "openai")

    @property
    def CLAUDE_EXTRACTION_MODEL(self) -> str:
        return os.environ.get("CLAUDE_EXTRACTION_MODEL", "claude-opus-4-7")

    # --- Google ---
    @property
    def GEMINI_API_KEY(self) -> str:
        return os.environ.get("GEMINI_API_KEY", "")

    @property
    def GOOGLE_API_KEY(self) -> str:
        return os.environ.get("GOOGLE_API_KEY", "")

    @property
    def GOOGLE_SEARCH_ENGINE_ID(self) -> str:
        return os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "")

    # --- Kite ---
    @property
    def KITE_API_KEY(self) -> str:
        return os.environ.get("KITE_API_KEY", "")

    @property
    def KITE_SECRET_KEY(self) -> str:
        return os.environ.get("KITE_SECRET_KEY", "")

    # --- Other APIs ---
    @property
    def STOCK_NEWS_API_KEY(self) -> str:
        return os.environ.get("STOCK_NEWS_API_KEY", "")

    @property
    def FACEBOOK_ACCESS_TOKEN(self) -> str:
        return os.environ.get("FACEBOOK_ACCESS_TOKEN", "")

    @property
    def ALPHA_VANTAGE_API_KEY(self) -> str:
        return os.environ.get("ALPHA_VANTAGE_API_KEY", "")

    @property
    def DEEPSEEK_API_KEY(self) -> str:
        return os.environ.get("DEEPSEEK_API_KEY", "")

    # --- Cloudflare R2 ---
    @property
    def R2_ENDPOINT_URL(self) -> str:
        return os.environ.get("CLOUDFLARE_R2_ENDPOINT_URL", "")

    @property
    def R2_BUCKET_NAME(self) -> str:
        return os.environ.get("CLOUDFLARE_R2_BUCKET_NAME", "eazybooks")

    @property
    def R2_ACCESS_KEY_ID(self) -> str:
        return os.environ.get("CLOUDFLARE_R2_ACCESS_KEY_ID", "")

    @property
    def R2_SECRET_ACCESS_KEY(self) -> str:
        return os.environ.get("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "")

    @property
    def R2_PUBLIC_URL(self) -> str:
        # Optional custom domain / public bucket URL for download links.
        # Falls back to the S3-compatible API endpoint (works if bucket is public).
        return os.environ.get("CLOUDFLARE_R2_PUBLIC_URL", "").rstrip("/")

    # --- Tally ---
    @property
    def TALLY_URL(self) -> str:
        return os.environ.get("TALLY_URL", "http://localhost:9000")

    # --- QuickBooks Desktop ---
    @property
    def QB_URL(self) -> str:
        return os.environ.get("QB_URL", "http://localhost:9001")

    # --- QuickBooks (mode switch: 'qbo' for Online via OAuth2/REST,
    #     'desktop' for QB Desktop via Electron bridge) ---
    @property
    def QB_MODE(self) -> str:
        return os.environ.get("QB_MODE", "qbo").lower()

    # --- QuickBooks Online (OAuth2 + REST) ---
    @property
    def QBO_CLIENT_ID(self) -> str:
        return os.environ.get("QBO_CLIENT_ID", "")

    @property
    def QBO_CLIENT_SECRET(self) -> str:
        return os.environ.get("QBO_CLIENT_SECRET", "")

    @property
    def QBO_REDIRECT_URI(self) -> str:
        configured = os.environ.get(
            "QBO_REDIRECT_URI",
            "http://localhost:8000/ca/qb/oauth/callback",
        ).strip()
        # Intuit must redirect to the public backend in Railway. Do not allow
        # a local callback or the old port-9001 value in the deployed flow.
        if self._is_railway:
            return "https://tallyautomation-backend-production.up.railway.app/ca/qb/oauth/callback"
        if configured.endswith(":9001/ca/qb/oauth/callback"):
            return configured.replace(":9001/ca/qb/oauth/callback", ":8000/ca/qb/oauth/callback")
        return configured

    @property
    def QBO_ENV(self) -> str:
        """sandbox | production. Controls which API base URL is used."""
        return os.environ.get("QBO_ENV", "sandbox").lower()

    @property
    def QBO_API_BASE(self) -> str:
        if self.QBO_ENV == "production":
            return "https://quickbooks.api.intuit.com"
        return "https://sandbox-quickbooks.api.intuit.com"

    @property
    def QBO_FRONTEND_RETURN_URL(self) -> str:
        """Where to redirect the browser after OAuth callback completes."""
        return os.environ.get("QBO_FRONTEND_RETURN_URL", "http://localhost:3000/dashboard/qb")

    @property
    def QBO_TOKEN_ENCRYPTION_KEY(self) -> str:
        """Fernet key used to encrypt QBO refresh tokens at rest. Generate with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
        """
        return os.environ.get("QBO_TOKEN_ENCRYPTION_KEY", "")

    # --- Zoho Books (OAuth2 + REST) ---
    # Zoho is data-center-specific. ZOHO_DC selects the accounts + API hosts
    # (com | in | eu | com.au | jp | ca | sa). A single Zoho token works across
    # every organization the user owns; the active org is passed per API call.
    @property
    def ZOHO_CLIENT_ID(self) -> str:
        return os.environ.get("ZOHO_CLIENT_ID", "")

    @property
    def ZOHO_CLIENT_SECRET(self) -> str:
        return os.environ.get("ZOHO_CLIENT_SECRET", "")

    @property
    def ZOHO_REDIRECT_URI(self) -> str:
        return os.environ.get("ZOHO_REDIRECT_URI", "http://localhost:8000/ca/zoho/oauth/callback")

    @property
    def ZOHO_DC(self) -> str:
        """Default Zoho data center for the authorize URL. The token response's
        api_domain (stored per user) authoritatively decides the API host after
        connect, so this only seeds the initial consent redirect."""
        return os.environ.get("ZOHO_DC", "com").lower()

    @property
    def ZOHO_SCOPES(self) -> str:
        return os.environ.get("ZOHO_SCOPES", "ZohoBooks.fullaccess.all")

    @property
    def ZOHO_FRONTEND_RETURN_URL(self) -> str:
        return os.environ.get("ZOHO_FRONTEND_RETURN_URL", "http://localhost:3000/dashboard/zoho")

    # --- Xero (OAuth2 + REST) ---
    # Xero issues one token set per user; GET /connections lists authorized
    # tenants (organisations), and the active tenant is sent as the
    # Xero-tenant-id header on every API call.
    @property
    def XERO_CLIENT_ID(self) -> str:
        return os.environ.get("XERO_CLIENT_ID", "")

    @property
    def XERO_CLIENT_SECRET(self) -> str:
        return os.environ.get("XERO_CLIENT_SECRET", "")

    @property
    def XERO_REDIRECT_URI(self) -> str:
        # Xero compares this value literally with the redirect URI registered
        # in the developer console. Remove only accidental whitespace; do not
        # add or remove a trailing slash from the configured path.
        configured = os.environ.get(
            "XERO_REDIRECT_URI",
            "http://localhost:8000/ca/xero/oauth/callback",
        ).strip()
        # Railway may retain an older XERO_REDIRECT_URI variable. The public
        # backend hostname is fixed for this deployment, so never let stale
        # Railway configuration send a different callback to Xero.
        if self._is_railway:
            return "https://tallyautomation-backend-production.up.railway.app/ca/xero/oauth/callback"
        return configured

    @property
    def XERO_SCOPES(self) -> str:
        # Xero retired the broad `accounting.transactions` and
        # `accounting.reports.read` scopes for apps created after 2 Mar 2026 —
        # requesting them now returns `invalid_scope`. Use the granular scopes:
        #   transactions -> accounting.invoices + accounting.banktransactions + accounting.payments
        #   reports.read -> per-report .read scopes (we only fetch P&L / BalanceSheet / TrialBalance)
        # `accounting.contacts` and `accounting.settings` are still valid broad scopes.
        return os.environ.get(
            "XERO_SCOPES",
            "openid profile email "
            "accounting.contacts accounting.settings "
            "accounting.invoices accounting.banktransactions accounting.payments "
            "accounting.reports.profitandloss.read accounting.reports.balancesheet.read "
            "accounting.reports.trialbalance.read offline_access",
        )

    @property
    def XERO_FRONTEND_RETURN_URL(self) -> str:
        configured = os.environ.get("XERO_FRONTEND_RETURN_URL", "http://localhost:3000/dashboard/xero").strip()
        if self._is_railway and configured.startswith(("http://localhost", "http://127.0.0.1")):
            return "https://www.eazybooks.ai/dashboard/xero"
        return configured

    @property
    def _is_railway(self) -> bool:
        return bool(
            os.environ.get("RAILWAY_ENVIRONMENT")
            or os.environ.get("RAILWAY_ENVIRONMENT_NAME")
            or os.environ.get("RAILWAY_PROJECT_ID")
            or os.environ.get("RAILWAY_SERVICE_ID")
        )

    # --- SAP S/4HANA Cloud (OAuth2 + OData) ---
    # SAP_AUTH_MODE selects the OAuth2 grant:
    #   client_credentials (default) — one technical Communication User per client
    #       tenant; no browser consent, no refresh token. We mint a fresh bearer
    #       token when the cached one nears expiry. Recommended for B2B where the
    #       CA connects on behalf of a client company.
    #   authorization_code — per-user consent (mirrors Zoho/Xero), rotating
    #       refresh_token; uses SAP_AUTHORIZE_URL + SAP_REDIRECT_URI + SAP_SCOPES.
    # The tenant is identified by SAP_API_BASE; Company Code selects the posting
    # org per request (our organization_id / tenant_id analogue) and is MANDATORY
    # on every accounting document.
    @property
    def SAP_AUTH_MODE(self) -> str:
        return os.environ.get("SAP_AUTH_MODE", "client_credentials").lower()

    @property
    def SAP_CLIENT_ID(self) -> str:
        return _sap_env("SAP_CLIENT_ID", "SAP_BTP_CLIENT_ID")

    @property
    def SAP_CLIENT_SECRET(self) -> str:
        return _sap_env("SAP_CLIENT_SECRET", "SAP_BTP_CLIENT_SECRET")

    @property
    def SAP_TOKEN_URL(self) -> str:
        """OAuth2 token endpoint, e.g.
        https://<subaccount>.authentication.<region>.hana.ondemand.com/oauth/token
        (BTP) or the S/4HANA tenant's /sap/bc/sec/oauth2/token."""
        return _sap_env("SAP_TOKEN_URL", "SAP_BTP_TOKEN_URL")

    @property
    def SAP_AUTHORIZE_URL(self) -> str:
        """OAuth2 authorize endpoint (authorization_code mode only)."""
        return os.environ.get("SAP_AUTHORIZE_URL", "")

    @property
    def SAP_API_BASE(self) -> str:
        """OData service base WITHOUT trailing slash, e.g.
        https://my3xxxxx.s4hana.cloud.sap/sap/opu/odata/sap"""
        return _sap_env("SAP_API_BASE", "SAP_S4HANA_API_BASE").rstrip("/")

    @property
    def SAP_REDIRECT_URI(self) -> str:
        return os.environ.get("SAP_REDIRECT_URI", "http://localhost:8000/ca/sap/oauth/callback")

    @property
    def SAP_SCOPES(self) -> str:
        return os.environ.get("SAP_SCOPES", "")

    @property
    def SAP_DEFAULT_COMPANY_CODE(self) -> str:
        """Fallback Company Code when a request omits one (e.g. '1000')."""
        return os.environ.get("SAP_DEFAULT_COMPANY_CODE", "")

    @property
    def SAP_FRONTEND_RETURN_URL(self) -> str:
        return os.environ.get("SAP_FRONTEND_RETURN_URL", "http://localhost:3000/dashboard/sap")

    @property
    def SAP_CHART_OF_ACCOUNTS(self) -> str:
        """Chart of accounts key — part of the GL-account composite key. Standard
        S/4HANA Cloud best-practice client uses 'YCOA'."""
        return os.environ.get("SAP_CHART_OF_ACCOUNTS", "YCOA")

    @property
    def SAP_LEDGER(self) -> str:
        """Ledger for trial-balance / GL reads (leading ledger is '0L')."""
        return os.environ.get("SAP_LEDGER", "0L")

    @property
    def SAP_LANGUAGE(self) -> str:
        """Language filter for text entities (e.g. GL account names)."""
        return os.environ.get("SAP_LANGUAGE", "EN")

    @property
    def SAP_SOAP_JOURNAL_ENTRY_URL(self) -> str:
        """Full endpoint URL of the SOAP A2X 'Journal Entry - Post' synchronous
        service (JournalEntryCreateRequestConfirmation_In), taken from the
        SAP_COM_0002 communication arrangement. Journal-entry / customer-invoice
        posting is unavailable until this is set (OData cannot post JEs in Public
        Edition)."""
        return os.environ.get("SAP_SOAP_JOURNAL_ENTRY_URL", "")

    @property
    def SAP_SOAP_USER(self) -> str:
        """Optional Basic-auth user for SOAP A2X calls. If unset, the OData OAuth
        bearer token is reused for SOAP (works when the comm arrangement is OAuth)."""
        return os.environ.get("SAP_SOAP_USER", "")

    @property
    def SAP_SOAP_PASSWORD(self) -> str:
        return os.environ.get("SAP_SOAP_PASSWORD", "")

    # --- Database components (parsed from DATABASE_URL) ---
    @property
    def db_components(self) -> dict:
        """Parse DATABASE_URL into individual components for libraries that need them."""
        from urllib.parse import urlparse
        url = self.DATABASE_URL
        if not url:
            return {"host": "", "dbname": "", "user": "", "password": "", "port": 5432}
        parsed = urlparse(url)
        return {
            "host": parsed.hostname or "",
            "dbname": parsed.path.lstrip("/") if parsed.path else "",
            "user": parsed.username or "",
            "password": parsed.password or "",
            "port": parsed.port or 5432,
        }

    # --- Reddit ---
    @property
    def REDDIT_CLIENT_ID(self) -> str:
        return os.environ.get("REDDIT_CLIENT_ID", "")

    @property
    def REDDIT_CLIENT_SECRET(self) -> str:
        return os.environ.get("REDDIT_CLIENT_SECRET", "")

    @property
    def REDDIT_USER_AGENT(self) -> str:
        return os.environ.get("REDDIT_USER_AGENT", "")

    # --- News API ---
    @property
    def NEWS_API_KEY(self) -> str:
        return os.environ.get("NEWS_API_KEY", "")

    # --- CORS ---
    @property
    def CORS_ORIGINS(self) -> list:
        configured = os.environ.get("CORS_ORIGINS", "")
        origins = {
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
            "http://127.0.0.1:5173",
        }
        origins.update(origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip())
        return sorted(origins)

    # --- App ---
    @property
    def APP_ENV(self) -> str:
        return os.environ.get("APP_ENV", "development")

    @property
    def DEBUG(self) -> bool:
        return self.APP_ENV == "development"

    def validate(self):
        """Validate that critical env vars are set. Call on startup."""
        required = {
            "DATABASE_URL": self.DATABASE_URL,
            "JWT_SECRET": self.JWT_SECRET,
            "OPENAI_API_KEY": self.OPENAI_API_KEY,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Missing required environment variables: {', '.join(missing)}. "
                "App may not function correctly."
            )


settings = _Settings()
