import React, { useState } from 'react';
import Marquee from 'react-fast-marquee';
import { Pause, Play } from 'lucide-react';
import OpenAI from '../../../../assets/logos/openai.svg';
import GoogleCloud from '../../../../assets/logos/googlecloud.svg';
import AWS from '../../../../assets/logos/aws-2.svg';
import Azure from '../../../../assets/logos/microsoft-azure.svg';
import Firebase from '../../../../assets/logos/firebase.svg';
import Google from '../../../../assets/logos/google.svg';
import Meta from '../../../../assets/logos/meta-quest-1.svg';
import PostgreSQL from '../../../../assets/logos/postgresql.svg';

const LOGOS = [
  { src: OpenAI, alt: 'OpenAI', link: 'https://openai.com' },
  { src: GoogleCloud, alt: 'Google Cloud', link: 'https://cloud.google.com' },
  { src: AWS, alt: 'AWS', link: 'https://aws.amazon.com' },
  { src: Azure, alt: 'Microsoft Azure', link: 'https://azure.microsoft.com' },
  { src: Firebase, alt: 'Firebase', link: 'https://firebase.google.com' },
  { src: Google, alt: 'Google', link: 'https://google.com' },
  { src: Meta, alt: 'Meta', link: 'https://about.meta.com' },
  { src: PostgreSQL, alt: 'PostgreSQL', link: 'https://www.postgresql.org' },
];

// The "Powered By" strip as a ticker between rules. The moving copy is
// decorative (aria-hidden, untabbable); screen readers and keyboards get
// the equivalent linked list, and the chip stops the motion (WCAG 2.2.2).
const GiltMarquee = () => {
  const [paused, setPaused] = useState(false);

  return (
    <section className="border-y border-gilt-hairline bg-gilt-paper py-10">
      <div className="mx-auto max-w-7xl px-6">
        <div className="flex items-center justify-center gap-3">
          <h2 className="gl-eyebrow text-center">Powered By</h2>
          <button
            type="button"
            aria-label={paused ? 'Play the partner-logo ticker' : 'Pause the partner-logo ticker'}
            onClick={() => setPaused((p) => !p)}
            className="inline-flex min-h-[28px] items-center gap-1 rounded-md border border-gilt-hairline px-2 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-gilt-soft transition-colors duration-200 hover:border-gilt-brass hover:text-gilt-brass"
          >
            {paused ? <Play size={12} aria-hidden="true" /> : <Pause size={12} aria-hidden="true" />}
          </button>
        </div>
        <ul className="sr-only">
          {LOGOS.map((logo) => (
            <li key={logo.alt}>
              <a href={logo.link} target="_blank" rel="noopener noreferrer">
                {logo.alt}
              </a>
            </li>
          ))}
        </ul>

        <div
          aria-hidden="true"
          className="mt-7"
          style={{
            WebkitMaskImage: 'linear-gradient(90deg, transparent, black 10%, black 90%, transparent)',
            maskImage: 'linear-gradient(90deg, transparent, black 10%, black 90%, transparent)',
          }}
        >
          <Marquee speed={40} pauseOnHover gradient={false} autoFill play={!paused}>
            {LOGOS.map((logo) => (
              <a
                key={logo.alt}
                href={logo.link}
                target="_blank"
                rel="noopener noreferrer"
                tabIndex={-1}
                className="mx-10 inline-flex items-center sm:mx-14"
              >
                <img
                  src={logo.src}
                  alt=""
                  height={24}
                  className="gl-logo h-6 w-auto max-w-[120px] object-contain opacity-50 transition-opacity duration-200 hover:opacity-90"
                />
              </a>
            ))}
          </Marquee>
        </div>
      </div>
    </section>
  );
};

export default GiltMarquee;
