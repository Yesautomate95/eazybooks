import { auth, googleProvider, signInWithPopup } from '../firebase';
import { ipcRenderer } from 'electron';

export const signInWithGoogle = async () => {
  try {
    const result = await signInWithPopup(auth, googleProvider);
    const user = result.user;
    
    // Store user data in electron-store
    ipcRenderer.send('store-user-data', {
      uid: user.uid,
      email: user.email,
      displayName: user.displayName,
      photoURL: user.photoURL
    });
    
    return user;
  } catch (error) {
    console.error('Google sign-in error:', error);
    throw error;
  }
};

export const signOut = async () => {
  try {
    // Clear stored user data
    ipcRenderer.send('clear-user-data');
  } catch (error) {
    console.error('Sign out error:', error);
    throw error;
  }
};

export const getCurrentUser = () => {
  return auth.currentUser;
}; 