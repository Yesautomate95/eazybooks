import React, { useEffect, useRef, useState } from 'react';
import { motion, useInView, useReducedMotion, useScroll, useTransform } from 'framer-motion';
import { ArrowRight, Pause, Play } from 'lucide-react';
import { cn } from '../../../../lib/utils';
import dataChatDemo from '../../../../assets/landing page/data chatbot.mp4';
import uploadDemo from '../../../../assets/landing page/upload demo.mp4';
import advisorDemo from '../../../../assets/landing page/advisor.mp4';
import dataChatPoster from '../../../../assets/landing page/posters/data-chatbot-poster.jpg';
import uploadDemoPoster from '../../../../assets/landing page/posters/upload-demo-poster.jpg';
import advisorPoster from '../../../../assets/landing page/posters/advisor-poster.jpg';

const EASE_OUT = [0.16, 1, 0.3, 1];

const FEATURES = [
  {
    id: 'feature-queries',
    plateNo: '01',
    eyebrow: 'Natural Language Queries',
    title: 'Chat with Your Data',
    body: 'Extract insights from Tally and other accounting software through natural language conversations. Get instant answers with relevant graphs and infographics.',
    cta: 'Try Data Chat',
    video: dataChatDemo,
    poster: dataChatPoster,
    videoLabel:
      'Product demo video: a user asks questions about their Tally data in plain English and the assistant answers with figures, graphs and infographics.',
  },
  {
    id: 'feature-data-entry',
    plateNo: '02',
    eyebrow: 'Data Entry',
    title: 'Automated Data Entry',
    body: 'Simply upload your bank entries PDF/XML and let our AI automatically add them to Tally and other accounting software. Save hours of manual data entry work.',
    cta: 'Try Auto Entry',
    video: uploadDemo,
    poster: uploadDemoPoster,
    videoLabel:
      'Product demo video: a bank statement is uploaded, every transaction is extracted into an editable table, and the entries are pushed to Tally automatically.',
  },
  {
    id: 'feature-locker',
    plateNo: '03',
    eyebrow: 'Document Intelligence',
    title: 'Smart Document Management',
    body: 'Automatically categorize and store documents with AI-powered insights. Chat with your documents using our custom-trained chatbot for instant information retrieval.',
    cta: 'Try Document AI',
    video: advisorDemo,
    poster: advisorPoster,
    videoLabel:
      'Product demo video: documents are uploaded to the locker, categorized automatically, and retrieved by chatting with the document AI.',
  },
];

const makeVariants = (reduced) => ({
  container: {
    hidden: {},
    show: { transition: { staggerChildren: reduced ? 0 : 0.1 } },
  },
  fadeUp: reduced
    ? { hidden: { opacity: 1, y: 0 }, show: { opacity: 1, y: 0 } }
    : {
        hidden: { opacity: 0, y: 24 },
        show: { opacity: 1, y: 0, transition: { duration: 0.7, ease: EASE_OUT } },
      },
});

/* Demo video framed as a book plate: card mat, gilt top edge, mono caption strip */
const VideoPlate = ({ src, poster, videoLabel, plateNo, eyebrow, reduced }) => {
  const plateRef = useRef(null);
  const videoRef = useRef(null);
  const inView = useInView(plateRef, { amount: 0.35 });
  // null = no user choice yet; viewport (and reduced-motion) decide
  const [userPaused, setUserPaused] = useState(null);

  const shouldPlay = userPaused === null ? inView && !reduced : !userPaused && inView;

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    if (shouldPlay) {
      el.play().catch(() => {});
    } else {
      el.pause();
    }
  }, [shouldPlay]);

  return (
    <figure
      ref={plateRef}
      className="gl-gilt-edge gl-grain relative overflow-hidden rounded-xl border border-gilt-hairline bg-gilt-card p-2 shadow-gl-card sm:p-3"
    >
      {/* intrinsic 1280x974 ratio — 16:9 + cover would crop ~26% of the demo;
          z-[2] lifts the frames above the grain overlay (no per-frame blend) */}
      <video
        ref={videoRef}
        src={src}
        poster={poster}
        muted
        loop
        playsInline
        preload="metadata"
        aria-label={videoLabel}
        className="relative z-[2] aspect-[1280/974] w-full rounded-lg"
      />
      <figcaption className="flex items-center justify-between gap-3 px-1 pb-0.5 pt-2 sm:pt-2.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
          Plate № {plateNo} · {eyebrow}
        </span>
        <button
          type="button"
          aria-label={`${shouldPlay ? 'Pause' : 'Play'} demo: ${eyebrow}`}
          onClick={() => setUserPaused(shouldPlay)}
          className="inline-flex min-h-[28px] items-center gap-1.5 rounded-md border border-gilt-hairline px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-gilt-soft transition-colors duration-200 hover:border-gilt-brass hover:text-gilt-brass"
        >
          {shouldPlay ? <Pause size={12} aria-hidden="true" /> : <Play size={12} aria-hidden="true" />}
          {shouldPlay ? 'Pause' : 'Play'}
        </button>
      </figcaption>
    </figure>
  );
};

