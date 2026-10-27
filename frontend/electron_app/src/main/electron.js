const { app, BrowserWindow, ipcMain, shell, protocol } = require('electron');
const Store = require('electron-store');
const path = require('path');
const axios = require('axios');
const WebSocketService = require('../services/websocket_service');
const os = require('os');
const { auth, isInitialized: isFirebaseInitialized } = require('../services/firebase/firebase-service');
const TallyLoaderService = require('../services/tally_loader_service');
const { autoUpdater } = require('electron-updater');
// Load .env only in packaged builds — the shipped .env holds prod URLs (Railway etc).
// In dev, ignore it so npm start doesn't accidentally point the local Electron app
// at the production backend; .env.development takes precedence if present.
if (app.isPackaged) {
    require('dotenv').config({ path: path.join(__dirname, '../../.env') });
} else {
    const devEnvPath = path.join(__dirname, '../../.env.development');
    require('dotenv').config({ path: devEnvPath });
}

// Use 127.0.0.1, not 'localhost' — on systems where 'localhost' resolves to the
// IPv6 loopback (::1) first, axios connects there before falling back to IPv4,
// and Tally's XML server (IPv4-only) refuses it, producing a false "not reachable".
const TALLY_URL = 'http://127.0.0.1:9000';

// Register custom protocol - must include app path in dev mode
if (process.defaultApp) {
    if (process.argv.length >= 2) {
        app.setAsDefaultProtocolClient('eazybooks', process.execPath, [path.resolve(process.argv[1])]);
    }
} else {
    app.setAsDefaultProtocolClient('eazybooks');
}

// Add this at the very top of the file, after the imports
console.log('\n=== EazyBooks App Starting ===');
console.log('App Version:', app.getVersion());
console.log('Platform:', process.platform);
console.log('Node Version:', process.version);
console.log('============================\n');

// Initialize electron-store
const store = new Store();

// URL defaults — dev uses local backend, packaged build uses prod
const PROD_API_URL = 'https://eazybooks.ai';
const PROD_AUTH_URL = 'https://eazybooks.ai/login';
const DEV_API_URL = 'http://127.0.0.1:8000';
const DEV_AUTH_URL = 'http://localhost:3000/login';

const isPackaged = app.isPackaged;
const DEFAULT_API_URL = isPackaged ? PROD_API_URL : DEV_API_URL;
const DEFAULT_AUTH_URL = isPackaged ? PROD_AUTH_URL : DEV_AUTH_URL;

// Create a store for environment variables
const envStore = new Store({
    name: 'env-config',
    defaults: {
        REACT_APP_API_URL: DEFAULT_API_URL,
        GENERATE_SOURCEMAP: 'false',
        SKIP_PREFLIGHT_CHECK: 'true',
        AUTH_URL: DEFAULT_AUTH_URL
    }
});

// In packaged builds, force the cached value to match the shipped prod URL.
// This prevents stale localhost values (cached during prior dev runs on the same
// machine) from leaking into a production install.
if (isPackaged) {
    if (process.env.REACT_APP_API_URL) envStore.set('REACT_APP_API_URL', process.env.REACT_APP_API_URL);
    else envStore.set('REACT_APP_API_URL', PROD_API_URL);

    if (process.env.AUTH_URL) envStore.set('AUTH_URL', process.env.AUTH_URL);
    else envStore.set('AUTH_URL', PROD_AUTH_URL);
} else {
    // Dev: hard-pin to localhost. firebase-service.js calls dotenv.config() during
    // its own require, which loads the prod electron_app/.env (Railway URL) before
    // we get a chance to skip it. Force the dev URLs here to undo that leak so the
    // Electron websocket registers against the local FastAPI backend.
    envStore.set('REACT_APP_API_URL', DEV_API_URL);
    envStore.set('AUTH_URL', DEV_AUTH_URL);
}

// Propagate resolved values back to process.env so modules that read them directly
// (e.g. websocket_service.js, tally_loader_service.js) pick up the right values.
process.env.REACT_APP_API_URL = envStore.get('REACT_APP_API_URL');
process.env.AUTH_URL = envStore.get('AUTH_URL');

console.log('[env] isPackaged=', isPackaged, 'API=', process.env.REACT_APP_API_URL, 'AUTH=', process.env.AUTH_URL);

let mainWindow;
let tallyConnected = false;
let wsService;
let tallyLoaderService = null;


