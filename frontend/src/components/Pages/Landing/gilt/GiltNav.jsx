import React, { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { cn } from '../../../../lib/utils';
import CoinMark from './CoinMark';

const LINKS = [
  { label: 'Data Chat', href: '#feature-queries' },
  { label: 'Auto Entry', href: '#feature-data-entry' },
  { label: 'Document AI', href: '#feature-locker' },
  { label: 'Live Demo', href: '#demo' },
];

const GiltNav = ({ theme, onToggleTheme, onSignIn }) => {
  // Height stays constant (CLS); scrolling only deepens the shadow.
  const [elevated, setElevated] = useState(false);

  useEffect(() => {
    const onScroll = () => setElevated(window.scrollY > 4);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <header
      className={cn(
        'sticky top-0 z-50 border-b border-gilt-hairline bg-gilt-paper',
        elevated && 'shadow-gl-card'
      )}
    >
      <a href="#content" className="gl-skip-link">
        Skip to content
      </a>
      <nav aria-label="Main" className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <a href="/" className="flex items-center gap-2.5 text-gilt-ink">
          <CoinMark className="shrink-0 text-gilt-gold" />
          <span className="gl-display text-[22px] leading-none">EazyBooks</span>
        </a>

        <div className="hidden items-center gap-8 md:flex">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="group relative font-body text-sm font-medium text-gilt-soft transition-colors duration-200 hover:text-gilt-ink"
            >
              {link.label}
              <span
                aria-hidden="true"
                className="absolute -bottom-1 left-0 h-px w-full origin-left scale-x-0 bg-gilt-brass transition-transform duration-300 ease-gl-out group-hover:scale-x-100"
              />
            </a>
          ))}
        </div>

        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={onToggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className="rounded-md p-2 text-gilt-soft transition-colors duration-200 hover:bg-gilt-sunken hover:text-gilt-ink"
          >
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <button
            type="button"
            onClick={onSignIn}
            className="rounded-md border border-gilt-hairline px-4 py-2 font-body text-sm font-medium text-gilt-ink transition-colors duration-200 hover:border-gilt-brass hover:text-gilt-brass"
          >
            Sign In
          </button>
        </div>
      </nav>
    </header>
  );
};

export default GiltNav;
