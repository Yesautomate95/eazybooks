import { initializeApp } from "firebase/app";
import { getAuth, getIdToken } from "firebase/auth";

// Add your Firebase project configuration here
const firebaseConfig = {
  apiKey: "AIzaSyDaDoJnNmyIZGAJP9T2OQNEbOqE0Ikf2ss",
  authDomain: "aureus-fa0c8.firebaseapp.com",
  projectId: "aureus-fa0c8",
  storageBucket: "aureus-fa0c8.firebasestorage.app",
  messagingSenderId: "19595158585",
  appId: "1:19595158585:web:4c3ca815f20f04acfd19e8",
  measurementId: "G-VNJ03S9SG6"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);

const auth = getAuth(app);

console.log("Auth Initialized:", auth);

async function testTokenRefresh() {
    const user = auth.currentUser;
    console.log(user)
    if (user) {
        const token = await getIdToken(user, true); // Force refresh
        console.log("Refreshed Token:", token);
    } else {
        console.log("No user is currently signed in.");
    }
}

testTokenRefresh();