// Single instance lock
const gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
    // If we're not the primary instance, quit
    app.quit();
    return;
} else {
    // We're the primary instance
    app.on('second-instance', (event, commandLine) => {
        // Someone tried to run a second instance, we should focus our window
        if (mainWindow) {
            if (mainWindow.isMinimized()) mainWindow.restore();
            mainWindow.focus();
            
            // Check if the command line contains our protocol
            const deepLinkUrl = commandLine.find(arg => arg.startsWith('eazybooks://'));
            if (deepLinkUrl) {
                console.log('Found deep link URL in second instance:', deepLinkUrl);
                handleDeepLink(deepLinkUrl);
            }
        }
    });
}

// Handle deep link URL if app was launched via protocol on Windows (first launch)
const deepLinkArg = process.argv.find(arg => arg.startsWith('eazybooks://'));
if (deepLinkArg) {
    app.whenReady().then(() => {
        handleDeepLink(deepLinkArg);
    });
}

// URL actually used for HTTP XML checks — defaults to TALLY_URL but is
// overridden by connect-odbc when the user configures a different
// server/port (e.g. Tally running on another machine on the LAN).
let currentTallyUrl = TALLY_URL;

async function checkTallyConnection(url = currentTallyUrl) {
    try {
        const xmlQuery = `<?xml version="1.0" ?><ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>CompanyCheck</ID></HEADER><BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES><TDL><TDLMESSAGE><COLLECTION NAME="CompanyCheck"><TYPE>Company</TYPE><FETCH>NAME</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>`;
        await axios.post(url, xmlQuery, {
            headers: { 'Content-Type': 'text/xml' },
            timeout: 5000
        });
        if (!tallyConnected) {
            tallyConnected = true;
            console.log('Tally connected via HTTP XML API');
            if (mainWindow) mainWindow.webContents.send('odbc-status-changed', true);
        }
        // Keep the websocket bridge's Tally leg in sync so the web app reflects
        // it without a round-trip; pushes immediately on change (RC4).
        if (wsService) wsService.updateTallyStatus(true);
        return true;
    } catch (error) {
        if (tallyConnected) {
            tallyConnected = false;
            console.log('Tally disconnected');
            if (mainWindow) mainWindow.webContents.send('odbc-status-changed', false);
        }
        if (wsService) wsService.updateTallyStatus(false);
        return false;
    }
}

function initializeTallyConnectionCheck() {
    console.log('Initializing Tally connection check (HTTP XML API)...');
    // Check immediately
    checkTallyConnection();
    // Then check every 30 seconds
    setInterval(checkTallyConnection, 30000);
}

// Initialize when the app is ready
app.whenReady().then(() => {
    // Create the loader BEFORE createWindow(): when stored auth credentials
    // exist, createWindow() immediately builds the persistent wsService and
    // only calls setTallyLoader() if tallyLoaderService is already set. If the
    // loader is created after createWindow(), that session's socket keeps
    // tallyLoader = null forever, so 'run-tally-sync' (analytics first sync)
    // fails with "Tally loader not available on this desktop client".
    tallyLoaderService = new TallyLoaderService();
    createWindow();

    // Handle macOS behavior
    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});

// Add this function to handle WebSocket initialization
async function initializeWebSocket() {
    try {
        if (!isFirebaseInitialized() || !auth) {
            console.log('Firebase not initialized, skipping WebSocket initialization via Firebase');
            return;
        }
        // Get current user if available
        const currentUser = auth.currentUser;
        if (currentUser) {
            const token = await currentUser.getIdToken();
            if (!wsService) {
                wsService = new WebSocketService();
                if (tallyLoaderService) wsService.setTallyLoader(tallyLoaderService);
                setupWsEventHandlers(wsService);
            }
            await wsService.connect({
                token: token,
                uid: currentUser.uid
            });
            console.log('WebSocket initialized with Firebase UID:', currentUser.uid);
        } else {
            console.log('No authenticated user found, skipping WebSocket initialization');
        }
    } catch (error) {
        console.error('Error initializing WebSocket:', error);
    }
}

