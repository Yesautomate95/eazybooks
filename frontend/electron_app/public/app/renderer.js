// Login functionality
let authData = null;
document.addEventListener('DOMContentLoaded', () => {
    const loginButton = document.getElementById('loginButton');
    
    if (loginButton) {
        loginButton.addEventListener('click', async () => {
            try {
                // Show loading state
                loginButton.disabled = true;
                loginButton.innerHTML = `
                    <svg class="animate-spin h-5 w-5 mr-2" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"/>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
                    </svg>
                    Signing in...
                `;

                // Redirect to web app for authentication
                await window.electron.redirectToAuth();

                // Listen for auth token
                window.electron.onAuthTokenReceived((token) => {
                    
                    console.log('Auth token received, reconnecting WebSocket...');
                    window.electron.testWebSocket(); // This will trigger a new connection with the token
                    // Store the token
                    localStorage.setItem('authToken', token);
                    
                    // Remove the listener
                    window.electron.removeAllListeners('auth-token-received');
                    
                    // The main process will handle the window reload
                });

            } catch (error) {
                console.error('Authentication error:', error);
                // Reset button state
                loginButton.disabled = false;
                loginButton.innerHTML = `
                    <svg class="w-5 h-5 mr-2" viewBox="0 0 24 24">
                        <path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                    </svg>
                    Sign in with Google
                `;
            }
        });
    }

    // Initialize status indicators
    initializeStatusIndicators();
    checkWebSocketStatus(); // Initial check

    // Update version display
    updateVersionDisplay();
});

// UI Elements
const connectionStatus = document.getElementById('connection-status');
const apiStatus = document.getElementById('api-status');
const odbcStatus = document.getElementById('odbc-status');
const currentCompany = document.getElementById('current-company');
const connectButton = document.getElementById('connect-button');
const testButton = document.getElementById('test-connection');
const refreshButton = document.getElementById('refresh-companies');
const companiesRefreshButton = document.getElementById('companies-refresh');
const settingsForm = document.getElementById('settings-form');
const logContainer = document.getElementById('log-container');
const clearLogsButton = document.getElementById('clear-logs');
const exportLogsButton = document.getElementById('export-logs');
const logLevelSelect = document.getElementById('log-level');
const companiesList = document.querySelector('.companies-list');
const logoutButton = document.getElementById('logout-button');

// Bottom Bar Elements
const odbcStatusText = document.getElementById('odbc-status-text');
const wsStatusText = document.getElementById('ws-status-text');

// Function to safely add event listeners
function safeAddEventListener(element, event, handler) {
    if (element) {
        element.addEventListener(event, handler);
    } else {
        console.warn(`Element not found for event: ${event}`);
    }
}

// Tab handling
const tabButtons = document.querySelectorAll('[data-tab]');
const tabPanels = document.querySelectorAll('[data-panel]');

if (tabButtons.length > 0 && tabPanels.length > 0) {
    tabButtons.forEach(button => {
        safeAddEventListener(button, 'click', async () => {
            const targetPanel = button.dataset.tab;
            
            // Update active states
            tabButtons.forEach(btn => btn.classList.remove('active'));
            tabPanels.forEach(panel => panel.classList.remove('active'));
            
            button.classList.add('active');
            const targetPanelElement = document.querySelector(`[data-panel="${targetPanel}"]`);
            if (targetPanelElement) {
                targetPanelElement.classList.add('active');
            }

            // If switching to companies tab and we're connected, refresh companies
            if (targetPanel === 'companies' && isConnected) {
                addLog('info', 'Switching to Companies tab - refreshing companies list');
                await refreshCompanies();
            }
        });
    });
}



// Add after your existing UI element declarations
const testWebSocketButton = document.getElementById('test-websocket');

// Add WebSocket status handling
let wsConnected = false;

