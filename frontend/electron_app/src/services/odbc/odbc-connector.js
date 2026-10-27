const odbc = require('odbc');
const EventEmitter = require('events');

class ODBCConnector extends EventEmitter {
    constructor() {
        super();
        this.pool = null;
        this.isConnected = false;
        this.connectionConfig = null;
        this.maxRetries = 3;
        this.retryDelay = 5000; // 5 seconds
        this.cache = new Map();
        this.cacheTimeout = 5 * 60 * 1000; // 5 minutes
        this.connectionCheckInterval = 30 * 1000; // 30 seconds
        this.connectionCheckTimer = null;
    }

    async connect(config) {
        this.connectionConfig = config;
        
        try {
            // Build connection string from config
            const connectionString = this._buildConnectionString(config);
            console.log('Attempting to connect with string:', this._sanitizeConnectionString(connectionString));
            
            // Create a connection pool
            this.pool = await odbc.pool({
                connectionString: connectionString,
                initialSize: 1,
                maxSize: 5,
                incrementSize: 1
            });

            // Set connected state before testing
            this.isConnected = true;
            
            // Test the connection
            try {
                await this.query('SELECT 1');
                this.emit('connection-status-changed', true);
                console.log('Successfully connected to database');
                this.startConnectionCheck();
                return true;
            } catch (error) {
                this.isConnected = false;
                this.emit('connection-status-changed', false);
                throw error;
            }
        } catch (error) {
            console.error('Failed to connect:', error);
            this.isConnected = false;
            this.emit('connection-status-changed', false);
            throw error;
        }
    }

    _buildConnectionString(config) {
        const { driver, server, port, database, username, password } = config;
        
        // Format server and port as 'server,port' like in the Python implementation
        const serverString = `${server},${port}`;
        
        // Build connection string with all necessary parameters to avoid dialogs
        let connectionString = [
            `DRIVER=${driver}`,
            `SERVER=${serverString}`,
            `Port=${port}`,
            `Protocol=TCP`,
            `Uid=${username || 'Administrator'}`,  // Default to Administrator if not provided
            `Pwd=${password || ''}`,               // Empty password if not provided
            `Location=${server}`,
            `AuthenticateMode=Default`,
            `Product=TallyPrime`,
            `AutoConnect=TRUE`,
            `TimeOut=30`
        ].join(';');
        
        // Add database if specified
        if (database) {
            connectionString += `;DATABASE=${database}`;
        }
        
        console.log('Built connection string:', this._sanitizeConnectionString(connectionString));
        return connectionString;
    }

    _sanitizeConnectionString(connectionString) {
        // Remove sensitive information from connection string for logging
        return connectionString
            .replace(/Pwd=[^;]+/i, 'Pwd=***')
            .replace(/Uid=[^;]+/i, 'Uid=***');
    }

    async disconnect() {
        if (this.pool) {
            try {
                await this.pool.close();
                this.isConnected = false;
                this.emit('connection-status-changed', false);
                console.log('Successfully disconnected from database');
                this.stopConnectionCheck();
                this.clearCache();
            } catch (error) {
                console.error('Error disconnecting:', error);
                throw error;
            } finally {
                this.pool = null;
            }
        }
    }

    startConnectionCheck() {
        if (this.connectionCheckTimer) {
            clearInterval(this.connectionCheckTimer);
        }
        
        this.connectionCheckTimer = setInterval(async () => {
            try {
                await this.testConnection();
            } catch (error) {
                console.error('Connection check failed:', error);
                this.emit('connection-status-changed', false);
                await this.reconnect();
            }
        }, this.connectionCheckInterval);
    }

    stopConnectionCheck() {
        if (this.connectionCheckTimer) {
            clearInterval(this.connectionCheckTimer);
            this.connectionCheckTimer = null;
        }
    }

    async testConnection() {
        if (!this.pool) {
            throw new Error('No connection pool available');
        }

        try {
            // Try a simple query to test the connection
            await this.query('SELECT 1');
            return true;
        } catch (error) {
            console.error('Connection test failed:', error);
            this.isConnected = false;
            this.emit('connection-status-changed', false);
            throw error;
        }
    }