// Add the IPC handler for WebSocket testing
ipcMain.handle('test-websocket', async () => {
    try {
        const token = store.get('authToken');
        const uid = store.get('userUid');

        if (!token || !uid) {
            throw new Error('No authentication credentials found');
        }

        // Use wsService (the active connection) or create one
        if (!wsService || !wsService.isConnected) {
            console.log('WebSocket not connected, reconnecting...');
            if (wsService) {
                wsService.disconnect();
            }
            wsService = new WebSocketService();
                if (tallyLoaderService) wsService.setTallyLoader(tallyLoaderService);
            setupWsEventHandlers(wsService);
            await wsService.connect({ token, uid });
            await new Promise(resolve => setTimeout(resolve, 1000));
        }

        const testMessage = {
            type: 'test',
            data: {
                timestamp: Date.now(),
                message: 'Test message from Electron',
                uid: uid
            }
        };

        wsService.send(testMessage);
        return testMessage;
    } catch (error) {
        console.error('Error in test-websocket handler:', error);
        throw error;
    }
});
// IPC handlers for Tally operations (via HTTP XML API — no ODBC driver needed)
ipcMain.handle('connect-odbc', async (event, config) => {
    // Honor a custom server/port from the ODBC config form instead of always
    // hitting the hardcoded default — Tally may be running on another host.
    const host = config?.server?.trim() || '127.0.0.1';
    const port = config?.port || 9000;
    const url = `http://${host}:${port}`;

    const connected = await checkTallyConnection(url);
    if (connected) {
        currentTallyUrl = url;
        if (wsService) wsService.setTallyUrl(url);
    }
    return { success: connected, error: connected ? null : `Tally not reachable on ${host}:${port}` };
});

ipcMain.handle('disconnect-odbc', async () => {
    tallyConnected = false;
    if (mainWindow) mainWindow.webContents.send('odbc-status-changed', false);
    return { success: true };
});

ipcMain.handle('test-odbc-connection', async () => {
    const connected = await checkTallyConnection();
    return { success: connected, error: connected ? null : 'Not connected' };
});

ipcMain.handle('get-companies', async () => {
    try {
        const xmlQuery = `<?xml version="1.0" ?><ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>CompanyList</ID></HEADER><BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES><TDL><TDLMESSAGE><COLLECTION NAME="CompanyList"><TYPE>Company</TYPE><FETCH>NAME</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>`;
        const response = await axios.post(currentTallyUrl, xmlQuery, {
            headers: { 'Content-Type': 'text/xml' },
            timeout: 10000
        });
        const xml2js = require('xml2js');
        const parser = new xml2js.Parser({ explicitArray: false });
        const result = await new Promise((resolve, reject) => {
            parser.parseString(response.data, (err, res) => err ? reject(err) : resolve(res));
        });
        const companies = [];
        if (result?.ENVELOPE?.BODY?.DATA?.COLLECTION?.COMPANY) {
            const items = Array.isArray(result.ENVELOPE.BODY.DATA.COLLECTION.COMPANY)
                ? result.ENVELOPE.BODY.DATA.COLLECTION.COMPANY
                : [result.ENVELOPE.BODY.DATA.COLLECTION.COMPANY];
            for (const item of items) {
                const name = (item.$?.NAME || item.NAME || '').trim();
                if (name) companies.push({ Name: name });
            }
        }
        return { success: true, companies };
    } catch (error) {
        console.error('Error getting companies:', error.message);
        return { success: false, error: error.message };
    }
});

ipcMain.handle('get-company-details', async (event, company) => {
    return { success: true, details: { name: company } };
});

// Add IPC handlers for TallyLoader service
ipcMain.handle('tally-loader:get-status', () => {
    return tallyLoaderService.getStatus();
  });

ipcMain.handle('tally-loader:save-config', async (event, config) => {
return tallyLoaderService.saveConfig(config);
});

ipcMain.handle('tally-loader:start-export', async (event, options) => {
return await tallyLoaderService.startTallyExport(options);
});

ipcMain.handle('tally-loader:read-import-log', () => {
return tallyLoaderService.readImportLog();
});

ipcMain.handle('tally-loader:read-error-log', () => {
return tallyLoaderService.readErrorLog();
});

ipcMain.handle('tally-loader:test-tally-connection', async () => {
return await tallyLoaderService.testTallyConnection();
});

ipcMain.handle('tally-loader:test-database-connection', async () => {
if (!tallyLoaderService) {
    console.error('tallyLoaderService is not initialized');
    return { success: false, error: 'Service not initialized' };
    }
    return await tallyLoaderService.testDatabaseConnection();
});

// Handle deep linking for macOS
app.on('open-url', (event, url) => {
    event.preventDefault();
    console.log('Received open-url event:', url);
    handleDeepLink(url);
});