// Listen for WebSocket status changes
window.electron.onWebSocketStatus((status) => {
    wsConnected = status.connected;

    // Update bottom bar WebSocket status
    if (wsStatusText) {
        wsStatusText.textContent = status.connected ? 'CONNECTED' : 'DISCONNECTED';
        wsStatusText.classList.toggle('connected', status.connected);
        wsStatusText.classList.toggle('disconnected', !status.connected);
    }

    addLog('info', `WebSocket ${status.connected ? 'connected' : 'disconnected'}`);
});

// Listen for WebSocket messages
window.electron.onWebSocketMessage((message) => {
    addLog('info', `WebSocket message received: ${JSON.stringify(message)}`);
});

// Add WebSocket test button handler
if (testWebSocketButton) {
    safeAddEventListener(testWebSocketButton, 'click', async () => {
        try {
            addLog('info', 'Testing WebSocket connection...');
            const result = await window.electron.testWebSocket();
            if (result) {
                addLog('info', `WebSocket test message sent: ${JSON.stringify(result)}`);
            } else {
                addLog('error', 'Failed to send WebSocket test message');
            }
        } catch (error) {
            addLog('error', `WebSocket test error: ${error.message}`);
        }
    });
}

// Authentication status handling
window.electron.onAuthSuccess((data) => {
    console.log('Authentication successful:', data);
    authData = data;
    
    // Update UI to show authenticated state
    updateAuthStatus(true);
    
    // If we have a WebSocket connection, reconnect it with the new auth data
    if (window.electron.testWebSocket) {
        window.electron.testWebSocket();
    }
});

window.electron.onAuthError((error) => {
    console.error('Authentication error:', error);
    authData = null;
    updateAuthStatus(false);
});

// Add this function to update UI based on auth status
function updateAuthStatus(isAuthenticated) {
    const statusIndicator = document.querySelector('.status-indicator');
    if (statusIndicator) {
        statusIndicator.classList.remove('status-disconnected');
        statusIndicator.classList.toggle('status-connected', isAuthenticated);
    }
    
    const connectionStatus = document.getElementById('connection-status');
    if (connectionStatus) {
        connectionStatus.textContent = isAuthenticated ? 'Connected' : 'Disconnected';
    }
}
// Connection handling
let isConnected = false;

// Add these variables at the top with other state variables
let odbcConnected = false;

if (connectButton) {
    safeAddEventListener(connectButton, 'click', async () => {
        try {
            if (isConnected) {
                const result = await window.electron.disconnectODBC();
                if (result.success) {
                    updateConnectionStatus(false);
                    // Clear companies list on disconnect
                    updateCompaniesList([]);
                } else {
                    addLog('error', `Failed to disconnect: ${result.error}`);
                }
            } else {
                // Get current configuration
                const config = getCurrentConfig();
                addLog('info', 'Attempting to connect with config: ' + JSON.stringify({
                    ...config,
                    password: '***' // Hide password in logs
                }));
                const result = await window.electron.connectODBC(config);
                if (result.success) {
                    updateConnectionStatus(true);
                    // Automatically fetch companies after successful connection
                    addLog('info', 'Connection successful - fetching companies');
                    await refreshCompanies();
                } else {
                    addLog('error', `Failed to connect: ${result.error}`);
                }
            }
        } catch (error) {
            addLog('error', `Connection error: ${error.message}`);
        }
    });
}

if (testButton) {
    safeAddEventListener(testButton, 'click', async () => {
        try {
            const result = await window.electron.testODBCConnection();
            if (result.success) {
                addLog('info', 'Connection test successful');
            } else {
                addLog('error', `Connection test failed: ${result.error}`);
            }
        } catch (error) {
            addLog('error', `Test error: ${error.message}`);
        }
    });
}

