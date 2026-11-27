import React, { useEffect, useState } from 'react';
import { DIMENSIONS } from './alphaDimensions';

/**
 * Live "ALPHA analysis run" — the landing page's one animated moment.
 *
 * It replays what the platform actually does: pick a company, read the sources
 * behind each of the five ALPHA dimensions in turn, resolve each to a signal,
 * and finish with a drafted report. Illustrative only — the figures are not
 * real analysis output, and the panel says so.
 *
 * The whole animation is a pure function of elapsed time (no chained timers),
 * so it can never drift out of sync, and reduced-motion users get the finished
 * frame instead.
 */

const TOTAL_SOURCES = DIMENSIONS.reduce((n, d) => n + d.sourceCount, 0);

// Illustrative scores (0–1) per run — different shapes so the radar visibly
// changes company to company.
const RUNS = [
    { ticker: 'AAPL', name: 'Apple Inc.',      scores: [0.82, 0.62, 0.92, 0.88, 0.56] },
    { ticker: 'NVDA', name: 'NVIDIA Corp.',    scores: [0.70, 0.48, 0.95, 0.78, 0.84] },
    { ticker: 'JPM',  name: 'JPMorgan Chase',  scores: [0.78, 0.40, 0.74, 0.86, 0.62] },
];

// Timeline (ms)
const START = 500;       // before first dimension starts reading
const STEP = 1250;       // gap between dimension starts
const DUR = 1150;        // time to read one dimension
const DONE_AT = START + (DIMENSIONS.length - 1) * STEP + DUR;   // 6650
const HOLD_UNTIL = DONE_AT + 3000;
const CYCLE = HOLD_UNTIL + 600;                                  // fade-out tail

// Radar geometry (viewBox 240×240)
const CX = 120;
const CY = 120;
const R = 84;
const angle = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / DIMENSIONS.length;
const pt = (i: number, scale: number) => ({
    x: CX + Math.cos(angle(i)) * R * scale,
    y: CY + Math.sin(angle(i)) * R * scale,
});
const polygon = (scales: number[]) =>
    scales.map((s, i) => { const p = pt(i, s); return `${p.x.toFixed(1)},${p.y.toFixed(1)}`; }).join(' ');

const clamp01 = (n: number) => Math.min(1, Math.max(0, n));
const easeOut = (n: number) => 1 - Math.pow(1 - n, 3);

type Signal = { text: string; tone: 'pos' | 'neu' | 'neg' };
const signalFor = (score: number): Signal =>
    score >= 0.75 ? { text: 'Strong', tone: 'pos' }
        : score >= 0.55 ? { text: 'Neutral', tone: 'neu' }
            : { text: 'Weak', tone: 'neg' };

