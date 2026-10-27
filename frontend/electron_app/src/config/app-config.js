const crypto = require('crypto');
const Store = require('electron-store');
const path = require('path');
const fs = require('fs');

// Delete existing config file if it exists
try {
    const configPath = path.join(process.env.APPDATA || (process.platform == 'darwin' ? process.env.HOME + '/Library/Preferences' : process.env.HOME + "/.local/share"), 'tally-connector-config.json');
    if (fs.existsSync(configPath)) {
        fs.unlinkSync(configPath);
        console.log('Deleted existing config file');
    }
} catch (error) {
    console.error('Error deleting config file:', error);
}

// Encryption key derivation
function deriveKey(password, salt) {
    return crypto.pbkdf2Sync(password, salt, 100000, 32, 'sha256');
}

// Encryption functions
function encrypt(text, key) {
    const iv = crypto.randomBytes(16);
    const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
    let encrypted = cipher.update(text, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    const authTag = cipher.getAuthTag();
    return {
        iv: iv.toString('hex'),
        encryptedData: encrypted,
        authTag: authTag.toString('hex')
    };
}

function decrypt(encryptedData, key, iv, authTag) {
    const decipher = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(iv, 'hex'));
    decipher.setAuthTag(Buffer.from(authTag, 'hex'));
    let decrypted = decipher.update(encryptedData, 'hex', 'utf8');
    decrypted += decipher.final('utf8');
    return decrypted;
}

const defaultConfig = {
    odbc: {
        driver: '{Tally ODBC Driver64}',
        server: 'localhost',
        port: '9000',
        database: '',  // Optional, will be set when connecting to specific company
        autoConnect: false,
        retryAttempts: 3,
        retryDelay: 5000
    },
    api: {
        port: 8080,
        host: 'localhost'
    },
    security: {
        allowedOrigins: ['http://localhost:3000'],
        requireAuth: false,
        jwtSecret: crypto.randomBytes(32).toString('hex'),
        encryptionKey: crypto.randomBytes(32).toString('hex'),
        rateLimit: {
            windowMs: 15 * 60 * 1000, // 15 minutes
            max: 100 // limit each IP to 100 requests per windowMs
        }
    },
    autoStart: true,
    minimizeToTray: true,
    logLevel: 'info',
    cache: {
        enabled: true,
        timeout: 5 * 60 * 1000 // 5 minutes
    }
};

// Initialize store with only the essentials
const store = new Store({
    name: 'tally-connector-config',
    defaults: defaultConfig
});

// Immediately set the default config
store.store = defaultConfig;

function loadConfig() {
    try {
        // Try to get stored config
        let storedConfig;
        try {
            storedConfig = store.get('config');
        } catch (error) {
            console.error('Error reading config, resetting to default:', error);
            // If there's an error reading the config, delete it and return default
            try {
                store.delete('config');
            } catch (deleteError) {
                console.error('Error deleting corrupted config:', deleteError);
            }
            store.set('config', defaultConfig);
            return defaultConfig;
        }
        
        if (!storedConfig) {
            // If no config exists, save and return default
            store.set('config', defaultConfig);
            return defaultConfig;
        }
        
        // Validate the stored config
        if (typeof storedConfig !== 'object' || Array.isArray(storedConfig)) {
            console.error('Invalid config format, resetting to default');
            store.set('config', defaultConfig);
            return defaultConfig;
        }
        
        // Merge stored config with default config to ensure all fields exist
        const mergedConfig = {
            ...defaultConfig,
            ...storedConfig,
            odbc: {
                ...defaultConfig.odbc,
                ...(storedConfig.odbc || {})
            },
            api: {
                ...defaultConfig.api,
                ...(storedConfig.api || {})
            },
            security: {
                ...defaultConfig.security,
                ...(storedConfig.security || {})
            },
            cache: {
                ...defaultConfig.cache,
                ...(storedConfig.cache || {})
            }
        };
        
        // Update stored config with merged config
        store.set('config', mergedConfig);
        
        return mergedConfig;
    } catch (error) {
        console.error('Error in loadConfig:', error);
        return defaultConfig;
    }
}

function saveConfig(config) {
    try {
        // Validate config before saving
        const validation = validateConfig(config);
        if (!validation.isValid) {
            throw new Error(`Invalid configuration: ${validation.errors.join(', ')}`);
        }

        // Encrypt sensitive data
        const encryptedConfig = {
            ...config,
            security: {
                ...config.security,
                password: config.security.password ? 
                    encrypt(config.security.password, Buffer.from(config.security.encryptionKey, 'hex')).encryptedData : 
                    ''
            }
        };

        store.set('config', encryptedConfig);
        return true;
    } catch (error) {
        console.error('Error saving config:', error);
        return false;
    }
}

function validateConfig(config) {
    const errors = [];
    
    // Validate ODBC settings
    if (!config.odbc.driver) {
        errors.push('ODBC driver name is required');
    }
    if (!config.odbc.server) {
        errors.push('ODBC server address is required');
    }
    if (!config.odbc.port || isNaN(parseInt(config.odbc.port))) {
        errors.push('Valid ODBC port number is required');
    }
    
    // Validate API settings
    if (!config.api.port || isNaN(parseInt(config.api.port))) {
        errors.push('Valid API port number is required');
    }
    if (!config.api.host) {
        errors.push('API host address is required');
    }
    
    // Validate security settings
    if (!Array.isArray(config.security.allowedOrigins)) {
        errors.push('Allowed origins must be an array');
    }
    if (config.security.requireAuth) {
        if (!config.security.username) {
            errors.push('Username is required when authentication is enabled');
        }
        if (!config.security.password) {
            errors.push('Password is required when authentication is enabled');
        }
        if (!config.security.jwtSecret) {
            errors.push('JWT secret is required when authentication is enabled');
        }
    }
    
    // Validate cache settings
    if (config.cache.enabled && (!config.cache.timeout || config.cache.timeout < 0)) {
        errors.push('Valid cache timeout is required when caching is enabled');
    }
    
    return {
        isValid: errors.length === 0,
        errors
    };
}

module.exports = {
    loadConfig,
    saveConfig,
    validateConfig,
    defaultConfig
}; 