    async query(sql, params = [], useCache = false) {
        if (!this.isConnected || !this.pool) {
            throw new Error('Not connected to database');
        }

        // Check cache if enabled
        if (useCache) {
            const cacheKey = `${sql}-${JSON.stringify(params)}`;
            const cached = this.cache.get(cacheKey);
            if (cached && Date.now() - cached.timestamp < this.cacheTimeout) {
                return cached.data;
            }
        }

        try {
            const result = await this.pool.query(sql, params);
            
            // Cache the result if caching is enabled
            if (useCache) {
                const cacheKey = `${sql}-${JSON.stringify(params)}`;
                this.cache.set(cacheKey, {
                    data: result,
                    timestamp: Date.now()
                });
            }
            
            return result;
        } catch (error) {
            console.error('Query error:', error);
            throw error;
        }
    }

    async executeWithRetry(operation) {
        let lastError;
        for (let attempt = 1; attempt <= this.maxRetries; attempt++) {
            try {
                return await operation();
            } catch (error) {
                lastError = error;
                if (attempt < this.maxRetries) {
                    await new Promise(resolve => setTimeout(resolve, this.retryDelay));
                    await this.reconnect();
                }
            }
        }
        throw lastError;
    }

    async reconnect() {
        if (this.connectionConfig) {
            try {
                await this.connect(this.connectionConfig);
                this.emit('connection-status-changed', true);
            } catch (error) {
                this.emit('connection-status-changed', false);
                throw error;
            }
        }
    }

    clearCache() {
        this.cache.clear();
    }

    async getCompanies() {
        if (!this.isConnected || !this.pool) {
            throw new Error('Not connected to database');
        }

        return this.executeWithRetry(async () => {
            try {
                const query = `SELECT $Name FROM Company`;
                const result = await this.query(query);
                let companies = [];

                if (Array.isArray(result)) {
                    companies = result.map(row => {
                        // Log each row to see its structure
                        console.log('Processing row:', row);
                        
                        // Get the name from the $Name property
                        const name = row['$Name'] || row.$Name;
                        
                        // Only return valid names
                        if (name && typeof name === 'string' && name.trim()) {
                            return { Name: name.trim() };
                        }
                        console.warn('Invalid company row:', row);
                        return null;
                    }).filter(Boolean); // Remove null entries
                }
                
                console.log('Final processed companies:', companies);
                return companies;
            } catch (error) {
                console.error('Error fetching companies:', error);
                throw new Error(`Failed to fetch companies: ${error.message}`);
            }
        });
    }

    async getCompanyDetails(companyName) {
        if (!this.isConnected || !this.pool) {
            throw new Error('Not connected to database');
        }

        return this.executeWithRetry(async () => {
            try {
                const tallyQuery = `
                    <ENVELOPE>
                        <HEADER>
                            <VERSION>1</VERSION>
                            <TALLYREQUEST>Export</TALLYREQUEST>
                            <TYPE>Collection</TYPE>
                            <ID>Company Details</ID>
                        </HEADER>
                        <BODY>
                            <DESC>
                                <STATICVARIABLES>
                                    <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                                    <COMPANYNAME>${companyName}</COMPANYNAME>
                                </STATICVARIABLES>
                                <TDL>
                                    <TDLMESSAGE>
                                        <COLLECTION NAME="Company Details" ISMODIFY="No">
                                            <TYPE>Company</TYPE>
                                            <FETCH>*</FETCH>
                                            <FILTER>Name = $$COMPANYNAME</FILTER>
                                        </COLLECTION>
                                    </TDLMESSAGE>
                                </TDL>
                            </DESC>
                        </BODY>
                    </ENVELOPE>`;
                
                const result = await this.query(tallyQuery, [], true);
                console.log('Company details result:', result);
                
                if (Array.isArray(result) && result.length > 0) {
                    return result[0];
                } else if (result && typeof result === 'object') {
                    return result.ENVELOPE?.BODY?.DATA?.COLLECTION || null;
                }
                
                return null;
            } catch (error) {
                console.error('Error fetching company details:', error);
                throw new Error(`Failed to fetch company details: ${error.message}`);
            }
        });
    }