const prefersReducedMotion = () =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export const AlphaRun: React.FC = () => {
    const [reduced] = useState(prefersReducedMotion);
    // Reduced motion: freeze on the finished frame of the first run.
    const [elapsed, setElapsed] = useState(reduced ? DONE_AT + 400 : 0);

    useEffect(() => {
        if (reduced) return;
        let raf = 0;
        let origin = 0;
        const frame = (now: number) => {
            if (!origin) origin = now;
            setElapsed(now - origin);
            raf = requestAnimationFrame(frame);
        };
        raf = requestAnimationFrame(frame);
        return () => cancelAnimationFrame(raf);
    }, [reduced]);

    const runIndex = Math.floor(elapsed / CYCLE);
    const t = elapsed % CYCLE;
    const run = RUNS[runIndex % RUNS.length];

    // Per-dimension progress 0→1 and phase
    const progress = DIMENSIONS.map((_, i) => {
        const s = START + i * STEP;
        return clamp01((t - s) / DUR);
    });
    const phase = progress.map(p => (p <= 0 ? 'queued' : p < 1 ? 'reading' : 'done'));
    const fills = progress.map((p, i) => easeOut(p) * run.scores[i]);

    const doneCount = phase.filter(p => p === 'done').length;
    const readingIndex = phase.indexOf('reading');
    const finished = t >= DONE_AT;
    const spotlight = readingIndex >= 0
        ? readingIndex
        : Math.max(0, doneCount - 1);

    const sourcesRead = Math.round(
        DIMENSIONS.reduce((n, d, i) => n + d.sourceCount * easeOut(progress[i]), 0),
    );

    const fading = t > HOLD_UNTIL;
    const status = finished ? 'ready' : t > 0 ? 'running' : 'queued';

    return (
        <div className={`alpharun ${fading ? 'alpharun--fade' : ''}`}>
            {/* Screen readers get the static framework, not the animation. */}
            <p className="sr-only">
                The ALPHA framework analyses a company across five dimensions:{' '}
                {DIMENSIONS.map(d => d.label).join(', ')}.
            </p>

            <div className="alpharun__panel" aria-hidden="true">
                <div className="alpharun__head">
                    <div className="alpharun__title">
                        <span className="alpharun__ticker" key={run.ticker}>{run.ticker}</span>
                        <span className="alpharun__co">{run.name}</span>
                    </div>
                    <span className={`alpharun__status alpharun__status--${status}`}>
                        {status === 'running' && <span className="alpharun__spinner" />}
                        {status === 'ready' ? 'Report ready' : status === 'running' ? 'ALPHA analysis running' : 'Queued'}
                    </span>
                </div>

                <div className="alpharun__body">
                    <svg viewBox="0 0 240 240" className="alpharun__radar">
                        <defs>
                            <linearGradient id="alphaRunFill" x1="0" y1="0" x2="1" y2="1">
                                <stop offset="0%" stopColor="#FF8A3D" stopOpacity="0.55" />
                                <stop offset="100%" stopColor="#E8452C" stopOpacity="0.16" />
                            </linearGradient>
                        </defs>

                        {[1, 0.75, 0.5, 0.25].map(s => (
                            <polygon key={s} points={polygon(DIMENSIONS.map(() => s))} className="alpharun__ring" />
                        ))}
                        {DIMENSIONS.map((_, i) => {
                            const p = pt(i, 1);
                            return <line key={i} x1={CX} y1={CY} x2={p.x} y2={p.y}
                                className={`alpharun__spoke ${phase[i] === 'reading' ? 'alpharun__spoke--on' : ''}`} />;
                        })}

                        <polygon
                            points={polygon(fills.map(f => Math.max(f, 0.02)))}
                            fill="url(#alphaRunFill)"
                            className="alpharun__shape"
                        />

                        {DIMENSIONS.map((d, i) => {
                            const p = pt(i, Math.max(fills[i], 0.02));
                            const edge = pt(i, 1.2);
                            return (
                                <g key={i}>
                                    {phase[i] === 'reading' && (
                                        <circle cx={p.x} cy={p.y} r="12" className="alpharun__pulse" />
                                    )}
                                    <circle cx={p.x} cy={p.y} r={phase[i] === 'queued' ? 2.5 : 4.5}
                                        className={`alpharun__dot alpharun__dot--${phase[i]}`} />
                                    <text x={edge.x} y={edge.y} textAnchor="middle" dominantBaseline="central"
                                        className={`alpharun__letter ${phase[i] !== 'queued' ? 'alpharun__letter--on' : ''}`}>
                                        {d.letter}
                                    </text>
                                </g>
                            );
                        })}
                    </svg>

                    <ul className="alpharun__rows">
                        {DIMENSIONS.map((d, i) => {
                            const sig = signalFor(run.scores[i]);
                            return (
                                <li key={d.label} className={`alpharun__row alpharun__row--${phase[i]}`}>
                                    <span className="alpharun__letterchip">{d.letter}</span>
                                    <span className="alpharun__rowtext">
                                        <span className="alpharun__rowlabel">{d.label}</span>
                                        <span className="alpharun__rowsrc">{d.sources}</span>
                                    </span>
                                    <span className="alpharun__rowstate">
                                        {phase[i] === 'queued' && <span className="alpharun__queued">Queued</span>}
                                        {phase[i] === 'reading' && <span className="alpharun__spinner" />}
                                        {phase[i] === 'done' && (
                                            <span className={`alpharun__pill alpharun__pill--${sig.tone}`}>{sig.text}</span>
                                        )}
                                    </span>
                                    <span className="alpharun__bar" style={{ transform: `scaleX(${progress[i]})` }} />
                                </li>
                            );
                        })}
                    </ul>
                </div>

                <p className="alpharun__question" key={spotlight}>{DIMENSIONS[spotlight].question}</p>

                <div className="alpharun__foot">
                    <span className="alpharun__count">
                        <b>{sourcesRead}</b> / {TOTAL_SOURCES} sources read
                    </span>
                    <span className="alpharun__meter">
                        <span className="alpharun__meter-fill"
                            style={{ transform: `scaleX(${sourcesRead / TOTAL_SOURCES})` }} />
                    </span>
                    <span className="alpharun__note">Illustrative run</span>
                </div>
            </div>
        </div>
    );
};

export default AlphaRun;
