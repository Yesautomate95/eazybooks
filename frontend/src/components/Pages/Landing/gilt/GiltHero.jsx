import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { ArrowDown } from 'lucide-react';
import SelfBalancingLedger from './SelfBalancingLedger';

const EASE_OUT = [0.16, 1, 0.3, 1];

const GiltHero = ({ onGetStarted }) => {
  const reduced = useReducedMotion();

  const fadeUp = {
    hidden: { opacity: 0, y: reduced ? 0 : 24 },
    show: (i) => ({
      opacity: 1,
      y: 0,
      transition: { duration: 0.7, ease: EASE_OUT, delay: 0.1 + 0.09 * i },
    }),
  };

  return (
    <section className="gl-grain relative overflow-hidden" aria-labelledby="hero-heading">
      {/* giant reeded coin edge turning slowly behind the ledger */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-72 top-1/2 -mt-[420px] h-[840px] w-[840px] text-gilt-gold opacity-10"
      >
        <svg className="h-full w-full motion-safe:animate-gl-spin-slow" viewBox="0 0 100 100" fill="none">
          <circle cx="50" cy="50" r="49" stroke="currentColor" strokeWidth="0.5" strokeDasharray="0.8 1.3" />
          <circle cx="50" cy="50" r="44" stroke="currentColor" strokeWidth="0.25" />
        </svg>
      </div>

      <div className="relative z-10 mx-auto grid max-w-7xl items-center gap-12 px-6 pb-20 pt-14 lg:grid-cols-12 lg:gap-8 lg:pb-28 lg:pt-20">
        <motion.div
          className="lg:col-span-5"
          initial="hidden"
          animate="show"
        >
          <motion.p variants={fadeUp} custom={0} className="gl-eyebrow flex items-center gap-2.5">
            <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-gilt-gold" />
            EazyBooks
          </motion.p>

          <motion.h1
            variants={fadeUp}
            custom={1}
            id="hero-heading"
            className="gl-display mt-6 text-gl-hero text-gilt-ink"
          >
            Accounting
            <br />
            Meets <span className="gl-wonk text-gilt-brass">AI</span>
          </motion.h1>

          <motion.p
            variants={fadeUp}
            custom={2}
            className="mt-6 max-w-md font-body text-gl-lead text-gilt-soft"
          >
            Trust, Security, and AI for Chartered Accountants.
          </motion.p>

          <motion.div variants={fadeUp} custom={3} className="mt-10 flex flex-wrap items-center gap-5">
            <button
              type="button"
              onClick={onGetStarted}
              className="gl-gilt-edge relative overflow-hidden rounded-lg bg-gilt-ink px-6 py-3.5 font-body text-[15px] font-medium text-gilt-paper shadow-gl-card transition-transform duration-200 ease-gl-out hover:-translate-y-0.5 active:translate-y-0"
            >
              Get Started
            </button>
            <a
              href="#features"
              className="group inline-flex items-center gap-2 font-body text-[15px] font-medium text-gilt-ink transition-colors duration-200 hover:text-gilt-brass"
            >
              See it in action
              <ArrowDown
                size={16}
                aria-hidden="true"
                className="transition-transform duration-300 ease-gl-out group-hover:translate-y-0.5"
              />
            </a>
          </motion.div>
        </motion.div>

        <motion.div
          className="lg:col-span-7"
          initial={{ opacity: 0, y: reduced ? 0 : 32 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: EASE_OUT, delay: 0.35 }}
        >
          <SelfBalancingLedger className="mx-auto w-full max-w-[640px]" />
        </motion.div>
      </div>
    </section>
  );
};

export default GiltHero;
