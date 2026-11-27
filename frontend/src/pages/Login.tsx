import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Wordmark } from '../components/ui/Wordmark';
import { SESSION_EXPIRED_KEY } from '../context/AuthContext';
import { useAuth } from '../context/useAuth';
import { useTheme } from '../context/useTheme';
import indiumLogoLight from '../assets/indium-logo.png';
import indiumLogoDark from '../assets/indium-logo-dark.png';
import { AlphaRun } from './AlphaRun';
import { DIMENSIONS } from './alphaDimensions';
import { GlowCard, Tilt, DotField } from './pointerFx';
import './Login.css';

type Mode = 'login' | 'signup';

const FEATURES = [
    { span: 2, tag: 'Secondary Research', t: 'Ask your filings anything', d: 'Question 10-K, 10-Q, 8-K, 20-F and 6-K filings. Retrieval picks the right filing type and fiscal period, and every answer cites its pages.' },
    { span: 1, tag: 'Quantitative Research', t: 'Live quant agents', d: 'Fundamentals, technicals, analyst ratings and options: put/call ratio, IV skew, unusual volume.' },
    { span: 1, tag: 'Insider & macro', t: 'Signals in context', d: 'Form 4 trades scored by role and dollar value, plus GDP, CPI, rates and jobs from FRED and BLS.' },
    { span: 1, tag: 'Compare · Scenarios', t: 'Side by side, or bull to bear', d: 'Compare companies, or run bull, bear and base cases on the same evidence.' },
    { span: 2, tag: 'Documents', t: 'Your files, read properly', d: 'Upload PDFs, scans, spreadsheets, Word or PowerPoint, or import from S3, SharePoint, Google Drive, Azure Blob, OneDrive, SFTP and Confluence. Read locally first, with finance arithmetic checked and doubtful pages held for review.' },
    { span: 1, tag: 'Analysis Dashboard', t: 'Every portfolio at a glance', d: 'See what is running, ready or failed, and batch-run a whole portfolio in the background.' },
    { span: 2, tag: 'Creation · Repository', t: 'From findings to a published report', d: 'Collect what matters, draft the report, publish it to a searchable repository and export it to PDF.' },
    { span: 2, tag: 'Analyst notes', t: 'Notes that join the evidence', d: 'Typed or handwritten notes are transcribed, reviewed and searched alongside the filings.' },
];

const TRUST = [
    { t: 'Cited to the page', d: 'Citations open the Document Inspector on the exact page, with the passage highlighted.' },
    { t: 'Right filing, right period', d: 'Fiscal calendars differ by company. Comparisons across mismatched periods are flagged, not blended.' },
    { t: 'Figures checked', d: 'Numbers and units in an answer are compared with the evidence it cites.' },
    { t: 'People review the doubtful', d: 'Low-confidence reads are held for approval instead of being indexed silently.' },
];

const COVERAGE = [
    { t: 'SEC filings', v: '10-K · 10-Q · 8-K · 20-F · 6-K, pulled from EDGAR' },
    { t: 'Insider', v: 'SEC Form 4 transactions' },
    { t: 'Macro', v: 'FRED · BLS' },
    { t: 'Markets', v: 'Fundamentals, technicals, ratings, options · Alpha Vantage, Polygon.io, Finnhub, Twelve Data, FMP, custom REST' },
    { t: 'Your files', v: 'PDF and scans, images, Excel and CSV, Word, PowerPoint, HTML' },
    { t: 'Connected', v: 'Amazon S3, SharePoint, Google Drive, Azure Blob, OneDrive, SFTP, Confluence, PostgreSQL' },
];

const CONTROLS = [
    { t: 'Bring your own model', d: 'OpenAI, Anthropic, Gemini, Ollama or any OpenAI-compatible server. Provider keys are stored encrypted.' },
    { t: 'Local-only mode', d: 'Switch off every cloud call. Local OCR and self-hosted models keep working.' },
    { t: 'Runs in the background', d: 'Interactive and batch queues keep long analyses off the page, with progress streamed live.' },
];

const STEPS = [
    { t: 'Build a portfolio', d: 'Group the companies you follow. Each portfolio is its own workspace for research, analysis and reports.' },
    { t: 'Research', d: 'Question filings and documents in Secondary Research, or query the numbers directly in Quantitative Research.' },
    { t: 'Run A[i]LPHA', d: 'Run the five-dimension analysis on one company, compare several, or batch-run a whole portfolio and keep working while it finishes.' },
    { t: 'Create the report', d: 'Collect the findings you want, draft the report, publish it to the searchable repository and export it to PDF.' },
];

const EyeIcon: React.FC<{ off?: boolean }> = ({ off }) => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
        <circle cx="12" cy="12" r="3" />
        {off && <line x1="3" y1="3" x2="21" y2="21" />}
    </svg>
);

