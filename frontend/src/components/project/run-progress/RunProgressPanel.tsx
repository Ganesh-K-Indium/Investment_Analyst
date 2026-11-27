import React, { useState } from 'react';
import type { ProgressLane, ProgressStep, RunProgress } from '../../../services/analysisTasksApi';
import {
    ALPHA_DIMS, activeStep, defaultLane, dimensionStates, formatClock, formatDuration, helpFor, latestByBase, laneFraction,
    type DimState,
} from './runProgressModel';
import { useEasedValues, useNow } from './useRunClock';
import './RunProgressPanel.css';

interface RunProgressPanelProps {
    kind: 'alpha' | 'compare' | 'ask';
    /** Latest snapshot from the backend; null until the worker has picked the run up. */
    progress: RunProgress | null;
    /** Client time (ms) the snapshot arrived — keeps running-step clocks right even if the server's clock is off. */
    receivedAt: number;
    /** Client time (ms) the user started the run, for the overall clock. */
    startedAt: number;
    /** Companies being compared, shown as chips. */
    chips?: string[];
    /** Set when the run has ended (the panel stops its clock). */
    ended?: boolean;
}

// Radar geometry (viewBox 240×240)
const CX = 120;
const CY = 120;
const R = 84;
const angle = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / ALPHA_DIMS.length;
const point = (i: number, scale: number) => ({ x: CX + Math.cos(angle(i)) * R * scale, y: CY + Math.sin(angle(i)) * R * scale });
const polygon = (scales: number[]) => scales.map((s, i) => { const p = point(i, s); return `${p.x.toFixed(1)},${p.y.toFixed(1)}`; }).join(' ');

const Check: React.FC = () => (
    <svg className="rp__check" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <polyline points="20 6 9 17 4 12" />
    </svg>
);

const Cross: React.FC = () => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" aria-hidden="true">
        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
);

const StatusIcon: React.FC<{ status: ProgressStep['status'] }> = ({ status }) => {
    if (status === 'done') return <span className="rp__marker rp__marker--done"><Check /></span>;
    if (status === 'failed') return <span className="rp__marker rp__marker--failed"><Cross /></span>;
    if (status === 'running') return <span className="rp__marker rp__marker--running"><span className="rp__spinner" /></span>;
    return <span className="rp__marker rp__marker--pending" />;
};