const FeatureRow = ({ feature, reversed, onTry }) => {
  const reduced = useReducedMotion();
  const rowRef = useRef(null);
  const { container, fadeUp } = makeVariants(reduced);

  // Folio numeral drifts gently against the scroll
  const { scrollYProgress } = useScroll({ target: rowRef, offset: ['start end', 'end start'] });
  const folioY = useTransform(scrollYProgress, [0, 1], [30, -30]);

  return (
    <motion.div
      ref={rowRef}
      id={feature.id}
      variants={container}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: '-80px' }}
      className="grid grid-cols-12 items-center gap-y-10 lg:gap-x-12"
    >
      <motion.div
        variants={fadeUp}
        className={cn('relative col-span-12 lg:col-span-7', reversed && 'lg:order-2')}
      >
        <VideoPlate
          src={feature.video}
          videoLabel={feature.videoLabel}
          plateNo={feature.plateNo}
          eyebrow={feature.eyebrow}
          reduced={reduced}
        />
        <motion.span
          aria-hidden="true"
          style={reduced ? { rotate: -2 } : { y: folioY, rotate: -2 }}
          className={cn(
            'gl-display pointer-events-none absolute -top-9 z-10 select-none text-[clamp(3.5rem,9vw,7rem)] leading-none text-gilt-gold opacity-25 lg:-top-14',
            reversed ? '-right-1 sm:-right-3 lg:-right-7' : '-left-1 sm:-left-3 lg:-left-7'
          )}
        >
          № {feature.plateNo}
        </motion.span>
      </motion.div>

      <div className={cn('col-span-12 self-center lg:col-span-5', reversed && 'lg:order-1')}>
        <motion.p variants={fadeUp} className="gl-eyebrow">
          {feature.eyebrow}
        </motion.p>
        <motion.h3 variants={fadeUp} className="gl-display mt-4 text-gl-h3 text-gilt-ink">
          {feature.title}
        </motion.h3>
        <motion.p variants={fadeUp} className="mt-4 max-w-md font-body text-[15px] leading-relaxed text-gilt-soft">
          {feature.body}
        </motion.p>
        <motion.div variants={fadeUp} className="mt-7">
          <button
            type="button"
            onClick={onTry}
            className="group relative inline-flex items-center gap-2 font-body text-[15px] font-medium text-gilt-brass"
          >
            {feature.cta}
            <ArrowRight
              size={16}
              aria-hidden="true"
              className="transition-transform duration-300 ease-gl-out group-hover:translate-x-1"
            />
            <span
              aria-hidden="true"
              className="absolute -bottom-1 left-0 h-px w-full origin-left scale-x-0 bg-gilt-brass transition-transform duration-300 ease-gl-out group-hover:scale-x-100"
            />
          </button>
        </motion.div>
      </div>
    </motion.div>
  );
};

const GiltFeatures = ({ onTry, className }) => {
  const reduced = useReducedMotion();
  const { container, fadeUp } = makeVariants(reduced);

  return (
    <section
      id="features"
      aria-labelledby="features-heading"
      className={cn('relative overflow-hidden bg-gilt-paper py-24 lg:py-32', className)}
    >
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          variants={container}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: '-80px' }}
        >
          <motion.p variants={fadeUp} className="gl-eyebrow flex items-center gap-2.5">
            <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-gilt-gold" />
            What EazyBooks Does
          </motion.p>
          <motion.h2
            variants={fadeUp}
            id="features-heading"
            className="gl-display mt-6 max-w-3xl text-gl-h2 text-gilt-ink"
          >
            Transform Your Accounting with AI-Powered{' '}
            <span className="gl-wonk text-gilt-brass">Tools</span>
          </motion.h2>
          <motion.p variants={fadeUp} className="mt-5 max-w-2xl font-body text-gl-lead text-gilt-soft">
            Chat with Your Data, Automate Data Entry, and Manage Documents Intelligently
          </motion.p>
          <motion.div variants={fadeUp} aria-hidden="true" className="mt-12 h-px w-full bg-gilt-hairline" />
        </motion.div>

        <div className="mt-20 lg:mt-24">
          {FEATURES.map((feature, i) => (
            <React.Fragment key={feature.id}>
              <FeatureRow feature={feature} reversed={i % 2 === 1} onTry={onTry} />
              {i < FEATURES.length - 1 && (
                <div
                  aria-hidden="true"
                  className="mx-auto my-8 h-12 w-px border-l border-dotted border-gilt-hairline lg:my-10 lg:h-16"
                />
              )}
            </React.Fragment>
          ))}
        </div>
      </div>
    </section>
  );
};

export default GiltFeatures;
