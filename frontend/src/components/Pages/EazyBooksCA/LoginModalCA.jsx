import React, { useState, useContext } from "react";
import { Button } from "../../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../../ui/dialog";
import { Chrome, Loader2 } from "lucide-react";
import { GoogleAuthProvider, signInWithPopup } from 'firebase/auth';
import { eazybooksAuth } from '../../auth/firebase-config';
import { useNavigate } from 'react-router-dom';
import Cookies from 'js-cookie';
import { EazyBooksContext } from '../../context/EazyBooksContext';
import { authApi } from '../../../services/api';

const LoginModalCA = () => {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const navigate = useNavigate();
  const { isOpen, setUser, closeModal } = useContext(EazyBooksContext);

  const handleSignInWithGoogle = async () => {
    console.log('[Login] Starting Google sign-in...');
    try {
      setIsLoading(true);
      setError(null);
      const provider = new GoogleAuthProvider();
      console.log('[Login] Opening Google popup...');
      const result = await signInWithPopup(eazybooksAuth, provider);
      const user = result.user;
      console.log('[Login] Firebase auth successful, user:', user.email);
      setUser(user);

      // Get token and make backend call
      const googleToken = await user.getIdToken(true);
      console.log('[Login] Got Firebase ID token, calling backend...');

      // Send token to backend for authentication
      const data = await authApi.googleSignIn(googleToken);
      console.log('[Login] Backend response:', data);

      if (!data.user || !data.jwt_token) {
        throw new Error('Invalid response from server. Please try again.');
      }

      Cookies.set('CloudFront-Key-Pair-Id', data.signed_cookies['CloudFront-Key-Pair-Id'], { secure: true, sameSite: 'Lax' });
      Cookies.set('CloudFront-Policy', data.signed_cookies['CloudFront-Policy'], { secure: true, sameSite: 'Lax' });
      Cookies.set('CloudFront-Signature', data.signed_cookies['CloudFront-Signature'], { secure: true, sameSite: 'Lax' });

      // Store user data
      localStorage.setItem('firebase_token', googleToken);
      localStorage.setItem('jwt_token', data.jwt_token);
      localStorage.setItem('username', data.user.name);
      localStorage.setItem('email', user.email);
      localStorage.setItem('userPhoto', data.user.photo_url);

      // Close modal and navigate
      closeModal();
      navigate('/dashboard');
    } catch (err) {
      console.error("Error during sign in:", err);
      setError(err.message || 'An error occurred during sign in. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={closeModal}>
      <DialogContent className="sm:max-w-md bg-gilt-card border border-gilt-hairline gl-gilt-edge">
        <div className="flex flex-col items-center gap-4">
          <div className="flex size-20 shrink-0 items-center justify-center rounded-full bg-gilt-sunken border border-gilt-hairline p-4">
            <svg
              className="size-10 text-gilt-brass"
              viewBox="0 0 24 24"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M12 2L2 7L12 12L22 7L12 2Z"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M2 17L12 22L22 17"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M2 12L12 17L22 12"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <DialogHeader className="space-y-2 text-center">
            <DialogTitle className="gl-display text-2xl text-gilt-ink">Welcome to EazyBooks</DialogTitle>
            <DialogDescription className="text-base text-gilt-soft">
              Sign in to access your account and manage your content
            </DialogDescription>
          </DialogHeader>
        </div>

        <div className="space-y-4">
          <Button 
            variant="outline" 
            className="w-full h-12 relative bg-gilt-card border-gilt-hairline text-gilt-ink hover:border-gilt-brass hover:text-gilt-brass transition-colors"
            onClick={handleSignInWithGoogle}
            disabled={isLoading}
          >
            {isLoading ? (
              <div className="flex items-center justify-center gap-2">
                <Loader2 className="h-5 w-5 animate-spin" />
                <span>Signing in...</span>
              </div>
            ) : (
              <div className="flex items-center justify-center gap-2">
                <Chrome className="h-5 w-5" />
                <span>Continue with Google</span>
              </div>
            )}
          </Button>
          
          {error && (
            <div className="text-sm text-gilt-debit text-center bg-gilt-sunken p-3 rounded-md border border-gilt-debit/40">
              {error}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default LoginModalCA; 