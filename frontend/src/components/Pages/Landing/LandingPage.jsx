import React, { useEffect, useContext } from 'react';
import { EazyBooksContext } from "../../context/EazyBooksContext";
import LoginModalCA from "../EazyBooksCA/LoginModalCA";
import { useNavigate, useLocation } from 'react-router-dom';
import { cn } from "../../../lib/utils";
import GiltNav from "./gilt/GiltNav";
import GiltHero from "./gilt/GiltHero";
import GiltMarquee from "./gilt/GiltMarquee";
import GiltFeatures from "./gilt/GiltFeatures";
import GiltDemo from "./gilt/GiltDemo";
import GiltVibe from "./gilt/GiltVibe";
import GiltFooter from "./gilt/GiltFooter";
import useGiltTheme from "./gilt/useGiltTheme";
import useLenis from "./gilt/useLenis";
import "../../../styles/gilt.css";

const LandingPage = () => {
  const [theme, toggleTheme] = useGiltTheme();
  useLenis();
  const { isOpen, isAuthenticated } = useContext(EazyBooksContext);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (isAuthenticated) {
      // If user is authenticated and there's a saved location, navigate there
      const from = location.state?.from?.pathname || '/dashboard';
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, location]);

  // Sign In -> /login (email + Google); Get Started / Try X -> /signup.
  // The Google-only LoginModalCA stays mounted for flows that still open it.
  const goToLogin = () => navigate('/login');
  const goToSignup = () => navigate('/signup');

  return (
      <div className={cn('gl-root min-h-screen font-sans', theme === 'dark' && 'gl-dark')}>
        {/* Masthead */}
        <GiltNav
          theme={theme}
          onToggleTheme={toggleTheme}
          onSignIn={goToLogin}
        />

        <main id="content">
          {/* Hero — The Self-Balancing Ledger */}
          <GiltHero onGetStarted={goToSignup} />

          {/* Powered By ticker */}
          <GiltMarquee />

          {/* Features — editorial plates */}
          <GiltFeatures onTry={goToSignup} />

          {/* The Counting Room — interactive sample-document demo */}
          <GiltDemo onGetStarted={goToSignup} />

          {/* Introducing Vibe Accounting — lamplit manifesto band */}
          <GiltVibe />
        </main>

        {/* Footer — the closing espresso band */}
        <GiltFooter onGetStarted={goToSignup} />

        {isOpen && <LoginModalCA/>}
      </div>
  );
};

export default LandingPage;
