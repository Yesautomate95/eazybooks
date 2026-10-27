import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { authApi } from '../../../services/api';
import { cn } from '../../../lib/utils';
import useGiltTheme from '../Landing/gilt/useGiltTheme';
import CoinMark from '../Landing/gilt/CoinMark';
import '../../../styles/gilt.css';

const EASE_OUT = [0.16, 1, 0.3, 1];

const Signup = () => {
    const [name, setName] = useState('');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const navigate = useNavigate();
    const [theme] = useGiltTheme();
    const prefersReducedMotion = useReducedMotion();

    // Check if opened from Electron app
    const urlParams = new URLSearchParams(window.location.search);
    const isFromElectron = urlParams.get('source') === 'electron';

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
                <h2 style="font-size: 24px; margin-bottom: 10px;">Account Created!</h2>
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
                setTimeout(() => {
                    countdownText.textContent =
                        'Please close this tab manually and return to the EazyBooks app.';
                }, 500);
            } else {
                countdownText.textContent = `This window will close in ${secondsLeft} seconds...`;
            }
        }, 1000);
    };

    const handleSignup = async (e) => {
        e.preventDefault();
        setError('');

        if (!email || !password) {
            setError('Please fill in all required fields.');
            return;
        }

        if (password.length < 6) {
            setError('Password must be at least 6 characters long.');
            return;
        }

        if (password !== confirmPassword) {
            setError('Passwords do not match.');
            return;
        }

        try {
            setLoading(true);
            const data = await authApi.emailSignUp(email, password, name);

            localStorage.setItem('jwt_token', data.jwt_token);
            if (data.user?.name || name) {
                localStorage.setItem('username', data.user?.name || name);
            }
            const userEmail = data.user?.email || email;
            if (userEmail) {
                localStorage.setItem('email', userEmail);
            }

            // Redirect to Electron app or web dashboard
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

    return (
        <div className={cn('gl-root min-h-screen', theme === 'dark' && 'gl-dark')}>
            <div className="gl-grain relative flex min-h-screen items-center justify-center overflow-hidden bg-gilt-paper px-4 py-12 sm:px-6">
                <div className="w-full max-w-[420px]">
                    <div className="mb-8 text-center">
                        <Link to="/" className="inline-flex items-center gap-2.5 text-gilt-ink">
                            <CoinMark className="shrink-0 text-gilt-gold" />
                            <h1 className="gl-display text-[22px] leading-none">
                                EazyBooks
                            </h1>
                        </Link>
                        <p className="mt-5 font-display text-gl-h3 text-gilt-ink">
                            Create your account
                        </p>
                    </div>

                    <motion.div
                        initial={prefersReducedMotion ? false : { opacity: 0, y: 14 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.6, ease: EASE_OUT }}
                        className="gl-gilt-edge relative overflow-hidden rounded-xl border border-gilt-hairline bg-gilt-card p-6 shadow-gl-card sm:p-8"
                    >
                        <form onSubmit={handleSignup} className="space-y-5">
                            <div>
                                <label
                                    htmlFor="name"
                                    className="mb-2 block font-mono text-[11px] uppercase tracking-[0.14em] text-gilt-soft"
                                >
                                    Name <span className="text-gilt-faint">(optional)</span>
                                </label>
                                <input
                                    type="text"
                                    id="name"
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    className="w-full rounded-md border border-gilt-hairline bg-gilt-paper px-3.5 py-2.5 font-body text-[15px] text-gilt-ink placeholder:text-gilt-faint transition-colors focus:border-gilt-brass focus:outline-none"
                                    placeholder="Your name"
                                />
                            </div>

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
                                    className="w-full rounded-md border border-gilt-hairline bg-gilt-paper px-3.5 py-2.5 font-body text-[15px] text-gilt-ink placeholder:text-gilt-faint transition-colors focus:border-gilt-brass focus:outline-none"
                                    placeholder="Your email address"
                                    required
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
                                    className="w-full rounded-md border border-gilt-hairline bg-gilt-paper px-3.5 py-2.5 font-body text-[15px] text-gilt-ink placeholder:text-gilt-faint transition-colors focus:border-gilt-brass focus:outline-none"
                                    placeholder="At least 6 characters"
                                    required
                                />
                            </div>

                            <div>
                                <label
                                    htmlFor="confirmPassword"
                                    className="mb-2 block font-mono text-[11px] uppercase tracking-[0.14em] text-gilt-soft"
                                >
                                    Confirm Password
                                </label>
                                <input
                                    type="password"
                                    id="confirmPassword"
                                    value={confirmPassword}
                                    onChange={(e) => setConfirmPassword(e.target.value)}
                                    className="w-full rounded-md border border-gilt-hairline bg-gilt-paper px-3.5 py-2.5 font-body text-[15px] text-gilt-ink placeholder:text-gilt-faint transition-colors focus:border-gilt-brass focus:outline-none"
                                    placeholder="Confirm your password"
                                    required
                                />
                            </div>

                            <button
                                type="submit"
                                className="gl-gilt-edge relative w-full overflow-hidden rounded-lg bg-gilt-ink px-6 py-3.5 font-body text-[15px] font-medium text-gilt-paper shadow-gl-card transition-transform duration-200 ease-gl-out hover:-translate-y-0.5 active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-60"
                                disabled={loading}
                            >
                                {loading ? 'Creating account...' : 'Create Account'}
                            </button>
                        </form>

                        {error && (
                            <div className="mt-4 text-center font-body text-sm text-gilt-debit">
                                {error}
                            </div>
                        )}

                        <p className="mt-6 text-center font-body text-sm text-gilt-soft">
                            Already have an account?{' '}
                            <Link to="/login" className="text-gilt-brass hover:underline">
                                Sign in
                            </Link>
                        </p>
                    </motion.div>
                </div>
            </div>
        </div>
    );
};

export default Signup;
