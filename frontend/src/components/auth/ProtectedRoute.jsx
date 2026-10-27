import React, { useContext, useEffect } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { EazyBooksContext } from '../context/EazyBooksContext';

const ProtectedRoute = ({ children }) => {
  const { isAuthenticated, checkAuth } = useContext(EazyBooksContext);
  const location = useLocation();

  useEffect(() => {
    if (checkAuth) {
      checkAuth();
    }
  }, [checkAuth]);

  if (!isAuthenticated) {
    // Redirect to the landing page but save the attempted location
    return <Navigate to="/" state={{ from: location }} replace />;
  }

  return children;
};

export default ProtectedRoute; 