import React, { useState, useEffect, useContext } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { signInWithPopup, GoogleAuthProvider } from 'firebase/auth';
import { eazybooksAuth } from '../../auth/firebase-config';
import { authApi } from '../../../services/api';
import { EazyBooksContext } from '../../context/EazyBooksContext';
import { cn } from '../../../lib/utils';
import useGiltTheme from '../Landing/gilt/useGiltTheme';
import CoinMark from '../Landing/gilt/CoinMark';
import '../../../styles/gilt.css';

const Login = () => {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [loading, setLoading] = useState(false);
    const { setIsAuthenticated } = useContext(EazyBooksContext);
    const [error, setError] = useState('');
    const [hasExistingToken, setHasExistingToken] = useState(false);
    const navigate = useNavigate();
    const [theme] = useGiltTheme();
    const reduced = useReducedMotion();

    useEffect(() => {
        const token = localStorage.getItem('jwt_token');
        setHasExistingToken(!!token);
    }, []);

    // Check if opened from Electron app
    const urlParams = new URLSearchParams(window.location.search);
    const isFromElectron = urlParams.get('source') === 'electron';

    const handleEmailSubmit = async (e) => {
        e.preventDefault();
        setError('');

        if (!email || !password) {
            setError('Please enter both email and password.');
            return;
        }

        try {
            setLoading(true);
            const data = await authApi.emailSignIn(email, password);

            localStorage.setItem('jwt_token', data.jwt_token);
            if (data.user?.name) {
                localStorage.setItem('username', data.user.name);
            }
            const userEmail = data.user?.email || email;
            if (userEmail) {
                localStorage.setItem('email', userEmail);
            }
            setIsAuthenticated(true);

            // Redirect to electron app or dashboard
            if (isFromElectron) {
                window.location.href = `eazybooks://auth?token=${data.jwt_token}`;
                showCountdownAndClose();
            } else {
                navigate('/dashboard');
            }
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const showCountdownAndClose = () => {
        // Create overlay
        const overlay = document.createElement('div');
        overlay.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.95);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 10000;
        `;

        overlay.innerHTML = `
            <div style="text-align: center; color: white; font-family: system-ui, sans-serif;">
                <div style="font-size: 48px; margin-bottom: 20px;">✓</div>
                <h2 style="font-size: 24px; margin-bottom: 10px;">Login Successful!</h2>
                <p style="font-size: 16px; color: #aaa; margin-bottom: 30px;">
                    Opening EazyBooks desktop app...
                </p>
                <p style="font-size: 14px; color: #888; margin-bottom: 20px;">
                    If prompted, click "Open EazyBooks"
                </p>
                <p id="countdown-text" style="font-size: 14px; color: #666; margin-bottom: 20px;">
                    This window will close in 5 seconds...
                </p>
                <button id="close-btn" style="
                    background: #333;
                    color: white;
                    border: 1px solid #555;
                    padding: 10px 30px;
                    border-radius: 6px;
                    cursor: pointer;
                    font-size: 14px;
                ">Close this window</button>
            </div>
        `;

        document.body.appendChild(overlay);

        // Add close button handler
        document.getElementById('close-btn').onclick = () => {
            window.close();
            // If window.close() doesn't work, show a message
            setTimeout(() => {
                document.getElementById('countdown-text').textContent =
                    'Please close this tab manually and return to the EazyBooks app.';
            }, 500);
        };

        let secondsLeft = 5;
        const countdownText = document.getElementById('countdown-text');

        const countdownInterval = setInterval(() => {
            secondsLeft--;
            if (secondsLeft <= 0) {
                clearInterval(countdownInterval);
                window.close();
                // If window.close() doesn't work, update message
                setTimeout(() => {
                    countdownText.textContent =
                        'Please close this tab manually and return to the EazyBooks app.';
                }, 500);
            } else {
                countdownText.textContent = `This window will close in ${secondsLeft} seconds...`;
            }
        }, 1000);
    };

    const handleGoogleSignIn = async () => {
        try {
            setLoading(true);
            setError('');
            const provider = new GoogleAuthProvider();
            const result = await signInWithPopup(eazybooksAuth, provider);
            const user = result.user;

            const googleToken = await user.getIdToken(true);
            const data = await authApi.googleSignIn(googleToken);

            if (!data.user || !data.jwt_token) {
              throw new Error('Invalid response from server. Please try again.');
            }
            localStorage.setItem('jwt_token', data.jwt_token);
            if (data.user?.name) {
                localStorage.setItem('username', data.user.name);
            }
            if (data.user?.email) {
                localStorage.setItem('email', data.user.email);
            }
            setIsAuthenticated(true);

            // Redirect to Electron app or web dashboard
            if (isFromElectron) {
                window.location.href = `eazybooks://auth?token=${data.jwt_token}`;
                showCountdownAndClose();
            } else {
                navigate('/dashboard');
            }

        } catch (error) {
            console.error('Error:', error);
            setError(error.message);
        } finally {
            setLoading(false);
        }
    };

    const handleTokenApproval = (approved) => {
        if (approved) {
            const token = localStorage.getItem('jwt_token');
            window.location.href = `eazybooks://auth?token=${token}`;
            showCountdownAndClose();
        } else {
            setHasExistingToken(false);
        }
    };

    return (
        <div className={cn('gl-root gl-grain min-h-screen flex items-center justify-center bg-gilt-paper px-4 py-12 sm:px-6', theme === 'dark' && 'gl-dark')}>
            <div className="relative z-10 w-full max-w-[420px]">
                <div className="text-center mb-8">
                    <Link to="/" className="inline-flex items-center justify-center gap-2.5">
                        <CoinMark size={26} className="text-gilt-gold" />
                        <h1 className="gl-display text-[22px] text-gilt-ink">
                            EazyBooks
                        </h1>
                    </Link>
                    <p className="mt-3 font-body text-gl-lead text-gilt-soft">
                        Sign in to continue
                    </p>
                    <div aria-hidden="true" className="gl-double-rule mx-auto mt-5 w-10" />
                </div>

                <motion.div
                    initial={reduced ? false : { opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
                    className="gl-gilt-edge rounded-xl border border-gilt-hairline bg-gilt-card p-6 shadow-gl-card sm:p-8"
                >
                    {hasExistingToken ? (
                        <div className="space-y-4">
                            <p className="mb-4 text-center font-body text-[15px] text-gilt-ink">
                                We found an existing session. Would you like to continue with it?
                            </p>
                            <div className="flex space-x-4">
                                <button
                                    onClick={() => handleTokenApproval(true)}
                                    className="gl-gilt-edge relative flex-1 overflow-hidden rounded-lg bg-gilt-ink px-4 py-3 font-body text-[15px] font-medium text-gilt-paper shadow-gl-card transition-transform duration-200 ease-gl-out hover:-translate-y-0.5 active:translate-y-0"
                                    disabled={loading}
                                >
                                    Yes, Continue
                                </button>
                                <button
                                    onClick={() => handleTokenApproval(false)}
                                    className="flex-1 rounded-md border border-gilt-hairline px-4 py-3 font-body text-[15px] text-gilt-ink transition-colors hover:border-gilt-brass hover:text-gilt-brass"
                                    disabled={loading}
                                >
                                    No, Sign In Again
                                </button>
                            </div>
                        </div>
                    ) : (
                        <form onSubmit={handleEmailSubmit} className="space-y-5">
                            <div>
                                <label
                                    htmlFor="email"
                                    className="mb-2 block font-mono text-[11px] uppercase tracking-[0.14em] text-gilt-soft"
                                >
                                    Email
                                </label>
                                <input
                                    type="email"
                                    id="email"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    className="w-full rounded-md border border-gilt-hairline bg-gilt-paper px-3.5 py-2.5 font-body text-[15px] text-gilt-ink placeholder:text-gilt-faint focus:border-gilt-brass focus:outline-none transition-colors"
                                    placeholder="Your email address"
                                />
                            </div>

                            <div>
                                <label
                                    htmlFor="password"
                                    className="mb-2 block font-mono text-[11px] uppercase tracking-[0.14em] text-gilt-soft"
                                >
                                    Password
                                </label>
                                <input
                                    type="password"
                                    id="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    className="w-full rounded-md border border-gilt-hairline bg-gilt-paper px-3.5 py-2.5 font-body text-[15px] text-gilt-ink placeholder:text-gilt-faint focus:border-gilt-brass focus:outline-none transition-colors"
                                    placeholder="Your password"
                                />
                            </div>

                            <button
                                type="submit"
                                className="gl-gilt-edge relative w-full overflow-hidden rounded-lg bg-gilt-ink px-6 py-3.5 font-body text-[15px] font-medium text-gilt-paper shadow-gl-card transition-transform duration-200 ease-gl-out hover:-translate-y-0.5 active:translate-y-0"
                                disabled={loading}
                            >
                                {loading ? 'Please wait...' : 'Sign In'}
                            </button>

                            <div className="flex items-center gap-3">
                                <span aria-hidden="true" className="flex-1 border-t border-gilt-hairline" />
                                <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-gilt-faint">OR</span>
                                <span aria-hidden="true" className="flex-1 border-t border-gilt-hairline" />
                            </div>

                            <button
                                type="button"
                                onClick={handleGoogleSignIn}
                                className="flex w-full items-center justify-center rounded-md border border-gilt-hairline bg-gilt-paper px-4 py-2.5 font-body text-[15px] text-gilt-ink transition-colors hover:border-gilt-brass hover:text-gilt-brass"
                                disabled={loading}
                            >
                                <img
                                    src="https://www.google.com/favicon.ico"
                                    alt="Google"
                                    className="w-4 h-4 mr-2"
                                />
                                Continue with Google
                            </button>
                        </form>
                    )}

                    {error && (
                        <div className="mt-4 text-center font-body text-sm text-gilt-debit">
                            {error}
                        </div>
                    )}
                </motion.div>

                <p className="mt-6 text-center font-body text-sm text-gilt-soft">
                    Don't have an account?{' '}
                    <Link to="/signup" className="text-gilt-brass hover:underline">
                        Sign up
                    </Link>
                </p>

                <p className="mt-4 text-center font-body text-xs text-gilt-faint">
                    By signing in, you agree to our{' '}
                    <a href="/terms" className="text-gilt-brass hover:underline">
                        Terms of Service
                    </a>{' '}
                    and{' '}
                    <a href="/privacy" className="text-gilt-brass hover:underline">
                        Privacy Policy
                    </a>
                </p>
            </div>
        </div>
    );
};

export default Login;
