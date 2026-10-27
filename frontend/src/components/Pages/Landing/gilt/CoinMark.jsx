import React from 'react';

// The brand orbit mark re-cut as a minted coin: reeded outer edge,
// inner ring, four crosshair ticks, solid center. Inherits currentColor.
const CoinMark = ({ size = 26, className }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 32 32"
    fill="none"
    aria-hidden="true"
    className={className}
  >
    <circle cx="16" cy="16" r="15" stroke="currentColor" strokeWidth="1.1" strokeDasharray="1.4 2.1" />
    <circle cx="16" cy="16" r="10.2" stroke="currentColor" strokeWidth="1.2" />
    <circle cx="16" cy="16" r="3.4" fill="currentColor" />
    <path
      d="M16 2.6v2.2M16 27.2v2.2M2.6 16h2.2M27.2 16h2.2"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
    />
  </svg>
);

export default CoinMark;
