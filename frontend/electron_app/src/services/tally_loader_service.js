const fs = require('fs');
const path = require('path');
const { app } = require('electron');
const { spawn } = require('child_process');
const Store = require('electron-store');

class TallyLoaderService {
  constructor() {
    this.isRunning = false;
    this.lastSyncTime = null;
    this.syncStatus = 'idle';
    // In a packaged build the app code lives inside app.asar (read-only), so the
    // loader is shipped as an unpacked extraResource at resources/tally-loader.
    // In dev it sits next to the app root.
    this.tallyLoaderPath = app.isPackaged
      ? path.join(process.resourcesPath, 'tally-loader')
      : path.join(app.getAppPath(), 'tally-loader');
    this.configPath = path.join(app.getPath('userData'), 'tally_config.json');
    this.logsPath = path.join(app.getPath('userData'), 'tally_logs');
    // The loader dir is read-only in a packaged install (Program Files), so the
    // runtime config copy lives in userData, not inside tally-loader.
    this.loaderConfigPath = path.join(this.logsPath, 'loader-config.json');
    this.store = new Store();

    console.log('Paths:');
    console.log(' - App path:', app.getAppPath());
    console.log(' - User data path:', app.getPath('userData'));
    console.log(' - Tally loader path:', this.tallyLoaderPath);
    console.log(' - Config path:', this.configPath);
    console.log(' - Logs path:', this.logsPath);
    
    // Ensure logs directory exists
    if (!fs.existsSync(this.logsPath)) {
      fs.mkdirSync(this.logsPath, { recursive: true });
      console.log('Created logs directory');
    }

    // Default configuration - PostgreSQL credentials from environment variables
    this.configDefaults = {
      database: {
        technology: 'postgres',
        server: process.env.PG_SERVER || '',
        port: parseInt(process.env.PG_PORT || '5432'),
        ssl: true,
        schema: process.env.PG_SCHEMA || 'defaultdb',
        username: process.env.PG_USERNAME || '',
        password: process.env.PG_PASSWORD || '',
        loadmethod: 'insert'
      },
      tally: {
        definition: 'config/tally-export-config.yaml',
        server: 'localhost',
        port: 9000,
        fromdate: '20210401',
        todate: '20250331',
        sync: 'full',
        frequency: 5,
        company: ''
      }
    };

    // Load or create config
    this.loadConfig();
  }

  loadConfig() {
    try {
      if (fs.existsSync(this.configPath)) {
        const configData = fs.readFileSync(this.configPath, 'utf8');
        this.config = JSON.parse(configData);
        console.log('Loaded existing config');
      } else {
        this.config = this.configDefaults;
        fs.writeFileSync(this.configPath, JSON.stringify(this.config, null, 2));
        console.log('Created new config file at:', this.configPath);
      }
    } catch (error) {
      console.error('Error loading config:', error);
      this.config = this.configDefaults;
    }
  }

  saveConfig(newConfig) {
    try {
      // Save the user-provided config
      this.config = { ...this.config, ...newConfig };
      
      // Don't expose Firebase ID in the stored config - it will be added at runtime
      fs.writeFileSync(this.configPath, JSON.stringify(this.config, null, 2));
      return { success: true };
    } catch (error) {
      console.error('Error saving config:', error);
      return { success: false, error: error.message };
    }
  }

  async startTallyExport(options = {}) {
    if (this.isRunning) {
      return { success: false, error: 'Export already in progress' };
    }

    // Per-run overrides for the analytics sync:
    //   options.company — which Tally company to sync (required for tenant scoping)
    //   options.mode    — 'incremental' (default) | 'full' (reconcile)
    // Default to incremental so routine syncs only pull what changed (AlterID
    // delta) instead of re-importing everything each time.
    this._runCompany = options.company || this.config?.tally?.company || '';
    this._runMode = options.mode === 'full' ? 'full' : 'incremental';

    // Persist any other config options provided (excluding our per-run keys).
    const { company, mode, ...rest } = options;
    if (Object.keys(rest).length > 0) {
      this.saveConfig(rest);
    }

    this.isRunning = true;
    this.syncStatus = 'running';

    try {
      const result = await this.runTallyLoader();
      this.lastSyncTime = new Date();
      this.syncStatus = result.success ? 'completed' : 'failed';
      this.isRunning = false;
      return result;
    } catch (error) {
      console.error('Tally export error:', error);
      this.syncStatus = 'failed';
      this.isRunning = false;
      return { success: false, error: error.message || 'Unknown error' };
    }
  }

