import { initializeApp } from "firebase/app";
import { getFirestore } from "firebase/firestore";
import {getAuth,onIdTokenChanged,getIdToken, onAuthStateChanged } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyDaDoJnNmyIZGAJP9T2OQNEbOqE0Ikf2ss",
  authDomain: "aureus-fa0c8.firebaseapp.com",
  projectId: "aureus-fa0c8",
  storageBucket: "aureus-fa0c8.firebasestorage.app",
  messagingSenderId: "19595158585",
  appId: "1:19595158585:web:4c3ca815f20f04acfd19e8",
  measurementId: "G-VNJ03S9SG6",
};

// Initialize Firebase App
const firebaseEazyBooksApp = initializeApp(firebaseConfig);

// Initialize Firebase Auth
const eazybooksAuth = getAuth(firebaseEazyBooksApp);

export { eazybooksAuth };