// Settings form handling
if (settingsForm) {
    safeAddEventListener(settingsForm, 'submit', async (event) => {
        event.preventDefault();
        
        const formData = new FormData(settingsForm);
        const config = {
            odbc: {
                driver: formData.get('odbc-driver'),
                server: formData.get('odbc-server'),
                port: formData.get('odbc-port'),
                database: formData.get('odbc-database'),
                username: formData.get('odbc-username'),
                password: formData.get('odbc-password'),
                autoConnect: formData.get('auto-connect') === 'on',
                retryAttempts: parseInt(formData.get('retry-attempts'), 10),
                retryDelay: parseInt(formData.get('retry-delay'), 10)
            },
            api: {
                port: parseInt(formData.get('api-port'), 10),
                host: formData.get('api-host')
            },
            security: {
                apiKey: formData.get('api-key'),
                allowedIPs: formData.get('allowed-ips').split(',').map(ip => ip.trim())
            }
        };
        
        try {
            const result = await window.electron.saveConfig(config);
            if (result.success) {
                addLog('info', 'Configuration saved successfully');
            } else {
                addLog('error', `Failed to save configuration: ${result.error}`);
            }
        } catch (error) {
            addLog('error', `Save error: ${error.message}`);
        }
    });
}

// Logging
function addLog(level, message) {
    if (!logContainer) {
        console.log(`[${level}] ${message}`);
        return;
    }
    
    const timestamp = new Date().toISOString();
    const logEntry = document.createElement('div');
    logEntry.className = `log-entry log-${level}`;
    logEntry.textContent = `[${timestamp}] ${level.toUpperCase()}: ${message}`;
    logContainer.appendChild(logEntry);
    logContainer.scrollTop = logContainer.scrollHeight;
}

if (clearLogsButton) {
    safeAddEventListener(clearLogsButton, 'click', () => {
        if (logContainer) {
            logContainer.innerHTML = '';
        }
    });
}