  async runTallyLoader() {
    try {
      // PostgreSQL configuration from environment variables
      const postgresConfig = {
        technology: 'postgres',
        server: process.env.PG_SERVER || '',
        port: parseInt(process.env.PG_PORT || '5432'),
        ssl: true,
        schema: process.env.PG_SCHEMA || 'defaultdb',
        username: process.env.PG_USERNAME || '',
        password: process.env.PG_PASSWORD || '',
        loadmethod: 'insert'
      };

      const syncMode = this._runMode === 'full' ? 'full' : 'incremental';
      const companyName = this._runCompany || this.config.tally.company || '';
      // Incremental syncs use auto date detection (AlterID-driven); a full
      // reconcile still honours the configured window.
      const isIncremental = syncMode === 'incremental';
      // The incremental definition wires up the AlterID-based diff/delete report.
      const definition = isIncremental
        ? 'config/tally-export-config-incremental.yaml'
        : 'config/tally-export-config.yaml';

      // Create a config that uses our PostgreSQL settings. user.id + company.name
      // are what the loader injects into every row (tenant scoping), so they MUST
      // be set — a blank company would mix data across companies.
      const modifiedConfig = {
        database: postgresConfig,
        tally: {
          definition,
          server: 'localhost',
          port: 9000,
          fromdate: isIncremental ? 'auto' : '20210401',
          todate: isIncremental ? 'auto' : '20250331',
          sync: syncMode,
          frequency: 0,
          company: companyName
        },
        user: {
          id: this.store.get('userUid') || ''
        },
        company: {
          name: companyName
        }
      };
      if (!modifiedConfig.user.id || !companyName) {
        this.isRunning = false;
        return { success: false, error: 'Sync requires a signed-in user and a selected Tally company' };
      }
      
      console.log('Running Tally Loader with config:', JSON.stringify(modifiedConfig, null, 2));
      
      // Ensure logs directory exists (in case it was deleted since constructor ran)
      if (!fs.existsSync(this.logsPath)) {
        fs.mkdirSync(this.logsPath, { recursive: true });
        console.log('Created logs directory that was missing');
      }
      
      // Set up paths for config and logs
      const tempConfigPath = path.join(this.logsPath, 'current_config.json');
      const importLogPath = path.join(this.logsPath, 'import-log.txt');
      const errorLogPath = path.join(this.logsPath, 'error-log.txt');
      
      // Path to the index.mjs file to run
      const indexMjsPath = path.join(this.tallyLoaderPath, 'dist', 'index.mjs');
      
      // Ensure log files exist
      if (!fs.existsSync(importLogPath)) {
        fs.writeFileSync(importLogPath, '');
      }
      if (!fs.existsSync(errorLogPath)) {
        fs.writeFileSync(errorLogPath, '');
      }
      
      // Create temporary config file for the loader
      fs.writeFileSync(tempConfigPath, JSON.stringify(modifiedConfig, null, 2));
      console.log('Created temp config file with correct relative path at:', tempConfigPath);
      
      // Persist the resolved config to a writable location (userData). The loader
      // itself reads CONFIG_FILE; this copy is what testDatabaseConnection reads.
      const configDestPath = this.loaderConfigPath;
      fs.writeFileSync(configDestPath, JSON.stringify(modifiedConfig, null, 2));
      console.log('Updated loader config at:', configDestPath);
      
      return new Promise((resolve, reject) => {
        console.log('Starting loader process with path:', indexMjsPath);

        // Run the loader on Electron's OWN bundled Node (process.execPath +
        // ELECTRON_RUN_AS_NODE=1) instead of a system `node`, so end users
        // don't need Node.js installed. Working dir is the loader root so its
        // relative config/*.yaml paths resolve.
        const child = spawn(process.execPath, [indexMjsPath], {
          env: {
            ...process.env,
            ELECTRON_RUN_AS_NODE: '1',
            CONFIG_FILE: tempConfigPath,
            IMPORT_LOG: importLogPath,
            ERROR_LOG: errorLogPath,
            USER_ID: modifiedConfig.user.id,
            COMPANY_NAME: modifiedConfig.company.name
          },
          stdio: 'pipe',  // Capture all output
          cwd: this.tallyLoaderPath // Set working directory to tally-loader root
        });

        let stdout = '';
        let stderr = '';

        child.stdout.on('data', (data) => {
          const output = data.toString();
          stdout += output;
          console.log(`STDOUT: ${output}`);
        });

        child.stderr.on('data', (data) => {
          const output = data.toString();
          stderr += output;
          console.error(`STDERR: ${output}`);
        });

        child.on('close', (code) => {
          if (code !== 0) {
            console.error(`Tally loader process exited with code ${code}`);
            console.error('STDERR:', stderr);
            
            // Write error to error log
            fs.appendFileSync(errorLogPath, `${new Date().toISOString()} - Error executing loader:\nExit code: ${code}\n${stderr}\n`);
            
            reject({ 
              success: false, 
              error: `Process exited with code ${code}. Error: ${stderr}`
            });
            return;
          }

          console.log('Tally loader executed successfully');
          console.log('STDOUT:', stdout);
          
          resolve({ 
            success: true,
            importLogPath,
            errorLogPath
          });
        });

        child.on('error', (error) => {
          console.error('Failed to start Tally loader process:', error);
          fs.appendFileSync(errorLogPath, `${new Date().toISOString()} - Failed to start loader process:\n${error.message}\n`);
          reject({ success: false, error: error.message });
        });
      });
    } catch (error) {
      console.error('Error setting up tally loader execution:', error);
      return { success: false, error: error.message || 'Unknown error' };
    }
  }