export const RunProgressPanel: React.FC<RunProgressPanelProps> = ({ kind, progress, receivedAt, startedAt, chips, ended = false }) => {
    const now = useNow(!ended);
    const [picked, setPicked] = useState<string | null>(null);

    const lane: ProgressLane | null = (picked && progress?.lanes.find(l => l.id === picked)) || defaultLane(progress);
    const lanes = progress?.lanes ?? [];
    const allSteps = lanes.flatMap(l => l.steps);
    const overall = allSteps.length ? allSteps.filter(s => s.status === 'done' || s.status === 'failed').length / allSteps.length : 0;
    const serverNow = progress ? progress.updated_at + (now - receivedAt) : now;
    const step = activeStep(lane);
    const queued = !progress || lanes.length === 0;

    const title = kind === 'alpha' ? '360° ALPHA analysis' : kind === 'compare' ? 'Company comparison' : 'Answering your question';
    const headLabel = kind === 'alpha' && lane ? lane.label : null;

    return (
        <section className={`rp rp--${kind}`} aria-label={`${title} progress`}>
            <header className="rp__head">
                <div className="rp__title">
                    <span className={`rp__live${ended ? ' rp__live--off' : ''}`} aria-hidden="true" />
                    <span>{title}</span>
                    {headLabel && <b className="rp__ticker">{headLabel}</b>}
                    {kind === 'compare' && chips && chips.length > 0 && (
                        <span className="rp__chips">{chips.map(c => <span key={c} className="rp__chip">{c}</span>)}</span>
                    )}
                </div>
                <span className="rp__clock" title="Time elapsed">{formatClock(now - startedAt)}</span>
            </header>

            {kind === 'alpha' && lanes.length > 1 && (
                <div className="rp__lanes" role="tablist" aria-label="Tickers">
                    {lanes.map(l => {
                        const frac = laneFraction(l);
                        const running = l.steps.some(s => s.status === 'running');
                        return (
                            <button key={l.id} type="button" role="tab" aria-selected={lane?.id === l.id}
                                className={`rp__lane${lane?.id === l.id ? ' rp__lane--on' : ''}${frac >= 1 ? ' rp__lane--done' : ''}${running ? ' rp__lane--running' : ''}`}
                                onClick={() => setPicked(l.id)}>
                                {frac >= 1 ? <Check /> : <span className="rp__lane-dot" />}
                                {l.label}
                                <span className="rp__lane-pct">{Math.round(frac * 100)}%</span>
                            </button>
                        );
                    })}
                </div>
            )}

            {/* ALPHA knows every step up front, so it gets a real percentage. Compare/Ask only discover their steps as the
                pipeline runs (it depends on the question), so a percentage would mislead: the bar just shows it is alive. */}
            {kind === 'alpha' ? (
                <div className="rp__bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(overall * 100)}
                    aria-label="Overall progress">
                    <span className={`rp__bar-fill${!queued && overall < 1 && !ended ? ' rp__bar-fill--live' : ''}`}
                        style={{ width: `${Math.max(queued ? 0 : 3, Math.round(overall * 100))}%` }} />
                </div>
            ) : (
                <div className="rp__bar" role="progressbar" aria-label="Working" aria-busy={!ended}>
                    {!ended && <span className="rp__bar-fill rp__bar-fill--indeterminate" />}
                </div>
            )}

            {kind === 'alpha'
                ? <AlphaBody lane={lane} />
                : <Timeline lane={lane} serverNow={serverNow} />}

            <p className="rp__now" role="status" aria-live="polite">
                <span key={step?.id ?? (queued ? 'queued' : 'idle')} className="rp__now-text">
                    {queued
                        ? 'Queued. Waiting for a worker to pick this up…'
                        : helpFor(step) ?? (overall >= 1 ? 'Done.' : 'Working…')}
                </span>
            </p>
            <p className="rp__note">
                This can take a few minutes. You can switch chats or pages — the result will be waiting here when it’s ready.
            </p>
        </section>
    );
};

// ── ALPHA: radar + five dimensions ──────────────────────────────────────────────────────────────────────────────────

/** The line under a dimension's name: where it is, with what it found. */
function dimStateText(d: DimState): string {
    switch (d.phase) {
        case 'queued': return 'Waiting';
        case 'reading': return 'Reading sources…';
        case 'gathered': return `${d.detail ?? 'Sources read'} · Ready to analyse`;
        case 'analysing': return `${d.detail ? `${d.detail} · ` : ''}Analysing…`;
        case 'done': return `${d.detail ?? 'Done'}${d.durationMs != null ? ` · ${formatDuration(d.durationMs)}` : ''}`;
        case 'failed': return 'Not enough data';
    }
}

const isWorking = (d: DimState) => d.phase === 'reading' || d.phase === 'analysing';