if (exportLogsButton) {
    safeAddEventListener(exportLogsButton, 'click', () => {
        if (!logContainer) return;
        
        const logs = Array.from(logContainer.children)
            .map(entry => entry.textContent)
            .join('\n');
        
        const blob = new Blob([logs], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `tally-connector-logs-${new Date().toISOString()}.txt`;
        a.click();
        URL.revokeObjectURL(url);
    });
}


// Event handlers
window.electron.onODBCStatusChanged((status) => {
    odbcConnected = status;

    // Update existing top status
    updateConnectionStatus(status);

    // Update bottom bar ODBC status
    if (odbcStatusText) {
        odbcStatusText.textContent = status ? 'CONNECTED' : 'DISCONNECTED';
        odbcStatusText.classList.toggle('connected', status);
        odbcStatusText.classList.toggle('disconnected', !status);
    }

    // Log the status change
    addLog('info', `Tally ODBC ${status ? 'connected' : 'disconnected'}`);
});

// Helper functions
function updateConnectionStatus(connected) {
    isConnected = connected;
    odbcConnected = connected;

    const statusIndicator = document.querySelector('.status-indicator');
    
    if (connectionStatus) {
        connectionStatus.textContent = connected ? 'Connected' : 'Disconnected';
    }
    
    if (statusIndicator) {
        if (connected) {
            statusIndicator.classList.remove('status-disconnected');
            statusIndicator.classList.add('status-connected');
        } else {
            statusIndicator.classList.remove('status-connected');
            statusIndicator.classList.add('status-disconnected');
        }
    }
    
    if (connectButton) {
        connectButton.textContent = connected ? 'Disconnect' : 'Connect';
    }
    
    // Update bottom bar status as well
    if (odbcStatusText) {
        odbcStatusText.textContent = connected ? 'CONNECTED' : 'DISCONNECTED';
        odbcStatusText.classList.toggle('connected', connected);
        odbcStatusText.classList.toggle('disconnected', !connected);
    }

    if (testButton) {
        testButton.disabled = !connected;
    }
    if (refreshButton) {
        refreshButton.disabled = !connected;
    }
    if (companiesRefreshButton) {
        companiesRefreshButton.disabled = !connected;
    }
}

function getCurrentConfig() {
    if (!settingsForm) return {};
    
    // Get current configuration from settings form
    const formData = new FormData(settingsForm);
    return {
        driver: formData.get('odbc-driver'),
        server: formData.get('odbc-server'),
        port: formData.get('odbc-port'),
        database: formData.get('odbc-database'),
        username: formData.get('odbc-username'),
        password: formData.get('odbc-password')
    };
}

function updateCompaniesList(companies) {
    if (!companiesList) return;
    
    // Clear existing content
    companiesList.innerHTML = '';
    
    if (!Array.isArray(companies) || companies.length === 0) {
        const noCompanies = document.createElement('div');
        noCompanies.className = 'no-companies';
        noCompanies.innerHTML = '<p>No companies available</p>';
        companiesList.appendChild(noCompanies);
        return;
    }
    
    // Add each company to the list
    companies.forEach(company => {
        const companyElement = document.createElement('div');
        companyElement.className = 'company-item';
        
        // Handle different company data structures
        const companyName = company.Name || company.name || company.$Name || company;
        
        companyElement.textContent = companyName;
        companyElement.addEventListener('click', () => selectCompany(company));
        companiesList.appendChild(companyElement);
    });
    
    // Log the update
    addLog('info', `Updated companies list with ${companies.length} companies`);
}

function selectCompany(company) {
    if (currentCompany) {
        // Handle different company data structures
        const companyName = company.Name || company.name || company.$Name || company;
        currentCompany.textContent = companyName;
        addLog('info', `Selected company: ${companyName}`);
    }
}

// Add company details view handler
document.addEventListener('click', async (e) => {
    if (e.target.classList.contains('view-company')) {
        const company = e.target.dataset.company;
        try {
            const result = await window.electron.getCompanyDetails(company);
            if (result.success) {
                // TODO: Implement company details view
                addLog('info', `Viewing details for company: ${company}`);
            } else {
                addLog('error', `Failed to get company details: ${result.error}`);
            }
        } catch (error) {
            addLog('error', `Error viewing company details: ${error.message}`);
        }
    }
});

// Add logout handler
if (logoutButton) {
    logoutButton.addEventListener('click', async () => {
        try {
            await window.electron.logout();
        } catch (error) {
            console.error('Logout error:', error);
        }
    });
}

// Add this function definition
async function refreshCompanies() {
    try {
        addLog('info', 'Refreshing companies list...');
        
        // Try to get companies through WebSocket first if connected
        if (wsConnected) {
            try {
                const response = await window.electron.getCompanies();
                if (response.success && response.companies) {
                    updateCompaniesList(response.companies);
                    addLog('info', `Successfully fetched ${response.companies.length} companies via WebSocket`);
                    return;
                }
            } catch (wsError) {
                addLog('warn', `WebSocket companies fetch failed: ${wsError.message}, falling back to ODBC`);
            }
        }
        
        // Fallback to ODBC if WebSocket failed or not connected
        if (isConnected) {
            const response = await window.electron.getCompanies();
            if (response.success && response.companies) {
                updateCompaniesList(response.companies);
                addLog('info', `Successfully fetched ${response.companies.length} companies via ODBC`);
            } else {
                addLog('error', `Failed to fetch companies: ${response.error || 'Unknown error'}`);
            }
        } else {
            addLog('error', 'Cannot fetch companies: Not connected to Tally');
        }
    } catch (error) {
        addLog('error', `Error refreshing companies: ${error.message}`);
        updateCompaniesList([]); // Clear the list on error
    }
}

// Add this function to initialize the status indicators
function initializeStatusIndicators() {
    // Initialize ODBC status
    if (window.electron.getODBCStatus) {
        window.electron.getODBCStatus().then(status => {
            odbcConnected = status;
            if (odbcStatusText) {
                odbcStatusText.textContent = status ? 'CONNECTED' : 'DISCONNECTED';
                odbcStatusText.classList.toggle('connected', status);
                odbcStatusText.classList.toggle('disconnected', !status);
            }
        });
    }

    // Initialize WebSocket status
    if (window.electron.getWebSocketStatus) {
        window.electron.getWebSocketStatus().then(status => {
            wsConnected = status;
            if (wsStatusText) {
                wsStatusText.textContent = status ? 'CONNECTED' : 'DISCONNECTED';
                wsStatusText.classList.toggle('connected', status);
                wsStatusText.classList.toggle('disconnected', !status);
            }
        });
    }
}

// Update the checkWebSocketStatus function
function checkWebSocketStatus() {
    if (window.electron.getWebSocketStatus) {
        window.electron.getWebSocketStatus().then(status => {
            wsConnected = status.connected;
            if (wsStatusText) {
                wsStatusText.textContent = status.connected ? 'CONNECTED' : 'DISCONNECTED';
                wsStatusText.classList.toggle('connected', status.connected);
                wsStatusText.classList.toggle('disconnected', !status.connected);
            }
            console.log('WebSocket status:', status.connected ? 'Connected' : 'Disconnected');
        }).catch(error => {
            console.error('Error checking WebSocket status:', error);
        });
    }
}

// Keep the periodic check
setInterval(checkWebSocketStatus, 5000);

// Tally Import Tab Functionality
document.addEventListener('DOMContentLoaded', function() {
    // Elements
    const importStatusText = document.getElementById('import-status-text');
    const importRunningStatus = document.getElementById('import-running-status');
    const importLastSync = document.getElementById('import-last-sync');
    const startExportBtn = document.getElementById('start-export-btn');
    const viewLogsBtn = document.getElementById('view-logs-btn');
    const testTallyBtn = document.getElementById('test-tally-btn');
    const testDatabaseBtn = document.getElementById('test-database-btn');
    const connectionTestResults = document.getElementById('connection-test-results');
    const tallyImportConfigForm = document.getElementById('tally-import-config-form');
    const logsCard = document.getElementById('logs-card');
    const closeLogsBtn = document.getElementById('close-logs-btn');
    const importLog = document.getElementById('import-log');
    const errorLog = document.getElementById('error-log');

    // Form Fields
    const tallyServer = document.getElementById('tally-server');
    const tallyPort = document.getElementById('tally-port');
    const tallyFromDate = document.getElementById('tally-fromdate');
    const tallyToDate = document.getElementById('tally-todate');
    const tallySync = document.getElementById('tally-sync');
    const tallyCompany = document.getElementById('tally-company');
    const dbServer = document.getElementById('db-server');
    const dbPort = document.getElementById('db-port');
    const dbName = document.getElementById('db-name');
    const dbUsername = document.getElementById('db-username');
    const dbPassword = document.getElementById('db-password');

    // Function to populate the company dropdown
    async function populateCompanyDropdown() {
        try {
            console.log('Starting populateCompanyDropdown');
            const tallyCompany = document.getElementById('tally-company');
            if (!tallyCompany) {
                console.error('Could not find tally-company dropdown element');
                return;
            }

            // Clear current options except the first one
            console.log('Current options before clearing:', tallyCompany.options.length);
            while (tallyCompany.options.length > 1) {
                tallyCompany.remove(1);
            }

            // Try to get companies
            let companies = [];

            // Try WebSocket first if connected
            if (wsConnected) {
                try {
                    console.log('Attempting to fetch companies via WebSocket');
                    const response = await window.electron.getCompanies();
                    console.log('WebSocket companies response:', response);
                    if (response.success && response.companies) {
                        companies = response.companies;
                        console.log('Successfully fetched companies via WebSocket:', companies);
                        addLog('info', `Fetched ${companies.length} companies for dropdown via WebSocket`);
                    }
                } catch (wsError) {
                    console.error('WebSocket companies fetch error:', wsError);
                    addLog('warn', `WebSocket companies fetch failed: ${wsError.message}, falling back to ODBC`);
                }
            }

            // Fallback to ODBC if WebSocket failed or not connected
            if (companies.length === 0 && isConnected) {
                try {
                    console.log('Attempting to fetch companies via ODBC');
                    const response = await window.electron.getCompanies();
                    console.log('ODBC companies response:', response);
                    if (response.success && response.companies) {
                        companies = response.companies;
                        console.log('Successfully fetched companies via ODBC:', companies);
                        addLog('info', `Fetched ${companies.length} companies for dropdown via ODBC`);
                    }
                } catch (error) {
                    console.error('ODBC companies fetch error:', error);
                    addLog('error', `Failed to fetch companies: ${error.message}`);
                }
            }

            // Add companies to dropdown
            if (companies && companies.length > 0) {
                console.log('Adding companies to dropdown:', companies);
                companies.forEach(company => {
                    console.log('Processing company:', company);
                    // Handle both possible data structures, but prioritize 'Name' since that's what we're getting
                    const companyName = company.Name || company.$Name || company['$Name'] || company.name || company;
                    console.log('Extracted company name:', companyName);
                    
                    if (companyName && typeof companyName === 'string') {
                        const option = document.createElement('option');
                        option.value = companyName;
                        option.textContent = companyName;
                        tallyCompany.appendChild(option);
                        console.log('Added company to dropdown:', companyName);
                    } else {
                        console.warn('Invalid company name format:', company);
                    }
                });
                console.log('Final dropdown options count:', tallyCompany.options.length);
                addLog('info', `Added ${companies.length} companies to dropdown`);
            } else {
                console.warn('No companies available to add to dropdown');
                addLog('warn', 'No companies available to add to dropdown');
            }
        } catch (error) {
            console.error('Error in populateCompanyDropdown:', error);
            addLog('error', `Error populating company dropdown: ${error.message}`);
        }
    }

    // Load initial status and configuration
    function loadStatus() {
        if (!window.tallyLoader) {
            console.error('tallyLoader API not available');
            return;
        }

        window.tallyLoader.getStatus().then(status => {
            // Update status display
            importStatusText.textContent = status.syncStatus;
            importStatusText.className = `status-indicator ${status.syncStatus}`;
            importRunningStatus.textContent = status.isRunning ? 'Yes' : 'No';

            if (status.lastSyncTime) {
                importLastSync.textContent = new Date(status.lastSyncTime).toLocaleString();
            }

            // Disable start button if already running
            startExportBtn.disabled = status.isRunning;

            // Fill form with config values if available
            if (status.config) {
                const config = status.config;

                // Tally settings
                if (config.tally) {
                    tallyServer.value = config.tally.server || '';
                    tallyPort.value = config.tally.port || '';
                    tallySync.value = config.tally.sync || 'full';

                    // For company dropdown, check if the saved value exists as an option
                    const savedCompany = config.tally.company || '';
                    if (savedCompany) {
                        // First try to find the option
                        let optionExists = false;
                        for (let i = 0; i < tallyCompany.options.length; i++) {
                            if (tallyCompany.options[i].value === savedCompany) {
                                tallyCompany.selectedIndex = i;
                                optionExists = true;
                                break;
                            }
                        }

                        // If option doesn't exist, add it
                        if (!optionExists && savedCompany) {
                            const option = document.createElement('option');
                            option.value = savedCompany;
                            option.textContent = savedCompany;
                            tallyCompany.appendChild(option);
                            tallyCompany.value = savedCompany;
                        }
                    }
                }

                // Database settings
                if (config.database) {
                    dbServer.value = config.database.server || '';
                    dbPort.value = config.database.port || '';
                    dbName.value = config.database.schema || '';
                    dbUsername.value = config.database.username || '';
                    dbPassword.value = config.database.password || '';
                }
            }

            // Try to populate the company dropdown
            populateCompanyDropdown();
        }).catch(error => {
            console.error('Error loading status:', error);
            addLog('Error loading status: ' + error.message, 'error');
        });
    }

    // Test Database connection
    testDatabaseBtn.addEventListener('click', function() {
        testDatabaseBtn.disabled = true;
        testDatabaseBtn.textContent = 'Testing...';
        connectionTestResults.innerHTML = 'Testing database connection...';

        window.tallyLoader.testDatabaseConnection().then(result => {
            console.log('DB Connection test result:', result);
            if (result.success) {
                connectionTestResults.innerHTML = '<div style="color: #27ae60; font-weight: bold;">✓ Database connection successful!</div>';
                if (result.message) {
                    connectionTestResults.innerHTML += `<div style="margin-top: 8px;">${result.message}</div>`;
                }
                addLog('Database connection test: Success', 'info');
            } else {
                connectionTestResults.innerHTML = `<div style="color: #e74c3c; font-weight: bold;">✗ Database connection failed: ${result.error}</div>`;
                addLog(`Database connection test failed: ${result.error}`, 'error');
            }
        }).catch(error => {
            connectionTestResults.innerHTML = `<div style="color: #e74c3c; font-weight: bold;">✗ Error: ${error.message}</div>`;
            addLog(`Database connection test error: ${error.message}`, 'error');
        }).finally(() => {
            testDatabaseBtn.disabled = false;
            testDatabaseBtn.textContent = 'Test Database Connection';
        });
    });

    // Add form submission handler
    if (tallyImportConfigForm) {
        tallyImportConfigForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            try {
                const config = {
                    tally: {
                        server: tallyServer.value,
                        port: parseInt(tallyPort.value),
                        fromdate: 'auto',  // Set to auto for automatic date handling
                        todate: 'auto',    // Set to auto for automatic date handling
                        sync: tallySync.value,
                        company: tallyCompany.value
                    }
                };

                const response = await window.tallyLoader.saveConfig(config);
                if (response.success) {
                    addLog('info', 'Configuration saved successfully');
                } else {
                    addLog('error', `Failed to save configuration: ${response.error}`);
                }
            } catch (error) {
                addLog('error', `Error saving configuration: ${error.message}`);
            }
        });
    }

    // Start export
    startExportBtn.addEventListener('click', function() {
        startExportBtn.disabled = true;
        startExportBtn.textContent = 'Starting...';

        window.tallyLoader.startExport().then(result => {
            if (result.success) {
                addLog('Tally data export started successfully', 'info');
                alert('Export started successfully!');
                // Update button text to show running state
                startExportBtn.textContent = 'Running...';
                // Start status polling since export is now running
                startStatusPolling();
            } else {
                addLog(`Failed to start export: ${result.error}`, 'error');
                alert(`Failed to start export: ${result.error}`);
                startExportBtn.disabled = false;
                startExportBtn.textContent = 'Start Import';
            }
            // Load status once after starting export
            loadStatus();
        }).catch(error => {
            addLog(`Error starting export: ${error.message}`, 'error');
            alert(`Error: ${error.message}`);
            startExportBtn.disabled = false;
            startExportBtn.textContent = 'Start Import';
        });
    });

    // View logs
    viewLogsBtn.addEventListener('click', function() {
        logsCard.style.display = 'block';
        viewLogsBtn.disabled = true;

        // Load import log
        window.tallyLoader.readImportLog().then(log => {
            importLog.textContent = log || 'No import log available.';
        }).catch(error => {
            importLog.textContent = `Error loading import log: ${error.message}`;
        });

        // Load error log
        window.tallyLoader.readErrorLog().then(log => {
            errorLog.textContent = log || 'No errors logged.';
        }).catch(error => {
            errorLog.textContent = `Error loading error log: ${error.message}`;
        }).finally(() => {
            viewLogsBtn.disabled = false;
        });
    });

    // Close logs
    closeLogsBtn.addEventListener('click', function() {
        logsCard.style.display = 'none';
    });

    // Initialize
    if (window.tallyLoader) {
        loadStatus();

        // Variable to hold interval ID for status updates
        let statusCheckInterval = null;

        // Function to start automatic status polling
        function startStatusPolling() {
            // Only start if not already polling
            if (!statusCheckInterval) {
                let completionDetected = false;

                statusCheckInterval = setInterval(function() {
                    window.tallyLoader.getStatus().then(status => {
                        // Update status display
                        importStatusText.textContent = status.syncStatus;
                        importStatusText.className = `status-indicator ${status.syncStatus}`;
                        importRunningStatus.textContent = status.isRunning ? 'Yes' : 'No';

                        if (status.lastSyncTime) {
                            importLastSync.textContent = new Date(status.lastSyncTime).toLocaleString();
                        }

                        // Check if the status message indicates completion
                        if (status.syncStatus &&
                            (status.syncStatus.includes("completed") ||
                             status.syncStatus.toLowerCase() === "idle" ||
                             status.syncStatus.includes("No change in Tally data found"))) {
                            completionDetected = true;
                        }

                        // Stop polling if either the status says not running or a completion message was detected
                        if ((!status.isRunning || completionDetected) && statusCheckInterval) {
                            clearInterval(statusCheckInterval);
                            statusCheckInterval = null;
                            startExportBtn.disabled = false;
                            startExportBtn.textContent = 'Start Import';
                            addLog('Export/import process completed', 'info');
                        } else {
                            // Keep button disabled while running
                            startExportBtn.disabled = true;
                        }
                    }).catch(error => {
                        console.error('Error checking status:', error);
                        // If there's an error, stop polling to prevent continuous errors
                        if (statusCheckInterval) {
                            clearInterval(statusCheckInterval);
                            statusCheckInterval = null;
                            startExportBtn.disabled = false;
                            startExportBtn.textContent = 'Start Import';
                        }
                    });
                }, 5000);
            }
        }
    } else {
        console.error("Tally Loader API not available. Make sure the preload script is properly configured.");
        addLog("Tally Loader API not available.", 'error');
    }

    // Test Tally connection
    testTallyBtn.addEventListener('click', function() {
        testTallyBtn.disabled = true;
        testTallyBtn.textContent = 'Testing...';
        connectionTestResults.innerHTML = 'Testing Tally connection...';

        window.tallyLoader.testTallyConnection().then(result => {
            if (result.success) {
                connectionTestResults.innerHTML = '<div style="color: #27ae60; font-weight: bold;">✓ Tally connection successful!</div>';
                addLog('Tally connection test: Success', 'info');

                // Attempt to populate the company dropdown after successful connection
                populateCompanyDropdown();
            } else {
                connectionTestResults.innerHTML = `<div style="color: #e74c3c; font-weight: bold;">✗ Tally connection failed: ${result.error}</div>`;
                addLog(`Tally connection test failed: ${result.error}`, 'error');
            }
        }).catch(error => {
            connectionTestResults.innerHTML = `<div style="color: #e74c3c; font-weight: bold;">✗ Error: ${error.message}</div>`;
            addLog(`Tally connection test error: ${error.message}`, 'error');
        }).finally(() => {
            testTallyBtn.disabled = false;
            testTallyBtn.textContent = 'Test Tally Connection';
        });
    });

    // Make sure tab navigation works
    // This assumes your existing tab navigation code is already handling the new tab
});

// Update version display
async function updateVersionDisplay() {
    const version = await window.electron.getAppVersion();
    const versionLabel = document.querySelector('.version-label');
    if (versionLabel) {
        versionLabel.textContent = `v ${version}`;
    }
}