  // Background incremental sync. Runs one immediately, then every
  // intervalMinutes, for the given company — but only when not already running,
  // so it never piles up or hammers Tally. Each tick is incremental (AlterID
  // delta); the loader itself records status/cursor in tally_sync_state.
  startBackgroundSync(company, intervalMinutes = 5) {
    this.stopBackgroundSync();
    if (!company) {
      console.warn('startBackgroundSync called without a company — skipping');
      return;
    }
    this._bgCompany = company;
    const tick = async () => {
      if (this.isRunning) return; // a manual/previous run is still going
      try {
        await this.startTallyExport({ company, mode: 'incremental' });
      } catch (err) {
        console.error('Background incremental sync failed:', err?.error || err?.message || err);
      }
    };
    // Kick one off now, then on the interval.
    tick();
    this._bgTimer = setInterval(tick, Math.max(1, intervalMinutes) * 60 * 1000);
    console.log(`Background Tally sync started for "${company}" every ${intervalMinutes} min`);
  }

  stopBackgroundSync() {
    if (this._bgTimer) {
      clearInterval(this._bgTimer);
      this._bgTimer = null;
      console.log('Background Tally sync stopped');
    }
  }

  async testTallyConnection() {
    try {
      const axios = require('axios');
      const response = await axios.post(
        `http://${this.config.tally.server}:${this.config.tally.port}`,
        `<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>CompanyInfo</ID></HEADER><BODY><DESC></DESC></BODY></ENVELOPE>`,
        { headers: { 'Content-Type': 'text/xml' }, timeout: 5000 }
      );
      
      if (response.status === 200 && response.data) {
        return { success: true, data: response.data };
      } else {
        return { success: false, error: 'Invalid response from Tally' };
      }
    } catch (error) {
      return { success: false, error: error.message };
    }
  }

