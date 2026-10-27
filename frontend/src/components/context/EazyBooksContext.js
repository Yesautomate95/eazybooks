import React, { createContext, useState, useCallback } from 'react'
import { logout, isTokenValid } from '../../services/auth';

// Create the Auth Context
export const EazyBooksContext = createContext();

export const EazyBooksProvider = ({children}) => {
    const [isOpen, setIsOpen] = useState(false);
    const [user, setUser] = useState(null);
    const [isAuthenticated, setIsAuthenticated] = useState(() => {
        return isTokenValid();
    });

    const openModal = () => setIsOpen(true);
    const closeModal = () => setIsOpen(false);

    const handleLogout = useCallback(() => {
        setUser(null);
        setIsAuthenticated(false);
        logout(false);
    }, []);

    const checkAuth = useCallback(async () => {
        const valid = isTokenValid();
        setIsAuthenticated(valid);

        if (!valid) {
            handleLogout();
            return false;
        }
        return true;
    }, [handleLogout]);

    return (
        <EazyBooksContext.Provider value={{
            user,
            setUser,
            openModal,
            closeModal,
            isOpen,
            handleLogout,
            isAuthenticated,
            setIsAuthenticated,
            checkAuth
        }}>
            {children}
        </EazyBooksContext.Provider>
    );
};
