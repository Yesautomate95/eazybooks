import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { cn } from '../../../../lib/utils';

const EASE_OUT = [0.16, 1, 0.3, 1];

const ITEMS = [
  {
    title: 'Natural Language Processing',
    description: 'Chat with your accounting data for instant insights and visualizations.',
  },
  {
    title: 'Automated Data Entry',
    description: 'Effortlessly import data from bank statements and other sources.',
  },
  {
    title: 'Document Intelligence',
    description: 'Smart categorization and insights from your business documents.',
  },
];

// The lamplit plate page: a full-bleed espresso band (gl-dark re-scopes
// every --gl-* var), with the three capabilities set as a ledger index —
// folio, title, dot leader, entry — closed by the accountant's double rule.
const GiltVibe = ({ className }) => {
  const reduced = useReducedMotion();

  const fadeUp = reduced
    ? { hidden: { opacity: 1, y: 0 }, show: { opacity: 1, y: 0 } }
    : {
        hidden: { opacity: 0, y: 20 },
        show: (i) => ({
          opacity: 1,
          y: 0,
          transition: { duration: 0.65, ease: EASE_OUT, delay: 0.08 * i },
        }),
      };

  return (
    <section
      aria-labelledby="vibe-heading"
      className={cn(
        'gl-dark gl-grain gl-gilt-edge relative overflow-hidden bg-gilt-paper py-24 lg:py-32',
        className
      )}
    >
      {/* the one decorative element: a faint reeded coin edge turning in the corner */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-72 -left-72 h-[560px] w-[560px] text-gilt-gold opacity-[0.07]"
      >
        <svg className="h-full w-full motion-safe:animate-gl-spin-slow" viewBox="0 0 100 100" fill="none">
          <circle cx="50" cy="50" r="49" stroke="currentColor" strokeWidth="0.5" strokeDasharray="0.8 1.3" />
          <circle cx="50" cy="50" r="44" stroke="currentColor" strokeWidth="0.25" />
        </svg>
      </div>

      <div className="relative z-10 mx-auto max-w-5xl px-6">
        <motion.div initial="hidden" whileInView="show" viewport={{ once: true, margin: '-80px' }}>
          <motion.p variants={fadeUp} custom={0} className="gl-eyebrow flex items-center gap-2.5">
            <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-gilt-gold" />
            The Philosophy
          </motion.p>

          <motion.h2
            variants={fadeUp}
            custom={1}
            id="vibe-heading"
            className="gl-display mt-6 text-gl-h2 text-gilt-ink"
          >
            Introducing <span className="gl-wonk text-gilt-brass">Vibe</span>
            <br />
            Accounting
          </motion.h2>
        </motion.div>

        <motion.ol
          role="list"
          className="gl-double-rule mt-14 lg:mt-16"
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: '-60px' }}
        >
          {ITEMS.map((item, i) => (
            <motion.li
              key={item.title}
              variants={fadeUp}
              custom={i}
              className="border-t border-gilt-hairline py-7 lg:flex lg:items-baseline lg:gap-5 lg:py-8"
            >
              <span
                aria-hidden="true"
                className="block font-mono text-xs tracking-[0.14em] text-gilt-brass lg:w-10 lg:shrink-0"
              >
                {String(i + 1).padStart(2, '0')}
              </span>

              <h3 className="gl-display mt-2 text-gl-h3 text-gilt-ink lg:mt-0">{item.title}</h3>

              {/* dot leader, title → entry; hidden when the row stacks */}
              <span
                aria-hidden="true"
                className="hidden min-w-[2rem] flex-1 lg:block"
                style={{ borderBottom: '2px dotted rgb(var(--gl-hairline))' }}
              />

              <p className="mt-3 font-body text-[15px] leading-relaxed text-gilt-soft lg:mt-0 lg:w-[300px] lg:shrink-0">
                {item.description}
              </p>
            </motion.li>
          ))}
        </motion.ol>
      </div>
    </section>
  );
};

export default GiltVibe;
