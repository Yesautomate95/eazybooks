/**
 * DEPRECATED: Import from 'services/auth' instead.
 * This file re-exports for backwards compatibility.
 */
export {
    getToken,
    storeToken,
    removeToken,
    logout,
    isTokenValid,
} from '../services/auth';

// Legacy alias
export { isTokenValid as isAuthenticated } from '../services/auth';