  async testDatabaseConnection() {
    try {
      const { Pool } = require('pg');
      
      // Read the database settings from the persisted loader config instead of user config
      const configDestPath = this.loaderConfigPath;
      let dbConfig;

      try {
        if (fs.existsSync(configDestPath)) {
          const loaderConfig = JSON.parse(fs.readFileSync(configDestPath, 'utf8'));
          dbConfig = loaderConfig.database;
          console.log('Using database settings from loader config for connection test');
        } else {
          dbConfig = this.config.database;
          console.log('Loader config not found, using user config for connection test');
        }
      } catch (error) {
        console.error('Error reading loader config:', error);
        dbConfig = this.config.database;
      }
      
      console.log('Connecting to database...', {...dbConfig});
      
      // Configure connection with detailed logging
      const pool = new Pool({
        user: dbConfig.username,
        host: dbConfig.server,
        database: dbConfig.schema,
        password: dbConfig.password,
        port: dbConfig.port,
        ssl: dbConfig.ssl ? { rejectUnauthorized: false } : false,
        connectionTimeoutMillis: 10000 // Increase timeout to 10 seconds
      });
      
      // Add error handler to catch connection pool errors
      pool.on('error', (err) => {
        console.error('Unexpected error on idle client', err);
      });
      
      console.log('Database pool created, attempting connection...');
      
      try {
        console.log('Acquiring client from pool...');
        const client = await pool.connect();
        console.log('Client connected successfully!');
        
        console.log('Executing test query...');
        const result = await client.query('SELECT 1 as connection_test');
        console.log('Query executed successfully:', result.rows[0]);
        
        client.release();
        console.log('Connection test successful, client released');
        
        await pool.end();
        console.log('Connection pool closed');
        
        // Log connection success to error log for reference
        const errorLogPath = path.join(this.logsPath, 'error-log.txt');
        fs.appendFileSync(errorLogPath, `${new Date().toISOString()} - Database connection test successful:\nServer: ${dbConfig.server}\nDatabase: ${dbConfig.schema}\n\n`);
        
        return { 
          success: true,
          message: `Connected successfully to ${dbConfig.server}:${dbConfig.port}/${dbConfig.schema}`
        };
      } catch (connError) {
        console.error('Connection error:', connError);
        
        // Log connection error to error log
        const errorLogPath = path.join(this.logsPath, 'error-log.txt');
        fs.appendFileSync(errorLogPath, `${new Date().toISOString()} - Database connection failed:\nServer: ${dbConfig.server}\nDatabase: ${dbConfig.schema}\nError: ${connError.message}\n\n`);
        
        // Try to end the pool even if connection failed
        try {
          await pool.end();
          console.log('Connection pool closed after error');
        } catch (endError) {
          console.error('Error closing pool:', endError);
        }
        
        return { 
          success: false, 
          error: connError.message,
          details: {
            server: dbConfig.server,
            port: dbConfig.port,
            database: dbConfig.schema,
            user: dbConfig.username
          }
        };
      }
    } catch (error) {
      console.error('Fatal error in testDatabaseConnection:', error);
      
      // Log error to error log
      const errorLogPath = path.join(this.logsPath, 'error-log.txt');
      fs.appendFileSync(errorLogPath, `${new Date().toISOString()} - Fatal database connection error:\nError: ${error.message}\n\n`);
      
      return { 
        success: false, 
        error: error.message 
      };
    }
  }

  getStatus() {
    return {
      isRunning: this.isRunning,
      lastSyncTime: this.lastSyncTime,
      syncStatus: this.syncStatus, 
      config: this.config
    };
  }

  readImportLog() {
    const logPath = path.join(this.logsPath, 'import-log.txt');
    if (fs.existsSync(logPath)) {
      return fs.readFileSync(logPath, 'utf8');
    }
    return 'No import log available.';
  }

  readErrorLog() {
    const logPath = path.join(this.logsPath, 'error-log.txt');
    if (fs.existsSync(logPath)) {
      return fs.readFileSync(logPath, 'utf8');
    }
    return 'No errors logged.';
  }
}

module.exports = TallyLoaderService;