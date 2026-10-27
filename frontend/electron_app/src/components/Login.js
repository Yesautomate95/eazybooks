import React, { useEffect, useState } from 'react';
import { signInWithGoogle, signOut, getCurrentUser } from '../services/auth';
import { ipcRenderer } from 'electron';

const Login = () => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check for stored user data
    const checkUser = async () => {
      try {
        const storedUser = await ipcRenderer.invoke('get-user-data');
        if (storedUser) {
          setUser(storedUser);
        }
      } catch (error) {
        console.error('Error checking user:', error);
      }
      setLoading(false);
    };

    checkUser();
  }, []);

  const handleGoogleSignIn = async () => {
    try {
      const user = await signInWithGoogle();
      setUser(user);
    } catch (error) {
      console.error('Sign in error:', error);
    }
  };

  const handleSignOut = async () => {
    try {
      await signOut();
      setUser(null);
    } catch (error) {
      console.error('Sign out error:', error);
    }
  };

  if (loading) {
    return <div>Loading...</div>;
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      {user ? (
        <div className="text-center">
          <img
            src={user.photoURL}
            alt={user.displayName}
            className="w-20 h-20 rounded-full mx-auto mb-4"
          />
          <h2 className="text-2xl font-bold mb-2">{user.displayName}</h2>
          <p className="text-gray-600 mb-4">{user.email}</p>
          <button
            onClick={handleSignOut}
            className="bg-red-500 hover:bg-red-600 text-white font-bold py-2 px-4 rounded"
          >
            Sign Out
          </button>
        </div>
      ) : (
        <div className="text-center">
          <h1 className="text-3xl font-bold mb-8">Welcome to EazyBooks</h1>
          <button
            onClick={handleGoogleSignIn}
            className="bg-white hover:bg-gray-100 text-gray-800 font-semibold py-2 px-4 border border-gray-400 rounded shadow flex items-center"
          >
            <img
              src="https://www.google.com/favicon.ico"
              alt="Google"
              className="w-5 h-5 mr-2"
            />
            Sign in with Google
          </button>
        </div>
      )}
    </div>
  );
};

export default Login; 