const AlphaBody: React.FC<{ lane: ProgressLane | null }> = ({ lane }) => {
    const dims = dimensionStates(lane);
    const eased = useEasedValues(dims.map(d => d.fraction));
    const combine = lane ? latestByBase(lane).get('combine') : undefined;
    const reading = dims.findIndex(isWorking);

    return (
        <div className="rp__alpha">
            <svg viewBox="0 0 240 240" className="rp__radar" aria-hidden="true">
                <defs>
                    <linearGradient id="rpFill" x1="0" y1="0" x2="1" y2="1">
                        <stop offset="0%" stopColor="#FF8A3D" stopOpacity="0.5" />
                        <stop offset="100%" stopColor="#E8452C" stopOpacity="0.14" />
                    </linearGradient>
                </defs>
                {[1, 0.75, 0.5, 0.25].map(s => <polygon key={s} points={polygon(ALPHA_DIMS.map(() => s))} className="rp__ring" />)}
                {ALPHA_DIMS.map((_, i) => {
                    const p = point(i, 1);
                    return <line key={i} x1={CX} y1={CY} x2={p.x} y2={p.y} className={`rp__spoke${isWorking(dims[i]) ? ' rp__spoke--on' : ''}`} />;
                })}
                <polygon points={polygon(eased.map(f => 0.05 + 0.95 * f))} fill="url(#rpFill)" className="rp__shape" />
                {ALPHA_DIMS.map((d, i) => {
                    const p = point(i, 0.05 + 0.95 * eased[i]);
                    const edge = point(i, 1.2);
                    const phase = dims[i].phase;
                    return (
                        <g key={d.key}>
                            {isWorking(dims[i]) && <circle cx={p.x} cy={p.y} r="12" className="rp__pulse" />}
                            <circle cx={p.x} cy={p.y} r={phase === 'queued' ? 2.5 : 4.5} className={`rp__dot rp__dot--${phase}`} />
                            <text x={edge.x} y={edge.y} textAnchor="middle" dominantBaseline="central"
                                className={`rp__letter${phase !== 'queued' ? ' rp__letter--on' : ''}`}>{d.letter}</text>
                        </g>
                    );
                })}
            </svg>

            <ol className="rp__dims">
                {dims.map((d, i) => (
                    <li key={d.key} className={`rp__dim rp__dim--${d.phase}${i === reading ? ' rp__dim--active' : ''}`}>
                        <span className="rp__letterchip" aria-hidden="true">{d.phase === 'done' ? <Check /> : d.letter}</span>
                        <span className="rp__dim-text">
                            <span className="rp__dim-name">{d.name}</span>
                            <span className="rp__dim-state">{dimStateText(d)}</span>
                        </span>
                        <span className="rp__stages" aria-hidden="true">
                            <span className={`rp__stage${d.fraction >= 0.5 ? ' rp__stage--done' : d.phase === 'reading' ? ' rp__stage--live' : ''}`} />
                            <span className={`rp__stage${d.fraction >= 1 ? ' rp__stage--done' : d.phase === 'analysing' ? ' rp__stage--live' : ''}`} />
                        </span>
                    </li>
                ))}
                <li className={`rp__dim rp__dim--compile rp__dim--${combine?.status ?? 'pending'}`}>
                    <span className="rp__letterchip" aria-hidden="true">{combine?.status === 'done' ? <Check /> : '∑'}</span>
                    <span className="rp__dim-text">
                        <span className="rp__dim-name">Compile the report</span>
                        <span className="rp__dim-state">
                            {combine?.status === 'running' ? 'Writing…' : combine?.status === 'done' ? 'Done' : combine?.status === 'failed' ? 'Could not compile' : 'After all five dimensions'}
                        </span>
                    </span>
                </li>
            </ol>
        </div>
    );
};

// ── Compare / Ask: step timeline ────────────────────────────────────────────────────────────────────────────────────

const Timeline: React.FC<{ lane: ProgressLane | null; serverNow: number }> = ({ lane, serverNow }) => {
    const steps = lane?.steps ?? [];
    if (steps.length === 0) {
        return (
            <ol className="rp__timeline">
                <li className="rp__step rp__step--pending"><StatusIcon status="pending" /><span className="rp__step-label">Getting started</span></li>
            </ol>
        );
    }
    return (
        <ol className="rp__timeline">
            {steps.map(s => {
                const elapsed = s.status === 'running' && s.started_at ? serverNow - s.started_at
                    : s.started_at && s.ended_at ? s.ended_at - s.started_at : null;
                return (
                    <li key={s.id} className={`rp__step rp__step--${s.status}`}>
                        <StatusIcon status={s.status} />
                        <span className="rp__step-body">
                            <span className="rp__step-label">{s.label}</span>
                            {s.detail && <span className="rp__step-detail">{s.detail}</span>}
                        </span>
                        {elapsed != null && <span className="rp__step-time">{formatDuration(Math.max(0, elapsed))}</span>}
                    </li>
                );
            })}
        </ol>
    );
};

export default RunProgressPanel;
