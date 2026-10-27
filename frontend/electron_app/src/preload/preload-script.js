const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods that allow the renderer process to use
// the ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld(
    'electron',
    {
        // App Info
        getAppVersion: () => ipcRenderer.invoke('get-app-version'),
        
        // Authentication
        redirectToAuth: () => ipcRenderer.invoke('redirect-to-auth'),
        storeToken: (token, user) => ipcRenderer.invoke('store-token', token, user),
        logout: () => ipcRenderer.invoke('logout'),

        // WebSocket Operations
        testWebSocket: () => ipcRenderer.invoke('test-websocket'),
        onWebSocketMessage: (callback) => {
            ipcRenderer.on('websocket-message', (event, message) => callback(message));
        },
        onWebSocketStatus: (callback) => {
            ipcRenderer.on('websocket-status', (event, status) => callback(status));
        },
        
        // Listen for auth token
        onAuthTokenReceived: (callback) => {
            ipcRenderer.on('auth-token-received', (event, token) => callback(token));
        },
        onAuthSuccess: (callback) => {
            ipcRenderer.on('auth-success', (event, data) => callback(data));
        },
        
        onAuthError: (callback) => {
            ipcRenderer.on('auth-error', (event, error) => callback(error));
        },

        // ODBC Operations
        connectODBC: (config) => ipcRenderer.invoke('connect-odbc', config),
        disconnectODBC: () => ipcRenderer.invoke('disconnect-odbc'),
        testODBCConnection: () => ipcRenderer.invoke('test-odbc-connection'),
        getCompanies: () => ipcRenderer.invoke('get-companies'),
        getCompanyDetails: (company) => ipcRenderer.invoke('get-company-details', company),
        onODBCStatusChanged: (callback) => {
            ipcRenderer.on('odbc-status-changed', (event, status) => callback(status));
        },
        
        // Remove listeners
        removeAllListeners: (channel) => {
            ipcRenderer.removeAllListeners(channel);
        },

        // Get ODBC status
        getODBCStatus: () => ipcRenderer.invoke('get-odbc-status'),

        // Get WebSocket status
        getWebSocketStatus: () => ipcRenderer.invoke('get-websocket-status'),

        // Get environment variables
        getEnvVars: () => ipcRenderer.invoke('get-env-vars'),
    }
);

contextBridge.exposeInMainWorld('tallyLoader', {
  getStatus: () => ipcRenderer.invoke('tally-loader:get-status'),
  saveConfig: (config) => ipcRenderer.invoke('tally-loader:save-config', config),
  startExport: (options) => ipcRenderer.invoke('tally-loader:start-export', options),
  readImportLog: () => ipcRenderer.invoke('tally-loader:read-import-log'),
  readErrorLog: () => ipcRenderer.invoke('tally-loader:read-error-log'),
  testTallyConnection: () => ipcRenderer.invoke('tally-loader:test-tally-connection'),
  testDatabaseConnection: () => ipcRenderer.invoke('tally-loader:test-database-connection')
});