    // Helper method to get ledger list for a company
    async getLedgers(company) {
        try {
            if (!this.isConnected) {
                throw new Error('Not connected to Tally');
            }

            // Construct Tally XML query to fetch ledgers
            const tallyQuery = `
                <ENVELOPE>
                    <HEADER>
                        <TALLYREQUEST>Export Data</TALLYREQUEST>
                    </HEADER>
                    <BODY>
                        <EXPORTDATA>
                            <REQUESTDESC>
                                <REPORTNAME>List of Ledgers</REPORTNAME>
                                <STATICVARIABLES>
                                    <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                                    <COMPANYNAME>${company}</COMPANYNAME>
                                </STATICVARIABLES>
                            </REQUESTDESC>
                            <TDL>
                                <TDLMESSAGE>
                                    <COLLECTION NAME="Ledger List" ISMODIFY="No">
                                        <TYPE>Ledger</TYPE>
                                        <FETCH>*</FETCH>
                                    </COLLECTION>
                                </TDLMESSAGE>
                            </TDL>
                        </EXPORTDATA>
                    </BODY>
                </ENVELOPE>`;
            
            console.log('Executing Tally query for ledgers:', tallyQuery);
            const result = await this.query(tallyQuery, [], true);
            
            // Process the results
            const ledgers = [];
            if (result && result.ENVELOPE?.BODY?.DATA?.COLLECTION) {
                const collection = result.ENVELOPE.BODY.DATA.COLLECTION;
                if (Array.isArray(collection)) {
                    for (const ledger of collection) {
                        if (ledger.NAME) {
                            ledgers.push({
                                name: ledger.NAME,
                                parent: ledger.PARENT || 'Primary',
                                openingBalance: ledger.OPENINGBALANCE || '0.00',
                                closingBalance: ledger.CLOSINGBALANCE || '0.00'
                            });
                        }
                    }
                }
            }
            
            console.log('Found ledgers:', ledgers);
            return ledgers;
        } catch (error) {
            console.error('Error fetching ledgers:', error);
            throw new Error(`Failed to fetch ledgers: ${error.message}`);
        }
    }

    // Helper method to get vouchers
    async getVouchers(company, fromDate, toDate) {
        return this.executeWithRetry(async () => {
            const tallyQuery = `
                <ENVELOPE>
                    <HEADER>
                        <VERSION>1</VERSION>
                        <TALLYREQUEST>Export</TALLYREQUEST>
                        <TYPE>Collection</TYPE>
                        <ID>List of Vouchers</ID>
                    </HEADER>
                    <BODY>
                        <DESC>
                            <STATICVARIABLES>
                                <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                                <COMPANYNAME>${company}</COMPANYNAME>
                                <FROMDATE>${fromDate}</FROMDATE>
                                <TODATE>${toDate}</TODATE>
                            </STATICVARIABLES>
                            <TDL>
                                <TDLMESSAGE>
                                    <COLLECTION NAME="List of Vouchers" ISMODIFY="No">
                                        <TYPE>Voucher</TYPE>
                                        <FETCH>VoucherNumber, Date, VoucherTypeName, PartyLedgerName, Amount</FETCH>
                                        <FILTER>Date BETWEEN $$FROMDATE AND $$TODATE</FILTER>
                                    </COLLECTION>
                                </TDLMESSAGE>
                            </TDL>
                        </DESC>
                    </BODY>
                </ENVELOPE>`;
            
            const result = await this.query(tallyQuery, [], true);
            return result;
        });
    }

    async pushToTally(xml) {
        return this.executeWithRetry(async () => {
            try {
                // Execute the XML import in Tally
                const result = await this.query(xml);
                return {
                    success: true,
                    message: 'XML successfully pushed to Tally',
                    result: result
                };
            } catch (error) {
                console.error('Error pushing XML to Tally:', error);
                throw error;
            }
        });
    }
}

module.exports = ODBCConnector; 