// Function to handle deep links
async function handleDeepLink(url) {
    try {
        console.log('Received deep link URL:', url);
        const urlObj = new URL(url);
        console.log('Protocol:', urlObj.protocol);
        console.log('Search params:', Object.fromEntries(urlObj.searchParams));
        if (urlObj.protocol === 'eazybooks:') {
            const token = urlObj.searchParams.get('token');
            
            if (!token) {
                throw new Error('No token received in callback');
            }

            console.log('Received auth callback token');

            // Store the JWT token
            store.set('authToken', token);

            // Parse the JWT token to get user info
            try {
                const payload = JSON.parse(atob(token.split('.')[1]));
                const uid = payload.uid;
                
                if (!uid) {
                    throw new Error('No user ID found in token');
                }

                console.log('Successfully parsed JWT and got UID:', uid);
                store.set('userUid', uid);

                // Reload the main window
                if (!mainWindow) {
                    createWindow();
                } else {
                    try {
                        console.log('Loading main interface...');
                        // Force reload and wait for it to complete
                        await mainWindow.loadFile(path.join(__dirname, '../../public/app/index.html'));
                        mainWindow.show();
                        mainWindow.focus();
                        
                        // Send auth success event to renderer
                        mainWindow.webContents.send('auth-success', {
                            uid: uid,
                            token: token
                        });
                        
                        console.log('Main interface loaded successfully');
                    } catch (loadError) {
                        console.error('Error loading main interface:', loadError);
                    }
                }

                // Try to connect WebSocket, but don't fail if it doesn't work
                try {
                    if (wsService) {
                        wsService.disconnect();
                    }
                    wsService = new WebSocketService();
                if (tallyLoaderService) wsService.setTallyLoader(tallyLoaderService);
                    setupWsEventHandlers(wsService);

                    await wsService.connect({
                        token: token,
                        uid: uid
                    });

                    console.log('WebSocket connected successfully');
                } catch (wsError) {
                    console.error('WebSocket connection failed:', wsError);
                    // Send websocket error to renderer but don't redirect to login
                    if (mainWindow) {
                        mainWindow.webContents.send('websocket-error', {
                            message: 'Failed to connect to WebSocket server'
                        });
                    }
                }
            } catch (jwtError) {
                console.error('Error parsing JWT:', jwtError);
                if (mainWindow) {
                    mainWindow.webContents.send('auth-error', {
                        message: 'Failed to parse authentication token'
                    });
                }
                throw jwtError;
            }
        }
    } catch (error) {
        console.error('Error handling deep link:', error);
        if (mainWindow) {
            mainWindow.webContents.send('auth-error', {
                message: 'Failed to complete authentication: ' + error.message
            });
            //on error, redirect back to login page
            await mainWindow.loadFile(path.join(__dirname, '../../public/app/login.html'));
        }
    }
}

// Configure auto-updater
autoUpdater.autoDownload = false; // Don't automatically download updates
autoUpdater.autoInstallOnAppQuit = true; // Install updates when app quits

// Auto-updater reads its feed URL from app-update.yml, which electron-builder
// embeds at build time from the `publish` config in package.json. We no longer
// call setFeedURL at runtime — keeping the source of truth in one place
// (package.json → app-update.yml → autoUpdater) avoids drift between manual
// download distribution and the update channel.
function configureAutoUpdater() {
    autoUpdater.on('checking-for-update', () => {
        console.log('Checking for updates...');
        mainWindow?.webContents.send('update_checking');
    });

    autoUpdater.on('update-available', (info) => {
        console.log('Update available:', info);
        mainWindow?.webContents.send('update_available', info);
    });

    autoUpdater.on('update-not-available', (info) => {
        console.log('Update not available:', info);
        mainWindow?.webContents.send('update_not_available', info);
    });

    autoUpdater.on('error', (err) => {
        console.error('Update error:', err);
        mainWindow?.webContents.send('update_error', err);
    });

    autoUpdater.on('download-progress', (progressObj) => {
        console.log('Download progress:', progressObj);
        mainWindow?.webContents.send('update_progress', progressObj);
    });

    autoUpdater.on('update-downloaded', (info) => {
        console.log('Update downloaded:', info);
        mainWindow?.webContents.send('update_downloaded', info);
    });
}

// Add these IPC handlers for update control
ipcMain.handle('check_for_updates', () => {
    return autoUpdater.checkForUpdates();
});

ipcMain.handle('download_update', () => {
    return autoUpdater.downloadUpdate();
});

