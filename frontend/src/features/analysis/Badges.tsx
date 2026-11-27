import React from 'react';
import type { PillarDirection, Stance, TaskStatus } from '../../services/analysisTasksApi';
import { DIRECTION_LABEL, STATUS_META, STANCE_ORDER, directionSymbol, stanceTone } from './format';

/** The model's overall ALPHA stance. Labelled "model stance" for screen readers: it is not investment advice. */
export const StanceBadge: React.FC<{ stance: Stance | null | undefined; size?: 'sm' | 'md' }> = ({ stance, size = 'sm' }) => {
    if (!stance) return <span className="ad-stance ad-stance--none" aria-label="No stance recorded">—</span>;
    return (
        <span className={`ad-stance ad-stance--${stanceTone(stance)} ad-stance--${size}`} aria-label={`Model stance: ${stance}`}>
            <span className="ad-stance__dot" aria-hidden="true" />
            {stance}
        </span>
    );
};

export const StatusPill: React.FC<{ status: TaskStatus }> = ({ status }) => {
    const meta = STATUS_META[status] ?? STATUS_META.PENDING;
    return <span className={`run-pill ${meta.className}`}>{meta.label}</span>;
};

export const TickerChips: React.FC<{ tickers?: string[] | null; max?: number; onClick?: (t: string) => void }> = ({ tickers, max = 4, onClick }) => {
    if (!tickers || tickers.length === 0) return null;
    const shown = tickers.slice(0, max);
    return (
        <span className="ad-chips">
            {shown.map((t) => onClick ? (
                <button key={t} type="button" className="ad-chip ad-chip--button" onClick={(e) => { e.stopPropagation(); onClick(t); }}
                    title={`Thesis timeline for ${t}`}>{t}</button>
            ) : <span key={t} className="ad-chip">{t}</span>)}
            {tickers.length > max && <span className="ad-chip ad-chip--more">+{tickers.length - max}</span>}
        </span>
    );
};

export const DirectionCell: React.FC<{ direction: PillarDirection; previous?: PillarDirection; label: string }> = ({ direction, previous, label }) => {
    const changed = previous !== undefined && previous !== null && direction !== null && previous !== direction;
    const rank = (d: PillarDirection) => (d === 'positive' ? 1 : d === 'negative' ? -1 : 0);
    const arrow = changed ? (rank(direction) > rank(previous ?? null) ? '▲' : '▼') : null;
    const text = direction ? DIRECTION_LABEL[direction] : 'Not stated';
    return (
        <span className={`ad-dir ad-dir--${direction ?? 'none'}`} aria-label={`${label}: ${text}${changed ? `, was ${DIRECTION_LABEL[previous!]}` : ''}`}>
            <span aria-hidden="true">{directionSymbol(direction)}</span>
            {arrow && <span className="ad-dir__arrow" aria-hidden="true">{arrow}</span>}
        </span>
    );
};

/** Horizontal bar of how many holdings sit at each stance. */
export const StanceMixBar: React.FC<{ mix: Partial<Record<Stance, number>>; total?: number; showLegend?: boolean }> = ({ mix, total, showLegend = true }) => {
    const sum = STANCE_ORDER.reduce((n, s) => n + (mix[s] ?? 0), 0);
    const denom = Math.max(total ?? sum, sum, 1);
    const unknown = Math.max(0, (total ?? sum) - sum);
    return (
        <div className="ad-mix">
            <div className="ad-mix__bar" role="img" aria-label={STANCE_ORDER.filter((s) => mix[s]).map((s) => `${mix[s]} ${s}`).join(', ') || 'No stances yet'}>
                {STANCE_ORDER.map((s) => (mix[s] ? (
                    <span key={s} className={`ad-mix__seg ad-stance--${stanceTone(s)}`} style={{ width: `${(100 * (mix[s] ?? 0)) / denom}%` }} />
                ) : null))}
                {unknown > 0 && <span className="ad-mix__seg ad-mix__seg--unknown" style={{ width: `${(100 * unknown) / denom}%` }} />}
            </div>
            {showLegend && (
                <ul className="ad-mix__legend">
                    {STANCE_ORDER.filter((s) => mix[s]).map((s) => (
                        <li key={s}><span className={`ad-mix__key ad-stance--${stanceTone(s)}`} aria-hidden="true" />{s} <strong className="tabular">{mix[s]}</strong></li>
                    ))}
                    {unknown > 0 && <li><span className="ad-mix__key ad-mix__seg--unknown" aria-hidden="true" />Not analysed <strong className="tabular">{unknown}</strong></li>}
                </ul>
            )}
        </div>
    );
};
