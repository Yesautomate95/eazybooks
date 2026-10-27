/**
 * Consolidated auth service - single source of truth for authentication.
 * Replaces: src/auth/auth.js, src/components/auth/auth.js, src/components/auth/firebase-auth.js
 */
import { eazybooksAuth } from '../components/auth/firebase-config';

const TOKEN_KEY = 'jwt_token';
const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

// ── Token Management ──

export const getToken = () => {
    return localStorage.getItem(TOKEN_KEY);
};

export const storeToken = (token) => {
    localStorage.setItem(TOKEN_KEY, token);
};

export const removeToken = () => {
    localStorage.removeItem(TOKEN_KEY);
};

// ── Token Validation ──

export const isTokenValid = () => {
    const token = getToken();
    if (!token) return false;

    try {
        const decoded = JSON.parse(atob(token.split('.')[1]));
        const expirationTime = decoded.exp * 1000;
        return expirationTime > Date.now();
    } catch {
        return false;
    }
};

const isTokenExpiringSoon = () => {
    const token = getToken();
    if (!token) return false;

    try {
        const decoded = JSON.parse(atob(token.split('.')[1]));
        const expirationTime = decoded.exp * 1000;
        const timeUntilExpiry = expirationTime - Date.now();
        return timeUntilExpiry > 0 && timeUntilExpiry < 5 * 60 * 1000; // 5 minutes
    } catch {
        return false;
    }
};

// ── Firebase Auth ──

export const checkAuthentication = () => {
    return new Promise((resolve, reject) => {
        const unsubscribe = eazybooksAuth.onAuthStateChanged(user => {
            unsubscribe();
            resolve(user);
        }, reject);
    });
};

export const getIdToken = async () => {
    const user = eazybooksAuth.currentUser;
    if (user) {
        return user.getIdToken();
    }
    throw new Error('No authenticated user found.');
};

// ── Token Refresh ──

export const refreshToken = async () => {
    const token = getToken();
    if (!token) {
        logout();
        return null;
    }

    try {
        const response = await fetch(`${API_URL}/ca/auth/refresh-token`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });

        if (!response.ok) {
            throw new Error('Failed to refresh token');
        }

        const data = await response.json();
        storeToken(data.token);

        // Set CloudFront cookies if provided
        if (data.signed_cookies) {
            const Cookies = require('js-cookie');
            Object.entries(data.signed_cookies).forEach(([key, value]) => {
                Cookies.set(key, value, { secure: true, sameSite: 'Lax' });
            });
        }

        return data.token;
    } catch (error) {
        console.error('Error refreshing token:', error);
        logout();
        return null;
    }
};

// ── Auth Status Check ──

export const checkAuthStatus = () => {
    const token = getToken();
    if (!token) return;

    if (!isTokenValid()) {
        logout();
        return;
    }

    if (isTokenExpiringSoon()) {
        refreshToken();
    }
};

// ── Logout ──

export const logout = (shouldRedirect = true) => {
    // Clear only auth-related items
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem('username');
    localStorage.removeItem('email');
    localStorage.removeItem('userPhoto');

    // Clear session storage
    sessionStorage.clear();

    // Clear cookies
    document.cookie.split(';').forEach((c) => {
        document.cookie = c
            .replace(/^ +/, '')
            .replace(/=.*/, '=;expires=' + new Date().toUTCString() + ';path=/');
    });

    // Firebase sign out (fire and forget)
    try {
        eazybooksAuth.signOut();
    } catch {
        // Ignore firebase signout errors
    }

    if (shouldRedirect) {
        window.location.href = '/';
    }
};

// ── Auto-refresh timer ──
let _refreshInterval = null;

export const startAutoRefresh = () => {
    stopAutoRefresh();
    _refreshInterval = setInterval(checkAuthStatus, 300000); // 5 minutes
};

export const stopAutoRefresh = () => {
    if (_refreshInterval) {
        clearInterval(_refreshInterval);
        _refreshInterval = null;
    }
};

// Start auto-refresh on import
startAutoRefresh();