ipcMain.on('install_update', () => {
    autoUpdater.quitAndInstall(false, true);
});

function setupWsEventHandlers(ws) {
    if (!ws) return;

    ws.on('connected', () => {
        console.log('WebSocket connected');
        if (mainWindow) {
            mainWindow.webContents.send('websocket-status', { connected: true });
        }
    });

    ws.on('reconnected', () => {
        console.log('WebSocket reconnected successfully');
        if (mainWindow) {
            mainWindow.webContents.send('websocket-status', { connected: true });
        }
    });

    ws.on('disconnected', () => {
        console.log('WebSocket disconnected — auto-reconnect will attempt');
        if (mainWindow) {
            mainWindow.webContents.send('websocket-status', { connected: false });
        }
    });

    ws.on('reconnect-failed', () => {
        console.log('WebSocket reconnection failed after max attempts');
        if (mainWindow) {
            mainWindow.webContents.send('websocket-status', {
                connected: false,
                error: 'Reconnection failed after maximum attempts'
            });
        }
    });

    ws.on('error', (error) => {
        console.error('WebSocket error:', error);
        if (mainWindow) {
            mainWindow.webContents.send('websocket-status', {
                connected: false,
                error: error.message
            });
        }
    });

    // Auth rejected (close 4003) — refresh the token and reconnect once, rather
    // than letting the reconnect loop hammer the server with a dead token (RC5).
    ws.on('auth-failed', async () => {
        console.warn('WebSocket auth failed — refreshing token and reconnecting');
        if (mainWindow) {
            mainWindow.webContents.send('websocket-status', { connected: false, state: 'reconnecting' });
        }
        try {
            await refreshToken();
            const token = store.get('authToken');
            const uid = store.get('userUid');
            if (token && uid) {
                await ws.connect({ token, uid });
            }
        } catch (err) {
            console.error('Reconnect after token refresh failed:', err);
        }
    });
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        title: `EazyBooks v${app.getVersion()}`,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, '../preload/preload-script.js')
        }
    });

    // Check if we have a stored auth token
    const token = store.get('authToken');
    const uid = store.get('userUid');

    console.log('\n=== Auth Check ===');
    console.log('Token exists:', !!token);
    console.log('UID exists:', !!uid);
    console.log('=================\n');
    
    if (!token || !uid) {
        // If no token or UID, load the login page
        console.log('Missing auth credentials, loading login page...');
        mainWindow.loadFile(path.join(__dirname, '../../public/app/login.html'));
    } else {
        // If we have both token and UID, load the main app
        console.log('Auth credentials found, loading main app...');
        mainWindow.loadFile(path.join(__dirname, '../../public/app/index.html'));

        // Initialize WebSocket service with both token and UID
        if (!wsService) {
            wsService = new WebSocketService();
                if (tallyLoaderService) wsService.setTallyLoader(tallyLoaderService);
            setupWsEventHandlers(wsService);
        }
        wsService.connect({
            token: token,
            uid: uid
        }).catch(error => {
            console.error('Failed to initialize WebSocket:', error);
            mainWindow.webContents.send('websocket-error', {
                message: 'Failed to connect to WebSocket server'
            });
        });
    }
    // Configure and check for updates
    configureAutoUpdater();
    autoUpdater.checkForUpdatesAndNotify();

    // Initialize ODBC connector when window is created
    initializeTallyConnectionCheck();
}
// Add token refresh logic
async function refreshToken() {
    try {
        // Get stored credentials
        const token = store.get('authToken');
        const uid = store.get('userUid');
        
        if (!token || !uid) {
            console.log('No stored credentials found, skipping token refresh');
            return;
        }

        // Make API call to refresh token
        const apiUrl = envStore.get('REACT_APP_API_URL');
        const response = await fetch(`${apiUrl}/auth/refresh-token`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ token })
        });

        if (!response.ok) {
            throw new Error('Failed to refresh token');
        }

        const data = await response.json();
        
        // Store the new token
        store.set('authToken', data.token);

        console.log("Token refreshed successfully");
        
        // Reconnect WebSocket with new token
        // if (wsService) {
        //     wsService.connect({ token: data.token, uid });
        // }
    } catch (error) {
        console.error('Token refresh failed:', error);
        // Handle authentication error (e.g., redirect to login)
        if (mainWindow) {
            mainWindow.loadFile(path.join(__dirname, '../../public/app/login.html'));
        }
    }
}