export const Login: React.FC = () => {
    const navigate = useNavigate();
    const { login, signup } = useAuth();
    const { theme, toggleTheme } = useTheme();
    const indiumLogo = theme === 'dark' ? indiumLogoDark : indiumLogoLight;

    const [mode, setMode]         = useState<Mode>('login');
    const [email, setEmail]       = useState('');
    const [username, setUsername] = useState('');
    const [fullName, setFullName] = useState('');
    const [role, setRole]         = useState<'analyst' | 'fund_manager'>('analyst');
    const [password, setPassword] = useState('');
    const [confirm, setConfirm]   = useState('');
    const [showPassword, setShowPassword] = useState(false);
    const [loading, setLoading]   = useState(false);
    const [error, setError]       = useState('');
    const [sessionExpired, setSessionExpired] = useState(false);
    const cardRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (sessionStorage.getItem(SESSION_EXPIRED_KEY)) {
            sessionStorage.removeItem(SESSION_EXPIRED_KEY);
            setSessionExpired(true);
        }
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');

        if (mode === 'signup') {
            if (password !== confirm) { setError('Passwords do not match.'); return; }
            if (password.length < 8)  { setError('Password must be at least 8 characters.'); return; }
        }

        setLoading(true);
        try {
            if (mode === 'login') {
                await login(email, password);
            } else {
                await signup(email, username, password, fullName, role);
            }
            navigate('/home');
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Something went wrong.');
        } finally {
            setLoading(false);
        }
    };

    const switchMode = (next: Mode) => {
        setMode(next);
        setError('');
        setSessionExpired(false);
    };

    // Bring the user to the form from anywhere on the page.
    const goToForm = (next?: Mode) => (e: React.MouseEvent) => {
        e.preventDefault();
        if (next) switchMode(next);
        cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        window.setTimeout(() => document.getElementById('login-email')?.focus({ preventScroll: true }), 350);
    };

    return (
        <div className="lp">
            <DotField />

            {/* ───────── Header ───────── */}
            <header className="lp-head">
                <div className="lp-wrap lp-head__in">
                    <a href="#top" className="lp-brand" aria-label="A[i]LPHA by Indium">
                        <Wordmark />
                        <span className="lp-brand__by">by</span>
                        <img src={indiumLogo} alt="Indium" className="lp-brand__img" />
                    </a>
                    <nav className="lp-nav" aria-label="Page sections">
                        <a href="#framework">Framework</a>
                        <a href="#workspace">Workspace</a>
                        <a href="#trust">Trust</a>
                        <a href="#data">Data</a>
                    </nav>
                    <button
                        type="button"
                        className="lp-icon-btn"
                        title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                        aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                        onClick={toggleTheme}
                    >
                        {theme === 'dark' ? (
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <circle cx="12" cy="12" r="5" />
                                <line x1="12" y1="1" x2="12" y2="3" />
                                <line x1="12" y1="21" x2="12" y2="23" />
                                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                                <line x1="1" y1="12" x2="3" y2="12" />
                                <line x1="21" y1="12" x2="23" y2="12" />
                                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                            </svg>
                        ) : (
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                            </svg>
                        )}
                    </button>
                    <a href="#top" className="lp-head__cta" onClick={goToForm('login')}>Log in</a>
                </div>
            </header>

            <main id="top">
                {/* ───────── Hero + sign-in ───────── */}
                <section className="lp-hero">
                    <div className="lp-wrap lp-hero__in">
                        <div className="lp-hero__copy">
                            <p className="lp-eyebrow">AI-native investment research</p>
                            <h1>Invest today.<br />Shape tomorrow.</h1>
                            <p className="lp-lede">
                                <Wordmark /> reads SEC filings, insider trades, macro data and live markets through
                                one five-dimension framework, and shows its sources. Research companies, run the
                                analysis, and write the report in one workspace.
                            </p>
                            <div className="lp-cta">
                                <a href="#run" className="lp-link">
                                    See an analysis run
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                        <line x1="12" y1="5" x2="12" y2="19" /><polyline points="19 12 12 19 5 12" />
                                    </svg>
                                </a>
                            </div>
                        </div>

                        <div className="lp-card" ref={cardRef}>
                            <h2 className="lp-card__title">
                                {mode === 'login' ? 'Log in to your workspace' : 'Create your account'}
                            </h2>
                            <p className="lp-card__sub">
                                {mode === 'login'
                                    ? 'Pick up your research where you left off.'
                                    : 'Set up a workspace to start analysing companies.'}
                            </p>

                            <div className="lp-tabs" role="tablist" aria-label="Authentication mode">
                                <button role="tab" aria-selected={mode === 'login'} type="button"
                                    className={`lp-tab ${mode === 'login' ? 'lp-tab--on' : ''}`}
                                    onClick={() => switchMode('login')}>Login</button>
                                <button role="tab" aria-selected={mode === 'signup'} type="button"
                                    className={`lp-tab ${mode === 'signup' ? 'lp-tab--on' : ''}`}
                                    onClick={() => switchMode('signup')}>Sign Up</button>
                            </div>

                            <form className="lp-form" onSubmit={handleSubmit}>
                                {sessionExpired && !error && (
                                    <div className="lp-error" role="alert">Your session expired. Please log in again.</div>
                                )}
                                {error && <div className="lp-error" role="alert">{error}</div>}

                                <Input
                                    id="login-email"
                                    type="email"
                                    label="Email"
                                    placeholder="name@company.com"
                                    autoComplete="email"
                                    required
                                    value={email}
                                    onChange={e => setEmail(e.target.value)}
                                />

                                {mode === 'signup' && (
                                    <>
                                        <Input
                                            id="login-username"
                                            type="text"
                                            label="Username"
                                            placeholder="Choose a username"
                                            autoComplete="username"
                                            required
                                            value={username}
                                            onChange={e => setUsername(e.target.value)}
                                        />
                                        <Input
                                            id="login-fullname"
                                            type="text"
                                            label="Full name (optional)"
                                            placeholder="As you'd like it shown"
                                            autoComplete="name"
                                            value={fullName}
                                            onChange={e => setFullName(e.target.value)}
                                        />
                                        <div className="lp-role" role="group" aria-labelledby="login-role-label">
                                            <span id="login-role-label" className="lp-role__label">Role</span>
                                            <div className="lp-role__opts">
                                                <button type="button" aria-pressed={role === 'analyst'}
                                                    className={`lp-role__btn ${role === 'analyst' ? 'lp-role__btn--on' : ''}`}
                                                    onClick={() => setRole('analyst')}>Analyst</button>
                                                <button type="button" aria-pressed={role === 'fund_manager'}
                                                    className={`lp-role__btn ${role === 'fund_manager' ? 'lp-role__btn--on' : ''}`}
                                                    onClick={() => setRole('fund_manager')}>Fund Manager</button>
                                            </div>
                                        </div>
                                    </>
                                )}

                                <div className="lp-pw">
                                    <Input
                                        id="login-password"
                                        type={showPassword ? 'text' : 'password'}
                                        label="Password"
                                        placeholder={mode === 'signup' ? 'At least 8 characters' : 'Enter your password'}
                                        autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                                        required
                                        value={password}
                                        onChange={e => setPassword(e.target.value)}
                                    />
                                    <button type="button" className="lp-pw__toggle"
                                        onClick={() => setShowPassword(s => !s)}
                                        aria-label={showPassword ? 'Hide password' : 'Show password'}
                                        aria-pressed={showPassword}
                                        title={showPassword ? 'Hide password' : 'Show password'}>
                                        <EyeIcon off={showPassword} />
                                    </button>
                                </div>

                                {mode === 'signup' && (
                                    <Input
                                        id="login-confirm"
                                        type={showPassword ? 'text' : 'password'}
                                        label="Confirm password"
                                        placeholder="Re-enter your password"
                                        autoComplete="new-password"
                                        required
                                        value={confirm}
                                        onChange={e => setConfirm(e.target.value)}
                                    />
                                )}

                                <Button type="submit" variant="primary" fullWidth loading={loading}>
                                    {mode === 'login' ? 'Log in' : 'Create account'}
                                </Button>
                            </form>

                            <p className="lp-card__switch">
                                {mode === 'login' ? 'New to Indium?' : 'Already have an account?'}{' '}
                                <button type="button" className="lp-textbtn"
                                    onClick={() => switchMode(mode === 'login' ? 'signup' : 'login')}>
                                    {mode === 'login' ? 'Create an account' : 'Log in'}
                                </button>
                            </p>
                        </div>
                    </div>
                </section>

                {/* ───────── A[i]LPHA flagship ───────── */}
                <section id="framework" className="lp-sec">
                    <div className="lp-wrap">
                        <h2>One framework, five dimensions</h2>
                        <p className="lp-sub">
                            <Wordmark /> asks the questions a portfolio manager asks, in the same order, every time,
                            so an analysis can be compared across companies instead of read as a pile of search results.
                        </p>
                        <div className="lp-split lp-split--flag">
                            <ol className="lp-dimlist">
                                {DIMENSIONS.map(d => (
                                    <li key={d.label}>
                                        <span className="lp-dimlist__letter">{d.letter}</span>
                                        <div>
                                            <h3>{d.label}</h3>
                                            <p>{d.question}</p>
                                            <small>{d.sources}</small>
                                        </div>
                                    </li>
                                ))}
                            </ol>
                            <Tilt className="lp-run-tilt"><AlphaRun /></Tilt>
                        </div>
                    </div>
                </section>

                {/* ───────── Workspace ───────── */}
                <section id="workspace" className="lp-sec lp-sec--tint">
                    <div className="lp-wrap">
                        <h2>And everything around it</h2>
                        <p className="lp-sub">
                            ALPHA is one tool in a full research workspace: ask, query, compare, read, track and
                            publish without leaving it.
                        </p>
                        <div className="lp-bento">
                            {FEATURES.map(f => (
                                <GlowCard key={f.t} className={`lp-cap lp-cap--${f.span}`}>
                                    <span className="lp-cap__tag">{f.tag}</span>
                                    <h3>{f.t}</h3>
                                    <p>{f.d}</p>
                                </GlowCard>
                            ))}
                        </div>
                    </div>
                </section>

                {/* ───────── Traceable ───────── */}
                <section id="trust" className="lp-sec">
                    <div className="lp-wrap lp-split lp-split--rev">
                        <Tilt className="lp-run-tilt">
                            <figure className="lp-answer" aria-label="Example of a cited answer">
                                <div className="lp-answer__q">
                                    <span className="lp-answer__who">You</span>
                                    How did margins and cash generation change in the latest quarter?
                                </div>
                                <div className="lp-answer__a">
                                    <span className="lp-answer__who lp-answer__who--ai">A<span>[i]</span>LPHA</span>
                                    <p>
                                        Margins moved against the prior quarter on product mix, and operating cash flow
                                        tracked earnings rather than working-capital timing
                                        <sup>1</sup>. Management attributed the change to pricing and supply costs
                                        <sup>2</sup>, which the annual filing frames as a multi-year trend
                                        <sup>3</sup>.
                                    </p>
                                    <ul className="lp-answer__src">
                                        <li><b>1</b> 10-Q · latest quarter · income statement</li>
                                        <li><b>2</b> 10-Q · MD&amp;A</li>
                                        <li><b>3</b> 10-K · prior fiscal year · segment note</li>
                                    </ul>
                                    <div className="lp-answer__check">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12" /></svg>
                                        Figures and units checked against the cited pages
                                    </div>
                                </div>
                                <figcaption>Illustrative answer</figcaption>
                            </figure>
                        </Tilt>
                        <div>
                            <h2>Answers you can trace to the page</h2>
                            <p className="lp-sub">
                                Every claim links back to its source, and the checks run before you see the answer.
                            </p>
                            <ul className="lp-points lp-points--2">
                                {TRUST.map(t => (
                                    <li key={t.t}><h3>{t.t}</h3><p>{t.d}</p></li>
                                ))}
                            </ul>
                        </div>
                    </div>
                </section>

                {/* ───────── Reads & control ───────── */}
                <section id="data" className="lp-sec lp-sec--tint">
                    <div className="lp-wrap">
                        <h2>What it reads, and where it runs</h2>
                        <p className="lp-sub">
                            Public filings and market data out of the box, plus the files and systems your team already
                            keeps. You choose the models, and you can keep everything on your own machine.
                        </p>
                        <div className="lp-split lp-split--flag">
                            <GlowCard className="lp-reads">
                                <dl>
                                    {COVERAGE.map(c => (
                                        <div key={c.t}><dt>{c.t}</dt><dd>{c.v}</dd></div>
                                    ))}
                                </dl>
                            </GlowCard>
                            <ul className="lp-points">
                                {CONTROLS.map(c => (
                                    <li key={c.t}><h3>{c.t}</h3><p>{c.d}</p></li>
                                ))}
                            </ul>
                        </div>
                    </div>
                </section>

                {/* ───────── Workflow ───────── */}
                <section id="workflow" className="lp-sec">
                    <div className="lp-wrap">
                        <h2>From a list of tickers to a finished report</h2>
                        <ol className="lp-steps">
                            {STEPS.map((s, i) => (
                                <li key={s.t}>
                                    <span className="lp-steps__n">{i + 1}</span>
                                    <h3>{s.t}</h3>
                                    <p>{s.d}</p>
                                </li>
                            ))}
                        </ol>
                    </div>
                </section>

                {/* ───────── Close ───────── */}
                <section className="lp-close">
                    <div className="lp-wrap lp-close__in">
                        <h2>Start with the companies you already follow</h2>
                        <a href="#top" className="lp-close__btn" onClick={goToForm('signup')}>Create an account</a>
                    </div>
                </section>
            </main>

            <footer className="lp-foot">
                <div className="lp-wrap lp-foot__in">
                    <span><Wordmark /></span>
                    <p>A[i]LPHA is a product of Indium. The analysis run and the cited answer shown on this page are illustrations of how the platform works, not output for any real company.</p>
                </div>
            </footer>
        </div>
    );
};

export default Login;
