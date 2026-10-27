const { initializeApp } = require('firebase/app');
const { getAuth, signInWithCustomToken } = require('firebase/auth');
const admin = require('firebase-admin');
const path = require('path');
require('dotenv').config();

let app;
let auth;
let adminApp;
let adminInitialized = false;
let webInitialized = false;

// Initialize Firebase Admin SDK
try {
    const serviceAccountPath = path.join(__dirname, '../../../firebase_key.json');
    adminApp = admin.initializeApp({
        credential: admin.credential.cert(serviceAccountPath)
    }, 'admin');
    adminInitialized = true;
    console.log('Firebase Admin SDK initialized successfully');
} catch (error) {
    console.error('Firebase Admin SDK initialization error:', error);
}

// Initialize Firebase Web SDK
try {
    const firebaseConfig = {
        apiKey: process.env.FIREBASE_API_KEY,
        authDomain: process.env.FIREBASE_AUTH_DOMAIN,
        projectId: process.env.FIREBASE_PROJECT_ID,
        storageBucket: process.env.FIREBASE_STORAGE_BUCKET,
        messagingSenderId: process.env.FIREBASE_MESSAGING_SENDER_ID,
        appId: process.env.FIREBASE_APP_ID
    };

    if (!firebaseConfig.apiKey) {
        console.warn('Firebase Web SDK: FIREBASE_API_KEY not set, skipping initialization');
    } else {
        app = initializeApp(firebaseConfig);
        auth = getAuth(app);
        webInitialized = true;
        console.log('Firebase Web SDK initialized successfully');
    }
} catch (error) {
    console.error('Firebase Web SDK initialization error:', error);
}

// Helper function to create custom token
async function createCustomToken(uid) {
    try {
        return await adminApp.auth().createCustomToken(uid);
    } catch (error) {
        console.error('Error creating custom token:', error);
        throw error;
    }
}

// Helper function to verify ID token
async function verifyIdToken(token) {
    try {
        return await adminApp.auth().verifyIdToken(token);
    } catch (error) {
        console.error('Error verifying token:', error);
        throw error;
    }
}

module.exports = {
    auth,
    adminAuth: adminApp ? adminApp.auth() : null,
    isInitialized: () => webInitialized,
    isAdminInitialized: () => adminInitialized,
    createCustomToken,
    verifyIdToken,
    signInWithCustomToken,
    reinitialize: () => {
        try {
            if (!webInitialized) {
                const firebaseConfig = {
                    apiKey: process.env.FIREBASE_API_KEY,
                    authDomain: process.env.FIREBASE_AUTH_DOMAIN,
                    projectId: process.env.FIREBASE_PROJECT_ID,
                    storageBucket: process.env.FIREBASE_STORAGE_BUCKET,
                    messagingSenderId: process.env.FIREBASE_MESSAGING_SENDER_ID,
                    appId: process.env.FIREBASE_APP_ID
                };

                if (!firebaseConfig.apiKey) {
                    console.warn('Cannot reinitialize: FIREBASE_API_KEY not set');
                    return false;
                }

                app = initializeApp(firebaseConfig);
                auth = getAuth(app);
                webInitialized = true;
            }
            return true;
        } catch (error) {
            console.error('Firebase reinitialization error:', error);
            return false;
        }
    }
};