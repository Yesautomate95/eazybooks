import * as React from "react";
import { cn } from "../../lib/utils";

/**
 * CoinDial — the Gilt Ledger "coin-edge" spinner.
 *
 * A minted brass coin whose milled rim turns while a connection is being
 * established or recovered. Reserved for transitional states (connecting /
 * reconnecting); it is intentionally NOT red. Visual is defined by the
 * `.gl-coin-dial` primitive in styles/gilt.css and respects reduced-motion.
 */
export const CoinDial = ({ size = 14, className }) => (
    <span
        className={cn("gl-coin-dial", className)}
        style={{ width: size, height: size }}
        aria-hidden="true"
    />
);

export default CoinDial;
