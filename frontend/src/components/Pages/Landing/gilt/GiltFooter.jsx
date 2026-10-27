import React, { useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import CoinMark from './CoinMark';

const PRODUCT_LINKS = [
  { label: 'Data Chat', href: '#feature-queries' },
  { label: 'Auto Entry', href: '#feature-data-entry' },
  { label: 'Document AI', href: '#feature-locker' },
  { label: 'Integrations', href: '#features' },
];

// TODO(owner): social profile URLs still need real destinations.
const RESOURCE_LINKS = [
  { label: 'End User Agreement', href: '/end-user-agreement' },
  { label: 'Privacy Policy', href: '/privacy-policy' },
  { label: 'Contact Us', href: '#' },
];
const FOLLOW_LINKS = [
  { label: 'LinkedIn', href: '#' },
  { label: 'X', href: '#', ariaLabel: 'X (formerly Twitter)' },
  { label: 'Instagram', href: '#' },
];

const LinkColumn = ({ heading, links }) => (
  <div>
    <h3 className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-gilt-brass">
      {heading}
    </h3>
    <ul className="mt-5 space-y-3.5">
      {links.map((link) => (
        <li key={link.label}>
          <a
            href={link.href}
            aria-label={link.ariaLabel}
            onClick={link.href === '#' ? (e) => e.preventDefault() : undefined}
            className="font-body text-sm text-gilt-soft transition-colors duration-200 hover:text-gilt-ink"
          >
            {link.label}
          </a>
        </li>
      ))}
    </ul>
  </div>
);

// The closing espresso band: CTA entry, the ledger of links, the final
// rulings, and the wordmark pressed into the bottom edge of the page.
const GiltFooter = ({ onGetStarted }) => {
  const reduced = useReducedMotion();
  const [flips, setFlips] = useState(0);

  return (
    <footer className="gl-dark gl-grain gl-gilt-edge relative overflow-hidden bg-gilt-card">
      <div className="relative z-10 mx-auto max-w-7xl px-6">
        {/* The next entry — closing CTA */}
        <div className="items-end justify-between gap-10 border-b border-gilt-hairline py-20 lg:flex lg:py-24">
          <div>
            <p className="gl-eyebrow flex items-center gap-2.5">
              <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-gilt-gold" />
              The Next Entry
            </p>
            <h2 className="gl-display mt-6 max-w-xl text-gl-h2 text-gilt-ink">
              Let&rsquo;s open <span className="gl-wonk text-gilt-brass">your</span> books.
            </h2>
          </div>
          <div className="mt-10 shrink-0 lg:mt-0">
            <button
              type="button"
              onClick={onGetStarted}
              className="gl-gilt-edge relative overflow-hidden rounded-lg bg-gilt-ink px-6 py-3.5 font-body text-[15px] font-medium text-gilt-paper shadow-gl-card transition-transform duration-200 ease-gl-out hover:-translate-y-0.5 active:translate-y-0"
            >
              Get Started
            </button>
          </div>
        </div>

        {/* Brand + link columns */}
        <div className="grid grid-cols-2 gap-x-8 gap-y-12 py-14 md:grid-cols-4 lg:py-16">
          <div className="col-span-2 md:col-span-1">
            <div className="flex items-center gap-2.5 text-gilt-ink">
              {/* pointer-only easter egg — purely visual, so kept out of the
                  focus order and accessibility tree */}
              <button
                type="button"
                aria-hidden="true"
                tabIndex={-1}
                onClick={() => setFlips((f) => f + 1)}
                className="cursor-pointer rounded-full text-gilt-gold"
              >
                <motion.span
                  className="block"
                  animate={{ rotateY: flips * 360, transformPerspective: 200 }}
                  transition={reduced ? { duration: 0 } : { duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
                >
                  <CoinMark className="block" />
                </motion.span>
              </button>
              <span className="gl-display text-[22px] leading-none">EazyBooks</span>
            </div>
            <p className="mt-5 max-w-[26ch] font-body text-sm leading-relaxed text-gilt-soft">
              Trust, Security, and AI for Chartered Accountants.
            </p>
          </div>

          <nav
            aria-label="Footer"
            className="col-span-2 grid grid-cols-2 gap-x-8 gap-y-12 md:col-span-3 md:grid-cols-3"
          >
            <LinkColumn heading="Products" links={PRODUCT_LINKS} />
            <LinkColumn heading="Resources" links={RESOURCE_LINKS} />
            <LinkColumn heading="Follow" links={FOLLOW_LINKS} />
          </nav>
        </div>

        {/* Final rulings */}
        <div className="flex flex-col items-center justify-between gap-3 border-t border-gilt-hairline py-6 sm:flex-row">
          <p className="font-mono text-[11px] text-gilt-soft">
            Copyright © 2025 Toxic Technologies. All rights reserved.
          </p>
          <p
            className="font-mono text-[11px] tracking-[0.14em] text-gilt-soft"
            title="Errors and Omissions Excepted — the accountant's oldest disclaimer"
          >
            E. &amp; O.E.
            <span className="sr-only"> (Errors and Omissions Excepted)</span>
          </p>
        </div>
      </div>

      {/* The wordmark pressed into the page edge */}
      <div aria-hidden="true" className="pointer-events-none relative select-none">
        <div className="gl-display -mb-[0.24em] whitespace-nowrap text-center text-[clamp(4.5rem,16.5vw,15rem)] leading-[0.85] text-gilt-gold opacity-[0.07]">
          EazyBooks
        </div>
      </div>
    </footer>
  );
};

export default GiltFooter;
