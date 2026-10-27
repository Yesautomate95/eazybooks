from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import StreamingResponse
from firebase_admin import auth
from typing import Dict
import logging
import asyncio
import uuid
import json
import time

from utils.auth_utils import verify_jwt

router = APIRouter()
logger = logging.getLogger(__name__)

class ConnectionManager:
    # ── Heartbeat / liveness tuning ────────────────────────────────────
    # The Electron client pings every HEARTBEAT_INTERVAL_S. We treat a socket
    # as dead once we've missed ~3 beats (STALE_AFTER_S) and evict it — this is
    # what catches half-open sockets that never raise WebSocketDisconnect (RC1).
    # CHECK_CACHE_FRESH_S is how long a heartbeat-reported Tally status is
    # trusted before check_tally_connection falls back to a live probe.
    HEARTBEAT_INTERVAL_S = 20
    STALE_AFTER_S = 70
    CHECK_CACHE_FRESH_S = 35

    def __init__(self):
        # Store active electron app connections
        self.electron_connections: Dict[str, WebSocket] = {}
        # Identity of the *current* socket per uid. A late WebSocketDisconnect
        # from a superseded socket must not evict the live reconnected one — so
        # disconnect() only acts when the conn_id matches (RC2).
        self._conn_ids: Dict[str, str] = {}
        # Single source of truth for connection status, read by the web app via
        # SSE push (+ poll fallback). Per uid:
        #   { bridge: bool,        # is an Electron WS attached & alive
        #     tally: bool|None,    # last-known Tally reachability via the bridge
        #     company: str|None,
        #     last_seen: float|None,  # epoch of last heartbeat/activity
        #     ts: float }             # when this record was last computed
        self._status: Dict[str, dict] = {}
        # SSE subscriber queues per uid (web-app status streams).
        self._subscribers: Dict[str, set] = {}
        # Store pending requests
        self.pending_requests: Dict[str, asyncio.Future] = {}
        # Background reaper, started lazily on first connect (needs a live loop).
        self._reaper_task = None

    async def connect(self, websocket: WebSocket, firebase_uid: str, token: str = None):
        try:
            # First try JWT verification (works for both email and Google users)
            verified = False
            if token:
                try:
                    decoded = verify_jwt(token)
                    if decoded.get("uid") == firebase_uid:
                        verified = True
                        logger.info(f"Client verified via JWT: {firebase_uid}")
                except Exception:
                    pass

            # Fall back to Firebase UID verification
            if not verified:
                try:
                    auth.get_user(firebase_uid)
                    verified = True
                    logger.info(f"Client verified via Firebase: {firebase_uid}")
                except auth.UserNotFoundError:
                    pass
                except Exception:
                    pass

            if not verified:
                logger.error(f"Failed to verify client: {firebase_uid}")
                await websocket.close(code=4003)
                raise HTTPException(status_code=401, detail="Authentication failed")

            await websocket.accept()
            conn_id = str(uuid.uuid4())
            self.electron_connections[firebase_uid] = websocket
            self._conn_ids[firebase_uid] = conn_id
            # Seed status: bridge up, Tally reachability unknown until the first
            # heartbeat. touch_seen so the reaper grace clock starts now.
            self._set_status(firebase_uid, bridge=True, touch_seen=True)
            self._ensure_reaper()
            logger.info(f"Electron client connected: {firebase_uid} (conn {conn_id[:8]})")

            # Fire-and-forget background warm-cache. Refreshes companies list
            # and grouped ledger snapshots (including GSTIN) for every
            # previously-seen company, so subsequent /suggest-ledger calls
            # have fresh data even before the user picks a company in the UI.
            # Bounded internally to avoid burning the bridge on multi-company
            # tenants.
            asyncio.create_task(self._warm_cache_on_connect(firebase_uid))

            return conn_id

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during connection: {str(e)}")
            await websocket.close(code=4000)
            raise HTTPException(status_code=500, detail="Connection failed")

    # Maximum number of companies whose ledgers we'll proactively refresh
    # on a single connect. Multi-company tenants would otherwise tie up the
    # Electron bridge for minutes on every reconnect. Companies beyond this
    # cap are still cached lazily — whenever the user actually picks one,
    # /suggest-ledger writes through to the cache on success.
    _WARM_CACHE_MAX_COMPANIES = 5

    async def _warm_cache_on_connect(self, firebase_uid: str) -> None:
        """Background sync triggered on every successful WS connect.

        Step 1: refresh companies list (so the UI's company dropdown is
                authoritative immediately).
        Step 2: refresh grouped ledgers (party + purchase + sales, with
                GSTIN) for the user's last-seen companies. This is what
                the suggestion engine reads when the bridge is offline —
                keeping it fresh means a user who reconnects briefly
                doesn't strand the cache.

        Errors are logged and swallowed — the user shouldn't see a connect
        failure because a single Tally fetch timed out.
        """
        try:
            from applicationca.services import tally_cache_store
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"warm-cache: cache store import failed for {firebase_uid}: {exc}")
            return

        # Step 1 — companies list.
        try:
            companies = await self.get_tally_companies(firebase_uid, timeout=20)
            if companies:
                try:
                    tally_cache_store.upsert_companies(user_id=firebase_uid, companies=companies)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"warm-cache: company upsert failed for {firebase_uid}: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"warm-cache: company fetch failed for {firebase_uid}: {exc}")

        # Step 2 — grouped ledgers for the most-recently-seen companies.
        try:
            cached_companies = tally_cache_store.list_cached_companies(user_id=firebase_uid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"warm-cache: list_cached_companies failed for {firebase_uid}: {exc}")
            return

        for entry in cached_companies[: self._WARM_CACHE_MAX_COMPANIES]:
            company_name = entry.get("company_name") if isinstance(entry, dict) else None
            if not company_name:
                continue
            try:
                grouped = await self.get_tally_grouped_ledgers(
                    firebase_uid, company_name, timeout=30,
                )
                if grouped:
                    tally_cache_store.upsert_grouped_ledgers(
                        user_id=firebase_uid,
                        company_name=company_name,
                        grouped=grouped,
                    )
                    logger.info(
                        f"warm-cache: refreshed grouped ledgers for "
                        f"user={firebase_uid} company={company_name}"
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"warm-cache: ledger fetch failed for "
                    f"user={firebase_uid} company={company_name}: {exc}"
                )

    def disconnect(self, firebase_uid: str, conn_id: str = None):
        # Identity guard (RC2): if a conn_id is supplied and it's not the one
        # currently registered, this is a superseded socket closing late — do
        # nothing, or we'd evict the live reconnected connection.
        if conn_id is not None and self._conn_ids.get(firebase_uid) not in (None, conn_id):
            logger.info(
                f"Ignoring stale disconnect for {firebase_uid} "
                f"(conn {str(conn_id)[:8]} superseded by {str(self._conn_ids.get(firebase_uid))[:8]})"
            )
            return

        existed = firebase_uid in self.electron_connections
        if existed:
            del self.electron_connections[firebase_uid]
        self._conn_ids.pop(firebase_uid, None)

        # Clean up any pending requests for this user
        for request_id in list(self.pending_requests.keys()):
            future = self.pending_requests[request_id]
            if not future.done():
                future.set_exception(Exception("WebSocket disconnected"))
            del self.pending_requests[request_id]

        if existed:
            # Clear stale status on disconnect (RC1/RC5) and push it out so the
            # web app flips to offline immediately rather than on the next poll.
            self._set_status(firebase_uid, bridge=False)
            logger.info(f"Electron client disconnected: {firebase_uid}")

    # ── Status, heartbeat & push plumbing ──────────────────────────────

    def _set_status(self, uid: str, *, bridge: bool = None, tally=None,
                    company=None, touch_seen: bool = False) -> dict:
        """Update the canonical status record for uid and push to subscribers."""
        st = self._status.get(uid) or {
            "bridge": False, "tally": None, "company": None,
            "last_seen": None, "ts": 0.0,
        }
        # User-visible signature before the update — so a plain heartbeat (which
        # only refreshes last_seen) doesn't spam the SSE stream / web app with
        # no-op frames. We publish only when one of these actually changes.
        before = (st.get("bridge"), st.get("tally"), st.get("company"))

        if bridge is not None:
            st["bridge"] = bridge
            if not bridge:
                # Bridge down ⇒ Tally is unreachable through it by definition.
                st["tally"] = False
        if tally is not None:
            st["tally"] = bool(tally)
        if company is not None:
            st["company"] = company
        if touch_seen:
            st["last_seen"] = time.time()
        st["ts"] = time.time()
        self._status[uid] = st

        if (st.get("bridge"), st.get("tally"), st.get("company")) != before:
            self._publish(uid)
        return st

    def get_status(self, uid: str) -> dict:
        """Public status snapshot. `connected` = both legs up (RC4)."""
        st = self._status.get(uid)
        if not st:
            return {
                "bridge": False, "tally": False, "company": None,
                "last_seen": None, "ts": time.time(), "connected": False,
            }
        out = dict(st)
        out["connected"] = bool(st.get("bridge") and st.get("tally"))
        return out

    def on_heartbeat(self, uid: str, tally=None, company=None):
        """Record a heartbeat from the Electron client (RC1)."""
        self._set_status(uid, bridge=True, tally=tally, company=company, touch_seen=True)

    def _publish(self, uid: str):
        subs = self._subscribers.get(uid)
        if not subs:
            return
        payload = self.get_status(uid)
        for q in list(subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer — drop the update; the next one (or the SSE
                # keepalive re-read) will resync it.
                pass
            except Exception:
                pass

    def subscribe(self, uid: str) -> "asyncio.Queue":
        q = asyncio.Queue(maxsize=16)
        self._subscribers.setdefault(uid, set()).add(q)
        # Prime immediately with current status so a fresh subscriber doesn't
        # sit blank until the next transition.
        try:
            q.put_nowait(self.get_status(uid))
        except Exception:
            pass
        return q

    def unsubscribe(self, uid: str, q):
        subs = self._subscribers.get(uid)
        if subs:
            subs.discard(q)
            if not subs:
                self._subscribers.pop(uid, None)

    def _ensure_reaper(self):
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def _reaper_loop(self):
        """Evict sockets whose heartbeat has gone stale (half-open detection)."""
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL_S)
                now = time.time()
                for uid in list(self.electron_connections.keys()):
                    st = self._status.get(uid) or {}
                    last = st.get("last_seen")
                    if last is None:
                        continue
                    if now - last > self.STALE_AFTER_S:
                        logger.warning(
                            f"Heartbeat stale for {uid} ({now - last:.0f}s > "
                            f"{self.STALE_AFTER_S}s) — evicting half-open socket"
                        )
                        ws = self.electron_connections.get(uid)
                        conn_id = self._conn_ids.get(uid)
                        # Drop bookkeeping first so a slow close can't race a
                        # reconnect, then best-effort close the dead socket.
                        self.disconnect(uid, conn_id)
                        if ws is not None:
                            try:
                                await ws.close(code=4001)
                            except Exception:
                                pass
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.error(f"Reaper loop error: {e}")


    async def send_message(self, firebase_uid: str, message: dict):
        if firebase_uid in self.electron_connections:
            await self.electron_connections[firebase_uid].send_json(message)
            logger.debug(f"Message sent to client {firebase_uid}: {message}")

    async def get_tally_ledgers(self, firebase_uid: str, company: str = None, timeout: int = 30) -> dict:
        """Send a get-ledgers request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")
        
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        
        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        
        try:
            # Send the get-ledgers request with request ID and company
            message = {
                "type": "get-ledgers",
                "request_id": request_id
            }
            if company:
                message["company"] = company
                
            await self.send_message(firebase_uid, message)
            
            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received ledger response from client {firebase_uid}")
            
            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])
            
            return response.get("data", [])
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for ledgers from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for ledgers")
            
        except Exception as e:
            logger.error(f"Error getting ledgers from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to get ledgers from client")
            
        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def get_tally_grouped_ledgers(self, firebase_uid: str, company: str = None, timeout: int = 60) -> dict:
        """Send a get-grouped-ledgers request to the Electron client and wait for response.

        Returns ledgers organized into party, purchase, and sales categories.
        Uses a longer timeout (60s) because it fetches from 8 Tally groups in parallel.
        """
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        request_id = str(uuid.uuid4())
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            message = {
                "type": "get-grouped-ledgers",
                "request_id": request_id
            }
            if company:
                message["company"] = company

            await self.send_message(firebase_uid, message)
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received grouped ledger response from client {firebase_uid}")

            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            return response.get("data", {"party": {}, "purchase": {}, "sales": {}})

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for grouped ledgers from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for grouped ledgers")

        except Exception as e:
            logger.error(f"Error getting grouped ledgers from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to get grouped ledgers from client")

        finally:
            self.pending_requests.pop(request_id, None)

    async def get_tally_ledger_transactions(
        self,
        firebase_uid: str,
        company: str,
        ledger_name: str,
        from_date: str,
        to_date: str,
        timeout: int = 90,
    ) -> dict:
        """
        Fetch voucher transactions for a specific bank ledger from Tally.

        Sends message type 'get-ledger-transactions' to the Electron app.
        The Electron app must handle this type, query Tally, and respond with
        type 'ledger-transactions-response' containing:
          {
            request_id: str,
            type: "ledger-transactions-response",
            data: {
              opening_balance: number,
              closing_balance: number,
              transactions: [
                { date, narration, voucher_type, voucher_number, guid, amount }
              ]
            }
          }
        """
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        request_id = str(uuid.uuid4())
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            message = {
                "type": "get-ledger-transactions",
                "request_id": request_id,
                "company": company,
                "ledger_name": ledger_name,
                "from_date": from_date,
                "to_date": to_date,
            }
            await self.send_message(firebase_uid, message)
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received ledger-transactions response from client {firebase_uid}")

            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            return response.get("data", {"transactions": [], "opening_balance": None, "closing_balance": None})

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for ledger transactions from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for ledger transactions from Tally")

        except Exception as e:
            logger.error(f"Error getting ledger transactions from client {firebase_uid}: {str(e)}")
            raise

        finally:
            self.pending_requests.pop(request_id, None)

    async def handle_client_response(self, response_data: dict):
        """Handle response from client"""
        request_id = response_data.get("request_id")
        response_type = response_data.get("type")
        logger.debug(f"Handling client response of type {response_type} with request ID: {request_id}")

        if request_id and request_id in self.pending_requests:
            logger.debug(f"Found pending request for ID: {request_id}")
            future = self.pending_requests[request_id]
            if not future.done():
                logger.debug(f"Setting result for request {request_id}")
                future.set_result(response_data)
                logger.debug(f"Result set successfully for {request_id}")
            else:
                logger.warning(f"Future already done for request {request_id}")
        else:
            logger.warning(f"No pending request found for ID: {request_id}")
            logger.debug(f"Current pending requests: {list(self.pending_requests.keys())}")

    async def get_tally_companies(self, firebase_uid: str, timeout: int = 30) -> dict:
        """Send a get-companies request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")
        
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        
        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        
        try:
            # Send the get-companies request with request ID
            await self.send_message(firebase_uid, {
                "type": "get-companies",
                "request_id": request_id
            })
            
            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received companies response from client {firebase_uid}")
            
            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])
            
            return response.get("data", [])
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for companies from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for companies")
            
        except Exception as e:
            logger.error(f"Error getting companies from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to get companies from client")
            
        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def get_tally_inventory(self, firebase_uid: str, company: str = None, timeout: int = 30) -> dict:
        """Send a get-inventory request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")
        
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        
        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        
        try:
            # Send the get-inventory request with request ID and company
            message = {
                "type": "get-inventory",
                "request_id": request_id
            }
            if company:
                message["company"] = company
                
            await self.send_message(firebase_uid, message)
            
            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received inventory response from client {firebase_uid}")
            
            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])
            
            return response.get("data", [])
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for inventory from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for inventory")
            
        except Exception as e:
            logger.error(f"Error getting inventory from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to get inventory from client")
            
        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def get_tally_godowns(self, firebase_uid: str, company: str = None, timeout: int = 30) -> dict:
        """Send a get-godowns request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            # Send the get-godowns request with request ID and company
            message = {
                "type": "get-godowns",
                "request_id": request_id
            }
            if company:
                message["company"] = company
                
            await self.send_message(firebase_uid, message)

            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received godowns response from client {firebase_uid}")

            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            return response.get("data", [])

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for godowns from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for godowns")

        except Exception as e:
            logger.error(f"Error getting godowns from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to get godowns from client")

        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def check_tally_connection(self, firebase_uid: str, timeout: int = 30) -> bool:
        """Check if Tally is connected through the Electron client.

        Prefers the heartbeat-reported status when fresh — this avoids a false
        "offline" when the bridge is alive but busy (warm-cache, a 12s ledger
        fetch) and would otherwise miss the round-trip window (RC3/RC4). Falls
        back to a live probe only when no recent heartbeat is available.
        """
        if firebase_uid not in self.electron_connections:
            return False

        st = self._status.get(firebase_uid) or {}
        last = st.get("last_seen")
        if (
            last is not None
            and (time.time() - last) <= self.CHECK_CACHE_FRESH_S
            and st.get("tally") is not None
        ):
            return bool(st.get("tally"))

        # Stale / no heartbeat yet → live round-trip probe.
        request_id = str(uuid.uuid4())
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            await self.send_message(firebase_uid, {
                "type": "check-connection",
                "request_id": request_id
            })

            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received connection check response from client {firebase_uid}")

            if "error" in response:
                logger.error(f"Error in connection check: {response['error']}")
                self._set_status(firebase_uid, tally=False, touch_seen=True)
                return False

            connected = bool(response.get("connected", False))
            self._set_status(firebase_uid, tally=connected, touch_seen=True)
            return connected

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for connection check from client {firebase_uid}")
            return False

        except Exception as e:
            logger.error(f"Error checking connection: {str(e)}")
            return False

        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def get_tally_groups(self, firebase_uid: str, company: str = None, timeout: int = 60) -> dict:
        """Send a get-groups request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            # Send the get-groups request with request ID and company
            message = {
                "type": "get-groups",
                "request_id": request_id
            }
            if company:
                message["company"] = company
                
            await self.send_message(firebase_uid, message)

            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received groups response from client {firebase_uid}")

            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            return response.get("data", [])

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for groups from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for groups")

        except Exception as e:
            logger.error(f"Error getting groups from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to get groups from client")

        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def create_tally_ledger(self, firebase_uid: str, ledger_data: dict, timeout: int = 60) -> dict:
        """Send a create-ledger request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            # Ensure all required fields are present with defaults if not provided
            ledger_data = {
                "name": ledger_data.get("name", ""),
                "parent_group": ledger_data.get("parent_group", ""),
                "company": ledger_data.get("company", ""),
                "opening_balance": ledger_data.get("opening_balance", "0"),
                "address": ledger_data.get("address", ""),
                "country": ledger_data.get("country", ""),
                "state": ledger_data.get("state", ""),
                "mobile": ledger_data.get("mobile", ""),
                "gstin": ledger_data.get("gstin", "")
            }

            # Send the create-ledger request with request ID and ledger data
            await self.send_message(firebase_uid, {
                "type": "create-ledger",
                "request_id": request_id,
                "data": ledger_data
            })

            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received create ledger response from client {firebase_uid}")

            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            return response.get("data", {})

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for ledger creation from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for ledger creation")

        except Exception as e:
            logger.error(f"Error creating ledger from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to create ledger in client")

        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def trigger_tally_sync(
        self,
        firebase_uid: str,
        company: str,
        mode: str = "incremental",
        timeout: int = 15,
    ) -> dict:
        """Ask the desktop bridge to kick off a tally-database-loader run for one
        company. The bridge starts the loader (which writes to our DB and updates
        tally_sync_state itself) and acks with 'run-tally-sync-response' that it
        STARTED — we deliberately do NOT wait for the (possibly multi-minute) sync
        to finish; the web app polls /tally/analytics/sync-status for progress.

        Sends: {type:'run-tally-sync', request_id, company, mode}
        Expects ack: {type:'run-tally-sync-response', request_id, data:{started, ...}}
        """
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Desktop app not connected")

        request_id = str(uuid.uuid4())
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        try:
            await self.send_message(firebase_uid, {
                "type": "run-tally-sync",
                "request_id": request_id,
                "company": company,
                "mode": mode if mode in ("incremental", "full") else "incremental",
            })
            response = await asyncio.wait_for(future, timeout)
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])
            return response.get("data", {"started": True})
        except asyncio.TimeoutError:
            # The desktop bridge may be busy/offline. Surface as a soft failure —
            # the caller treats this as "couldn't start", not a sync error.
            logger.warning(f"Timeout awaiting run-tally-sync ack from {firebase_uid}")
            raise HTTPException(status_code=504, detail="Desktop bridge did not acknowledge sync start")
        finally:
            self.pending_requests.pop(request_id, None)

    async def push_to_tally(
        self,
        firebase_uid: str,
        company: str,
        xml: str,
        timeout: int = 180,
    ) -> dict:
        """Push pre-built voucher XML to Tally through the Electron desktop bridge.

        Mirrors the /push-to-tally controller round-trip so backend services (e.g.
        bank-reconciliation adjustments) can post vouchers without going through an
        HTTP hop. Sends message type 'push-to-tally'; the Electron app forwards the
        XML to the local Tally instance and replies with 'push-to-tally-response':
          { request_id, type, error?, data: { success, created, altered, errors,
            exception, lastvchid, raw } }

        Returns the inner `data` dict. Raises HTTPException on no client / timeout /
        transport error. The CALLER must inspect data.exception / data.error to
        decide whether Tally actually accepted the voucher — a delivered message is
        not the same as a created voucher.
        """
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Desktop app not connected")

        request_id = str(uuid.uuid4())
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            await self.send_message(firebase_uid, {
                "type": "push-to-tally",
                "request_id": request_id,
                "company": company,
                "data": xml,
            })

            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received push-to-tally response from client {firebase_uid}")

            # Transport-level error (Electron couldn't reach Tally at all).
            if response.get("error"):
                raise HTTPException(status_code=502, detail=response["error"])

            return response.get("data", {})

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for push-to-tally from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for Tally to accept the voucher")

        except HTTPException:
            raise

        except Exception as e:
            logger.error(f"Error pushing to Tally for client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to push voucher to Tally")

        finally:
            self.pending_requests.pop(request_id, None)

    async def create_tally_inventory(self, firebase_uid: str, inventory_data: dict, timeout: int = 60) -> dict:
        """Send a create-inventory request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            # Ensure all required fields are present with defaults if not provided
            inventory_data = {
                "name": inventory_data.get("name", ""),
                "parent": inventory_data.get("parent", ""),
                "category": inventory_data.get("category", ""),
                "baseUnits": inventory_data.get("baseUnits", ""),
                "openingBalance": inventory_data.get("openingBalance", "0"),
                "openingValue": inventory_data.get("openingValue", "0"),
                "description": inventory_data.get("description", ""),
                "godown": inventory_data.get("godown", "")
            }

            # Send the create-inventory request with request ID and inventory data
            await self.send_message(firebase_uid, {
                "type": "create-inventory",
                "request_id": request_id,
                "data": inventory_data
            })

            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received create inventory response from client {firebase_uid}")

            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            return response.get("data", {})

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for inventory creation from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for inventory creation")

        except Exception as e:
            logger.error(f"Error creating inventory from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to create inventory in client")

        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def edit_tally_ledger(self, firebase_uid: str, ledger_data: dict, timeout: int = 60) -> dict:
        """Send an edit-ledger request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            # Log incoming data in detail
            logger.info("Received ledger data for editing:")
            logger.info(f"Original data: {json.dumps(ledger_data, indent=2)}")
            
            # Validate required fields
            if 'current_name' not in ledger_data:
                raise HTTPException(
                    status_code=400, 
                    detail=f"current_name is required for editing a ledger. Received data: {json.dumps(ledger_data, indent=2)}"
                )
            
            if 'name' not in ledger_data and 'new_name' not in ledger_data:
                raise HTTPException(
                    status_code=400,
                    detail="Either name or new_name is required for editing a ledger"
                )
            
            # Prepare the ledger data
            processed_data = {
                "current_name": ledger_data["current_name"],  # Current name in Tally
                "new_name": ledger_data.get("new_name") or ledger_data.get("name"),  # New name to change to
                "parent_group": ledger_data.get("parent_group", ""),
                "company": ledger_data.get("company", ""),
                "opening_balance": ledger_data.get("opening_balance", "0"),
                "address": ledger_data.get("address", ""),
                "country": ledger_data.get("country", ""),
                "state": ledger_data.get("state", ""),
                "mobile": ledger_data.get("mobile", ""),
                "gstin": ledger_data.get("gstin", ""),
            }
            
            logger.info(f"Processed ledger data: {json.dumps(processed_data, indent=2)}")

            # Send the edit-ledger request with request ID and ledger data
            await self.send_message(firebase_uid, {
                "type": "edit-ledger",
                "request_id": request_id,
                "data": processed_data
            })

            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received edit ledger response from client {firebase_uid}")

            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            return response.get("data", {})

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for ledger edit from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for ledger edit")

        except HTTPException as e:
            logger.error(f"Error editing ledger from client {firebase_uid}: {e.status_code}: {e.detail}")
            raise e

        except Exception as e:
            logger.error(f"Error editing ledger from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to edit ledger in client: {str(e)}")

        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def create_tally_group(self, firebase_uid: str, group_data: dict, timeout: int = 60) -> dict:
        """Send a create-group request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            # Ensure all required fields are present with defaults if not provided
            group_data = {
                "name": group_data.get("name", ""),
                "parent_group": group_data.get("parent_group", ""),
                "company": group_data.get("company", ""),
                "nature": group_data.get("nature", ""),
                "under": group_data.get("under", "")
            }

            # Send the create-group request with request ID and group data
            await self.send_message(firebase_uid, {
                "type": "create-group",
                "request_id": request_id,
                "data": group_data
            })

            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received create group response from client {firebase_uid}")

            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            return response.get("data", {})

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for group creation from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for group creation")

        except Exception as e:
            logger.error(f"Error creating group from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to create group in client")

        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    async def get_tally_ledger_details(self, firebase_uid: str, ledger_data: dict, timeout: int = 60) -> dict:
        """Send a get-ledger-details request to the Electron client and wait for response"""
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            # Log the request
            logger.info(f"Fetching ledger details for: {ledger_data}")

            # Send the get-ledger-details request with request ID and ledger data
            await self.send_message(firebase_uid, {
                "type": "get-ledger-details",
                "request_id": request_id,
                "data": {
                    "ledger_name": ledger_data.get("ledger_name", ""),
                    "company": ledger_data.get("company", "")
                }
            })

            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout)
            logger.info(f"Received ledger details response from client {firebase_uid}")

            # Extract and validate the data
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])

            # Get the data from the response
            ledger_details = response.get("data", {})
            
            # Log the raw response
            logger.info(f"Raw ledger details from Tally: {ledger_details}")

            # Ensure we have all required fields with proper defaults
            processed_details = {
                "name": ledger_details.get("name", ledger_data.get("ledger_name", "")),
                "tally_name": ledger_details.get("name", ledger_data.get("ledger_name", "")),  # Exact name from Tally
                "parent_group": ledger_details.get("parent_group", ""),
                "opening_balance": ledger_details.get("opening_balance", "0"),
                "address": ledger_details.get("address", ""),
                "country": ledger_details.get("country", ""),
                "state": ledger_details.get("state", ""),
                "mobile": ledger_details.get("mobile", ""),
                "gstin": ledger_details.get("gstin", "")
            }

            # Log the processed response
            logger.info(f"Processed ledger details: {processed_details}")

            return {
                "success": True,
                "ledger": processed_details
            }

        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for ledger details from client {firebase_uid}")
            raise HTTPException(status_code=504, detail="Timeout waiting for ledger details")

        except Exception as e:
            logger.error(f"Error getting ledger details from client {firebase_uid}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to get ledger details from client")

        finally:
            # Clean up the pending request
            self.pending_requests.pop(request_id, None)

    # ==================================================================
    # QuickBooks Desktop bridge
    # ==================================================================
    # Generic request/response over the same Electron WebSocket. Each helper
    # sends a typed message ("qb-*"), the Electron client speaks QBXML to QB
    # Desktop on the user's machine, and replies with "*-response".

    async def _qb_request(
        self,
        firebase_uid: str,
        msg_type: str,
        payload: dict = None,
        timeout: int = 60,
    ) -> dict:
        """Generic request helper for QB-bridge messages.

        Sends `{type: msg_type, request_id, **payload}`, awaits a response of
        type `<msg_type>-response`, and returns its `data` field.
        """
        if firebase_uid not in self.electron_connections:
            raise HTTPException(status_code=404, detail="Client not connected")

        request_id = str(uuid.uuid4())
        future = asyncio.Future()
        self.pending_requests[request_id] = future

        try:
            message = {"type": msg_type, "request_id": request_id}
            if payload:
                message.update(payload)
            await self.send_message(firebase_uid, message)

            response = await asyncio.wait_for(future, timeout)
            if "error" in response:
                raise HTTPException(status_code=500, detail=response["error"])
            return response.get("data", {})

        except asyncio.TimeoutError:
            logger.error(f"Timeout on {msg_type} from client {firebase_uid}")
            raise HTTPException(status_code=504, detail=f"Timeout waiting for {msg_type}")
        finally:
            self.pending_requests.pop(request_id, None)

    async def check_qb_connection(self, firebase_uid: str, timeout: int = 30) -> bool:
        if firebase_uid not in self.electron_connections:
            return False
        try:
            data = await self._qb_request(firebase_uid, "qb-check-connection", timeout=timeout)
            return bool(data.get("connected", False)) if isinstance(data, dict) else bool(data)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"QB connection check error: {exc}")
            return False

    async def get_qb_companies(self, firebase_uid: str, timeout: int = 30) -> list:
        return await self._qb_request(firebase_uid, "qb-get-companies", timeout=timeout) or []

    async def get_qb_accounts(self, firebase_uid: str, company: str = None, timeout: int = 60) -> list:
        payload = {"company": company} if company else None
        return await self._qb_request(firebase_uid, "qb-get-accounts", payload, timeout) or []

    async def get_qb_customers(self, firebase_uid: str, company: str = None, timeout: int = 60) -> list:
        payload = {"company": company} if company else None
        return await self._qb_request(firebase_uid, "qb-get-customers", payload, timeout) or []

    async def get_qb_vendors(self, firebase_uid: str, company: str = None, timeout: int = 60) -> list:
        payload = {"company": company} if company else None
        return await self._qb_request(firebase_uid, "qb-get-vendors", payload, timeout) or []

    async def get_qb_items(self, firebase_uid: str, company: str = None, timeout: int = 60) -> list:
        payload = {"company": company} if company else None
        return await self._qb_request(firebase_uid, "qb-get-items", payload, timeout) or []

    async def create_qb_customer(self, firebase_uid: str, data: dict, timeout: int = 60) -> dict:
        return await self._qb_request(firebase_uid, "qb-create-customer", {"data": data}, timeout)

    async def create_qb_vendor(self, firebase_uid: str, data: dict, timeout: int = 60) -> dict:
        return await self._qb_request(firebase_uid, "qb-create-vendor", {"data": data}, timeout)

    async def create_qb_account(self, firebase_uid: str, data: dict, timeout: int = 60) -> dict:
        return await self._qb_request(firebase_uid, "qb-create-account", {"data": data}, timeout)

    async def push_qb_invoice(self, firebase_uid: str, qbxml: str, company: str = None, timeout: int = 90) -> dict:
        payload = {"qbxml": qbxml}
        if company:
            payload["company"] = company
        return await self._qb_request(firebase_uid, "qb-push-invoice", payload, timeout)

    async def push_qb_bill(self, firebase_uid: str, qbxml: str, company: str = None, timeout: int = 90) -> dict:
        payload = {"qbxml": qbxml}
        if company:
            payload["company"] = company
        return await self._qb_request(firebase_uid, "qb-push-bill", payload, timeout)


# Create global connection manager
manager = ConnectionManager()

@router.websocket("/ws/electron/{firebase_uid}")
async def websocket_endpoint(websocket: WebSocket, firebase_uid: str, token: str = None):
    """WebSocket endpoint for Electron app"""
    conn_id = None
    try:
        conn_id = await manager.connect(websocket, firebase_uid, token=token)

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")
                logger.debug(f"Received from client {firebase_uid}: {msg_type}")

                if msg_type == "ping":
                    # Heartbeat (RC1). Carries the bridge's latest view of Tally
                    # reachability so the web app's status stays fresh without a
                    # round-trip probe. Reply pong so the client can detect a
                    # half-open socket from its end too.
                    manager.on_heartbeat(
                        firebase_uid,
                        tally=data.get("tally_connected"),
                        company=data.get("company"),
                    )
                    try:
                        await websocket.send_json({"type": "pong", "ts": data.get("ts")})
                    except Exception:
                        pass

                elif msg_type == "auth":
                    # Initial handshake — the socket is already verified at
                    # accept() time; just mark it seen so the reaper clock and
                    # the pushed status reflect a live bridge right away.
                    manager.on_heartbeat(firebase_uid)

                # Handle responses to pending requests
                elif msg_type in ["ledgers-response", "companies-response", "connection-response",
                                      "inventory-response", "groups-response", "create-ledger-response",
                                      "create-group-response", "godowns-response", "get-ledger-details-response",
                                      "edit-ledger-response", "push-to-tally-response",
                                      "grouped-ledgers-response", "ledger-transactions-response",
                                      # QuickBooks Desktop bridge responses
                                      "qb-check-connection-response", "qb-get-companies-response",
                                      "qb-get-accounts-response", "qb-get-customers-response",
                                      "qb-get-vendors-response", "qb-get-items-response",
                                      "qb-create-customer-response", "qb-create-vendor-response",
                                      "qb-create-account-response", "qb-push-invoice-response",
                                      "qb-push-bill-response",
                                      # Tally analytics background sync trigger ack
                                      "run-tally-sync-response"]:
                    # Any inbound traffic also counts as liveness.
                    manager.on_heartbeat(firebase_uid)
                    await manager.handle_client_response(data)
                    logger.debug(f"Finished processing {msg_type}")
                else:
                    logger.warning(f"Unhandled message type: {msg_type}")

        except WebSocketDisconnect as disconnect:
            logger.warning(
                "Electron websocket disconnected: uid=%s code=%s",
                firebase_uid,
                disconnect.code,
            )
            manager.disconnect(firebase_uid, conn_id)

    except Exception as e:
        logger.exception("Error in WebSocket connection for uid=%s: %s", firebase_uid, e)
        manager.disconnect(firebase_uid, conn_id)
        try:
            await websocket.close(code=4000)
        except Exception:
            pass


@router.get("/tally/status-stream")
async def tally_status_stream(token: str = Query(None), uid: str = Query(None)):
    """Server-Sent Events stream of Tally bridge status for the web app (RC3).

    The browser can't set Authorization headers on EventSource, so the JWT is
    passed as a query param (same approach as the Electron WS). Emits the
    current status immediately, then on every transition. A periodic comment
    keeps the stream alive through idle-killing proxies.
    """
    decoded = verify_jwt(token) if token else None
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_uid = decoded.get("uid")
    if not user_uid or (uid and uid != user_uid):
        raise HTTPException(status_code=403, detail="UID mismatch")

    q = manager.subscribe(user_uid)

    async def event_gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"event: status\ndata: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive comment — keeps proxies from idling the stream.
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            manager.unsubscribe(user_uid, q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx/proxy buffering
        },
    )

# Simple health check endpoint
@router.get("/ws/health")
async def websocket_health():
    return {
        "status": "healthy",
        "active_connections": len(manager.electron_connections)
    }


# @router.websocket("/ws/electron/{firebase_uid}")
# async def websocket_endpoint(websocket: WebSocket, firebase_uid: str):
#     """WebSocket endpoint for Electron app"""
#     try:
#         await manager.connect(websocket, firebase_uid)

#         try:
#             while True:
#                 # Receive and log messages from Electron
#                 data = await websocket.receive_json()
#                 logger.info(f"Received from client {firebase_uid}: {data}")

#                 # Echo the message back for testing
#                 await manager.send_message(firebase_uid, {
#                     "type": "echo",
#                     "data": data,
#                     "firebase_uid": firebase_uid
#                 })

#         except WebSocketDisconnect:
#             manager.disconnect(firebase_uid)

#     except Exception as e:
#         logger.error(f"Error in WebSocket connection: {str(e)}")
#         await websocket.close(code=4000)




