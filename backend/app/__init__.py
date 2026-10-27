import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from finadvisor.controller.chat_controller import router as chat_router
from finadvisor.controller.profile_controller import router as profile_router
from finadvisor.controller.kite_login_controller import router as kite_router
from finadvisor.controller.auth_controller import router as auth_router
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

from applicationca.contoller.auth_controller import router as ca_auth_router
from applicationca.contoller.file_controller import router as ca_file_router
from applicationca.contoller.chat_controller import router as ca_chat_router
from applicationca.contoller.vector import router as ca_vector_router
from applicationca.contoller.database_controller import router as ca_database_router
from applicationca.contoller.tally_controller import router as ca_tally_router
from applicationca.contoller.qb_controller import router as ca_qb_router
from applicationca.contoller.qbo_oauth_controller import router as ca_qbo_oauth_router
from applicationca.services.qbo_connector import QBOReconnectRequired
from applicationca.contoller.zoho_controller import router as ca_zoho_router
from applicationca.contoller.zoho_oauth_controller import router as ca_zoho_oauth_router
from applicationca.contoller.xero_controller import router as ca_xero_router
from applicationca.contoller.xero_oauth_controller import router as ca_xero_oauth_router
from applicationca.contoller.sap_controller import router as ca_sap_router
from applicationca.contoller.sap_oauth_controller import router as ca_sap_oauth_router
from applicationca.contoller.websocket_controller import router as ca_websocket_router
from applicationca.contoller.mis_controller import router as ca_mis_router
from applicationca.contoller.desktop_app_controller import router as ca_desktop_app_router
from applicationca.contoller.bank_recon_controller import router as ca_bank_recon_router
from applicationca.contoller.qb_bank_recon_controller import router as ca_qb_bank_recon_router
from applicationca.contoller.zoho_bank_recon_controller import router as ca_zoho_bank_recon_router
from applicationca.contoller.xero_bank_recon_controller import router as ca_xero_bank_recon_router
from applicationca.contoller.dashboard_controller import router as ca_dashboard_router
from applicationca.contoller.tally_analytics_controller import router as ca_tally_analytics_router
from applicationca.rbac import router as ca_rbac_router
from applicationca.rbac.bootstrap import bootstrap_rbac
from applicationca.database.run_all_migrations import run_all as run_all_migrations


class WebSocketCORSBypass:
    """ASGI middleware that strips Origin from WebSocket upgrades.

    Electron apps send Origin: null or file:// which CORSMiddleware rejects.
    WebSocket security is handled by token-based auth in the endpoint itself,
    so we remove the Origin header to let the request pass through CORS.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            # Remove origin header so CORSMiddleware won't reject it
            headers = [(k, v) for k, v in scope.get("headers", []) if k != b"origin"]
            scope["headers"] = headers
        await self.app(scope, receive, send)


def create_app():
    from config.settings import settings

    app = FastAPI(title="FastAPI App", description="A modular FastAPI application")

    # Health check endpoint for Railway
    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    # Global handler for unhandled httpx errors (e.g. QBO OAuth token expired).
    # Without this, httpx.HTTPStatusError propagates as a bare 500 with no CORS
    # headers, causing the browser to report a CORS error instead of the real issue.
    @app.exception_handler(httpx.HTTPStatusError)
    async def httpx_status_error_handler(request: Request, exc: httpx.HTTPStatusError):
        status = exc.response.status_code
        logger.error(f"Upstream HTTP error {status} for {request.url}: {exc}")
        if status == 401:
            return JSONResponse(
                status_code=401,
                content={"detail": "QuickBooks authentication expired — please reconnect your QuickBooks account."},
            )
        return JSONResponse(
            status_code=502,
            content={"detail": f"QuickBooks API error ({status}). Please try again or reconnect your account."},
        )

    # QBO refresh token expired/revoked (Intuit invalid_grant). Registered
    # globally (not just in qb_controller) so every endpoint that resolves a
    # QBO access token gets a clean "please reconnect" response instead of a
    # bare 500, even ones that don't explicitly catch RuntimeError.
    @app.exception_handler(QBOReconnectRequired)
    async def qbo_reconnect_required_handler(request: Request, exc: QBOReconnectRequired):
        logger.warning(f"QBO reconnect required for {request.url}: {exc}")
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled API error for %s: %s", request.url, exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error. Check the backend logs for details."},
        )

    # Configure CORS for HTTP requests
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Bypass CORS for WebSocket (auth is token-based at the endpoint level)
    app.add_middleware(WebSocketCORSBypass)

    # Register Routers
    app.include_router(chat_router, prefix="/chat", tags=["Chat"])
    app.include_router(profile_router)
    app.include_router(kite_router)
    app.include_router(auth_router)

    app.include_router(ca_auth_router, prefix="/ca")
    app.include_router(ca_file_router, prefix="/ca")
    app.include_router(ca_vector_router, prefix="/ca")
    app.include_router(ca_chat_router, prefix="/ca")
    app.include_router(ca_database_router, prefix="/ca")
    app.include_router(ca_tally_router, prefix="/ca")
    app.include_router(ca_qb_router, prefix="/ca")
    app.include_router(ca_qbo_oauth_router, prefix="/ca")
    app.include_router(ca_zoho_router, prefix="/ca")
    app.include_router(ca_zoho_oauth_router, prefix="/ca")
    app.include_router(ca_xero_router, prefix="/ca")
    app.include_router(ca_xero_oauth_router, prefix="/ca")
    app.include_router(ca_sap_router, prefix="/ca")
    app.include_router(ca_sap_oauth_router, prefix="/ca")
    app.include_router(ca_websocket_router, prefix="/ca")
    app.include_router(ca_mis_router, prefix="/ca")
    app.include_router(ca_desktop_app_router, prefix="/ca", tags=["Desktop App"])
    app.include_router(ca_bank_recon_router, prefix="/ca", tags=["Bank Reconciliation"])
    app.include_router(ca_qb_bank_recon_router, prefix="/ca", tags=["QuickBooks Bank Reconciliation"])
    app.include_router(ca_zoho_bank_recon_router, prefix="/ca", tags=["Zoho Bank Reconciliation"])
    app.include_router(ca_xero_bank_recon_router, prefix="/ca", tags=["Xero Bank Reconciliation"])
    app.include_router(ca_dashboard_router, prefix="/ca")
    app.include_router(ca_tally_analytics_router, prefix="/ca")
    app.include_router(ca_rbac_router, prefix="/ca")

    # RBAC bootstrap: applies migration if needed, syncs permission registry
    # to DB, ensures super_admin role exists with all permissions, and promotes
    # SUPER_ADMIN_EMAILS / the first user as the platform's bootstrap admin.
    # Safe to call on every startup (all steps are idempotent).
    @app.on_event("startup")
    def _rbac_startup() -> None:
        try:
            if not run_all_migrations():
                logger.error("database migrations did not complete successfully")
        except Exception:
            logger.exception("database migrations failed during startup")
        bootstrap_rbac()

    return app