// Set up token refresh interval (refresh 5 minutes before expiry)
setInterval(() => {
    const token = store.get('authToken');
    if (token) {
        // Decode token to check expiry
        const decoded = JSON.parse(Buffer.from(token.split('.')[1], 'base64').toString());
        const expiryTime = decoded.exp * 1000; // Convert to milliseconds
        const currentTime = Date.now();
        const fiveMinutes = 5 * 60 * 1000;

        if (expiryTime - currentTime <= fiveMinutes) {
            refreshToken();
        }
    }
}, 300000); // Check every 5 minute

// Handle auth redirection — opens login in system's default browser
ipcMain.handle('redirect-to-auth', async () => {
    try {
        const authUrl = envStore.get('AUTH_URL');
        const storedUid = store.get('userUid');

        const url = new URL(authUrl);
        url.searchParams.set('source', 'electron');
        if (storedUid) {
            url.searchParams.set('client_id', storedUid);
        }

        console.log('Opening auth URL in system browser:', url.toString());

        // Open in system's default browser (Chrome, Firefox, Safari, etc.)
        // After OAuth completes, the browser will redirect to eazybooks://auth?token=...
        // which will be handled by the OS and launch/focus this Electron app
        await shell.openExternal(url.toString());

        return { success: true };
    } catch (error) {
        console.error('Failed to open auth URL:', error);
        throw error;
    }
});

// Handle direct email auth token storage
ipcMain.handle('store-token', async (event, token, user) => {
    try {
        // Parse JWT to get uid
        const payload = JSON.parse(atob(token.split('.')[1]));
        const uid = payload.uid;

        if (!uid) {
            throw new Error('No user ID found in token');
        }

        // Store credentials
        store.set('authToken', token);
        store.set('userUid', uid);

        console.log('Token stored successfully for UID:', uid);

        // Load main app interface
        if (mainWindow) {
            await mainWindow.loadFile(path.join(__dirname, '../../public/app/index.html'));
            mainWindow.show();
            mainWindow.focus();

            mainWindow.webContents.send('auth-success', {
                uid: uid,
                token: token
            });
        }

        // Initialize WebSocket
        try {
            if (wsService) {
                wsService.disconnect();
            }
            wsService = new WebSocketService();
                if (tallyLoaderService) wsService.setTallyLoader(tallyLoaderService);
            setupWsEventHandlers(wsService);
            await wsService.connect({ token, uid });
        } catch (wsError) {
            console.error('WebSocket connection failed:', wsError);
        }

        return { success: true };
    } catch (error) {
        console.error('Error storing token:', error);
        return { success: false, error: error.message };
    }
});

// Add logout handler
ipcMain.handle('logout', async () => {
    try {
        // Clear stored credentials
        store.delete('authToken');
        store.delete('userUid');
        
        if (wsService) {
            wsService.disconnect();
            wsService = null;
        }
        if (mainWindow) {
            mainWindow.loadFile(path.join(__dirname, '../../public/app/login.html'));
        }
    } catch (error) {
        console.error('Logout error:', error);
    }
});


// On macOS the app keeps running after the window closes (and can be
// re-activated), so we must NOT tear down the websocket bridge here — doing so
// silently took the web app offline while the app was still "running" (RC5).
// Only clean up + quit on non-macOS platforms, where window-all-closed really
// is app shutdown.
app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        if (wsService) {
            wsService.disconnect();
            wsService = null;
        }
        tallyLoaderService = null;
        app.quit();
    }
});

// Clean, intentional disconnect on real quit so the backend clears stale status
// immediately rather than waiting for the heartbeat reaper (RC5).
app.on('before-quit', () => {
    if (wsService) {
        wsService.disconnect();
        wsService = null;
    }
    tallyLoaderService = null;
});

// Add these IPC handlers
ipcMain.handle('get-odbc-status', () => {
    return tallyConnected;
});

ipcMain.handle('get-websocket-status', () => {
    return { connected: wsService ? wsService.isConnected : false };
});

// Expose environment variables to renderer process
ipcMain.handle('get-env-vars', () => {
    return {
        REACT_APP_API_URL: envStore.get('REACT_APP_API_URL'),
        GENERATE_SOURCEMAP: envStore.get('GENERATE_SOURCEMAP'),
        SKIP_PREFLIGHT_CHECK: envStore.get('SKIP_PREFLIGHT_CHECK')
    };
});

// Add version handler
ipcMain.handle('get-app-version', () => {
    return app.getVersion();
});
