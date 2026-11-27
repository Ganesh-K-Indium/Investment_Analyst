import React from 'react';
import type { PulseTickerRow } from '../../services/analysisDashboardApi';
import { DirectionCell, StanceBadge } from './Badges';
import { PILLARS, compactUsd, relativeTime } from './format';

interface SignalMatrixProps {
    rows: PulseTickerRow[];
    staleDays: number;
    onOpenRun: (taskId: string) => void;
    onOpenTimeline: (ticker: string) => void;
    onRunAlpha: (ticker: string) => void;
    busyTicker?: string | null;
}

/** Holdings × ALPHA pillars: where the latest analysis stands on each, and what moved since the run before. */
export const SignalMatrix: React.FC<SignalMatrixProps> = ({ rows, staleDays, onOpenRun, onOpenTimeline, onRunAlpha, busyTicker }) => {
    if (rows.length === 0) {
        return <p className="ad-empty">This portfolio has no holdings yet. Add companies in Portfolio Details above.</p>;
    }
    return (
        <div className="ad-matrix" role="region" aria-label="Signal matrix" tabIndex={0}>
            <table className="ad-matrix__table">
                <thead>
                    <tr>
                        <th scope="col" className="ad-matrix__sticky">Holding</th>
                        <th scope="col">Model stance</th>
                        {PILLARS.map((p) => (
                            <th key={p.key} scope="col" className="ad-matrix__pillar" title={p.label}>
                                <span className="ad-matrix__pillar-name">{p.label}</span>
                            </th>
                        ))}
                        <th scope="col">Last ALPHA</th>
                        <th scope="col">Insiders</th>
                        <th scope="col"><span className="sr-only">Actions</span></th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((r) => {
                        const latest = r.latest;
                        const prev = r.previous;
                        const moved = latest && prev && latest.stance_score != null && prev.stance_score != null && latest.stance_score !== prev.stance_score;
                        return (
                            <tr key={r.ticker} className={r.stale ? 'ad-matrix__row--stale' : undefined}>
                                <th scope="row" className="ad-matrix__sticky">
                                    <button type="button" className="ad-link ad-matrix__ticker" onClick={() => onOpenTimeline(r.ticker)} title={`Thesis timeline for ${r.ticker}`}>
                                        {r.ticker}
                                    </button>
                                    {r.filings === 0 && <span className="ad-tag ad-tag--warn" title="No SEC filings ingested">no filings</span>}
                                </th>
                                <td>
                                    {latest ? (
                                        <button type="button" className="ad-cell-btn" onClick={() => onOpenRun(latest.task_id)} aria-label={`Open the ALPHA run behind ${r.ticker}'s stance`}>
                                            <StanceBadge stance={latest.stance} />
                                            {moved && (
                                                <span className={`ad-move ${latest.stance_score! > prev.stance_score! ? 'ad-move--up' : 'ad-move--down'}`}
                                                    title={`Was ${prev.stance}`}>
                                                    {latest.stance_score! > prev.stance_score! ? '▲' : '▼'}
                                                    <span className="sr-only">{`, was ${prev.stance}`}</span>
                                                </span>
                                            )}
                                        </button>
                                    ) : <span className="ad-muted">Not analysed</span>}
                                </td>
                                {PILLARS.map((p) => (
                                    <td key={p.key} className="ad-matrix__cell">
                                        {latest ? (
                                            <DirectionCell direction={latest.pillars?.[p.key] ?? null} previous={prev ? prev.pillars?.[p.key] ?? null : undefined}
                                                label={`${r.ticker} ${p.label}`} />
                                        ) : <span className="ad-muted" aria-hidden="true">·</span>}
                                    </td>
                                ))}
                                <td className={`ad-matrix__age${r.stale && latest ? ' ad-matrix__age--stale' : ''}`}
                                    title={r.stale && latest ? `Older than ${staleDays} days` : undefined}>
                                    {latest ? relativeTime(latest.created_at) : '—'}
                                </td>
                                <td className="tabular">
                                    {r.insider.trades > 0 ? (
                                        <span className={r.insider.net_usd >= 0 ? 'ad-pos' : 'ad-neg'} title={`${r.insider.trades} open-market trades since ${r.insider.since}`}>
                                            {compactUsd(r.insider.net_usd)}
                                        </span>
                                    ) : <span className="ad-muted">—</span>}
                                </td>
                                <td>
                                    <button type="button" className="ad-btn ad-btn--ghost ad-btn--sm" onClick={() => onRunAlpha(r.ticker)}
                                        disabled={busyTicker === r.ticker}>
                                        {busyTicker === r.ticker ? 'Starting…' : latest ? 'Re-run' : 'Run ALPHA'}
                                    </button>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            <p className="ad-footnote">
                Pillar marks come from the facts each ALPHA report tags as positive or negative: <span className="ad-dir ad-dir--positive">+</span> supportive,
                {' '}<span className="ad-dir ad-dir--mixed">±</span> mixed, <span className="ad-dir ad-dir--negative">−</span> weak. ▲▼ show a change since the run before.
                Model stances are generated analysis, not investment advice.
            </p>
        </div>
    );
};

export default SignalMatrix;
