import React from 'react';
import { analysisTasksApi, getRunProgress, type AnalysisTask } from '../../services/analysisTasksApi';
import { StatusPill, TickerChips } from './Badges';
import { relativeTime, runTitle } from './format';

/** Runs in flight, with how far along each is. Updates arrive over the live stream. */
export const LiveRuns: React.FC<{ runs: AnalysisTask[]; onOpen: (t: AnalysisTask) => void }> = ({ runs, onOpen }) => {
    const [cancelling, setCancelling] = React.useState<string | null>(null);
    if (runs.length === 0) return <p className="ad-empty">Nothing running. Runs you start keep going even if you leave this page.</p>;
    return (
        <ul className="ad-live">
            {runs.map((t) => {
                const p = getRunProgress(t);
                const steps = p?.lanes.flatMap((l) => l.steps) ?? [];
                const done = steps.filter((s) => s.status === 'done').length;
                const pct = steps.length ? Math.round((100 * done) / steps.length) : null;
                return (
                    <li key={t.id} className="ad-live__item">
                        <div className="ad-live__top">
                            <StatusPill status={t.status} />
                            <button type="button" className="ad-link ad-live__title" onClick={() => onOpen(t)}>{runTitle(t)}</button>
                            <span className="ad-muted tabular">{relativeTime(t.created_at)}</span>
                        </div>
                        <TickerChips tickers={t.tickers} max={6} />
                        <span className="ad-live__msg">{t.progress_message || (t.status === 'PENDING' ? 'Queued: waiting for a worker' : 'Starting…')}</span>
                        {pct != null && (
                            <div className="ad-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={`${runTitle(t)} progress`}>
                                <span className="ad-progress__fill" style={{ width: `${pct}%` }} />
                            </div>
                        )}
                        <button type="button" className="ad-btn ad-btn--ghost ad-btn--sm ad-live__cancel" disabled={cancelling === t.id}
                            onClick={async () => { setCancelling(t.id); try { await analysisTasksApi.cancel(t.id); } finally { setCancelling(null); } }}>
                            {cancelling === t.id ? 'Cancelling…' : 'Cancel'}
                        </button>
                    </li>
                );
            })}
        </ul>
    );
};

export default LiveRuns;
