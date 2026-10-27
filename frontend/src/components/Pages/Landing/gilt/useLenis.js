import { useEffect } from 'react';
import Lenis from 'lenis';

// Inertia scrolling for the landing page only. Skipped entirely under
// prefers-reduced-motion (the CSS smooth-scroll fallback is also disabled
// there, so those users get plain native scrolling).
export default function useLenis() {
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;

    const lenis = new Lenis({ duration: 1.05 });

    let raf = requestAnimationFrame(function loop(time) {
      lenis.raf(time);
      raf = requestAnimationFrame(loop);
    });

    // Route in-page anchors through Lenis (native hash jumps would be
    // instant since Lenis disables CSS smooth scroll). The skip link is
    // excluded: keyboard users should jump and move focus natively.
    // Lenis already honors the targets' scroll-margin-top, so no offset.
    const onClick = (event) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      const anchor = event.target.closest('a[href^="#"]');
      if (!anchor || anchor.classList.contains('gl-skip-link')) return;
      const hash = anchor.getAttribute('href');
      if (hash.length < 2) return;
      const target = document.querySelector(hash);
      if (!target) return;
      event.preventDefault();
      window.history.pushState(null, '', hash);
      lenis.scrollTo(target);
      // keep keyboard/SR reading position in sync with the visual jump
      target.setAttribute('tabindex', '-1');
      target.focus({ preventScroll: true });
    };
    document.addEventListener('click', onClick);

    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('click', onClick);
      lenis.destroy();
    };
  }, []);
}
