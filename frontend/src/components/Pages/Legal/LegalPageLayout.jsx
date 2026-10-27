import React from 'react';
import { useNavigate } from 'react-router-dom';
import GiltNav from '../Landing/gilt/GiltNav';
import GiltFooter from '../Landing/gilt/GiltFooter';
import useGiltTheme from '../Landing/gilt/useGiltTheme';
import '../../../styles/gilt.css';

// Shared chrome for standalone legal documents (Privacy Policy, EULA, etc.)
// so they read as part of the product rather than a bare text dump.
const LegalPageLayout = ({ title, updated, children }) => {
  const [theme, toggleTheme] = useGiltTheme();
  const navigate = useNavigate();

  return (
    <div className={`gl-root min-h-screen font-sans ${theme === 'dark' ? 'gl-dark' : ''}`}>
      <GiltNav theme={theme} onToggleTheme={toggleTheme} onSignIn={() => navigate('/login')} />
      <main className="mx-auto max-w-3xl px-6 py-24 sm:py-28">
        <p className="gl-eyebrow flex items-center gap-2.5">
          <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-gilt-gold" />
          Legal
        </p>
        <h1 className="gl-display mt-4 text-gl-h2 text-gilt-ink">{title}</h1>
        {updated && (
          <p className="mt-3 font-mono text-[11px] uppercase tracking-[0.14em] text-gilt-soft">
            Last updated: {updated}
          </p>
        )}
        <div className="prose prose-neutral mt-12 max-w-none font-body text-gilt-ink prose-headings:font-display prose-headings:text-gilt-ink prose-a:text-gilt-brass">
          {children}
        </div>
      </main>
      <GiltFooter onGetStarted={() => navigate('/signup')} />
    </div>
  );
};

export default LegalPageLayout;
