import React, { useEffect, useMemo, useState } from 'react';
import { analysisTasksApi, type AnalysisTask, type RunComparison } from '../../services/analysisTasksApi';
import { SlideOver } from './SlideOver';
import { DirectionCell, StanceBadge } from './Badges';
import { PILLARS, dateTime } from './format';

interface RunCompareProps {
    ticker: string | null;
    /** Runs to start from: `b` (the later one) and optionally `a`; otherwise the two newest. */
    a?: string;
    b?: string;
    portfolioId?: number;
    onClose: () => void;
    onOpenRun: (taskId: string) => void;
}

/** Two ALPHA runs of one company side by side: stance, each pillar, and the facts that appeared or disappeared. */
export const RunCompare: React.FC<RunCompareProps> = ({ ticker, a, b, portfolioId, onClose, onOpenRun }) => {
    const [runs, setRuns] = useState<AnalysisTask[]>([]);
    const [pick, setPick] = useState<{ a?: string; b?: string }>({});
    const [diff, setDiff] = useState<RunComparison | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!ticker) return;
        analysisTasksApi.history({ task_type: 'alpha', ticker, status: 'COMPLETED', portfolio_id: portfolioId }, null, 50)
            .then((page) => {
                setRuns(page.items);
                const ids = page.items.map((r) => r.id);
                const later = b && ids.includes(b) ? b : ids[0];
                const idx = ids.indexOf(later ?? '');
                const earlier = a && ids.includes(a) ? a : ids[idx + 1];
                setPick({ a: earlier, b: later });
            })
            .catch((e) => setError(e instanceof Error ? e.message : 'Could not load runs.'));
    }, [ticker, a, b, portfolioId]);

    useEffect(() => {
        if (!ticker || !pick.a || !pick.b || pick.a === pick.b) return;
        let live = true;
        analysisTasksApi.compare(pick.a, pick.b, ticker)
            .then((d) => { if (live) { setDiff(d); setError(null); } })
            .catch((e) => { if (live) setError(e instanceof Error ? e.message : 'Could not compare these runs.'); });
        return () => { live = false; };
    }, [ticker, pick.a, pick.b]);

    // the diff on screen always belongs to the two runs picked; while the next one loads, show the placeholder
    const current = diff && pick.a !== pick.b && new Set([diff.a.id, diff.b.id]).has(pick.a ?? '') && new Set([diff.a.id, diff.b.id]).has(pick.b ?? '') ? diff : null;
    const loading = !current && !!pick.a && !!pick.b && pick.a !== pick.b && !error;

    const options = useMemo(() => runs.map((r) => ({ id: r.id, label: `${dateTime(r.created_at)}${r.outcome?.stances?.[ticker ?? ''] ? ` — ${r.outcome.stances[ticker ?? '']}` : ''}` })), [runs, ticker]);

    if (!ticker) return null;
    return (
        <SlideOver open onClose={onClose} width="lg" title={`What changed: ${ticker}`} subtitle="Compare two ALPHA runs of the same company">
            {error && <p className="ad-error" role="alert">{error}</p>}
            {runs.length < 2 && !error && (
                <p className="ad-empty">{runs.length === 0 ? 'Loading runs…' : `${ticker} has only one ALPHA run so far. Run it again later to see what changed.`}</p>
            )}
            {runs.length >= 2 && (
                <div className="ad-compare">
                    <div className="ad-compare__pickers">
                        <label className="ad-field"><span className="ad-field__label">Earlier run</span>
                            <select className="ad-input" value={pick.a ?? ''} onChange={(e) => setPick((p) => ({ ...p, a: e.target.value }))}>
                                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                            </select>
                        </label>
                        <span aria-hidden="true" className="ad-compare__arrow">→</span>
                        <label className="ad-field"><span className="ad-field__label">Later run</span>
                            <select className="ad-input" value={pick.b ?? ''} onChange={(e) => setPick((p) => ({ ...p, b: e.target.value }))}>
                                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                            </select>
                        </label>
                    </div>
                    {pick.a === pick.b && <p className="ad-muted">Pick two different runs.</p>}
                    {loading && <div className="ad-skeleton ad-skeleton--block" aria-busy="true" />}
                    {current && (
                        <>
                            <div className="ad-compare__stance">
                                <StanceBadge stance={current.stance.before} size="md" />
                                <span aria-hidden="true">→</span>
                                <StanceBadge stance={current.stance.after} size="md" />
                                {current.stance.change != null && current.stance.change !== 0 && (
                                    <span className={current.stance.change > 0 ? 'ad-pos' : 'ad-neg'}>{current.stance.change > 0 ? 'Upgraded' : 'Downgraded'}</span>
                                )}
                                {current.stance.change === 0 && <span className="ad-muted">Stance unchanged</span>}
                            </div>
                            <table className="ad-mini-table">
                                <thead><tr><th scope="col">Pillar</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
                                <tbody>
                                    {PILLARS.map((p) => {
                                        const d = current.pillars[p.key];
                                        return (
                                            <tr key={p.key} className={d && d.before !== d.after ? 'is-changed' : undefined}>
                                                <th scope="row">{p.label}</th>
                                                <td><DirectionCell direction={d?.before ?? null} label={`${p.label} before`} /></td>
                                                <td><DirectionCell direction={d?.after ?? null} previous={d?.before ?? null} label={`${p.label} after`} /></td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                            <FactList title="New concerns" tone="neg" items={current.new_negatives} empty="No new negative facts." />
                            <FactList title="New strengths" tone="pos" items={current.new_positives} empty="No new positive facts." />
                            <FactList title="Concerns no longer mentioned" tone="muted" items={current.dropped_negatives} empty="None." />
                            <details className="ad-detail__answer">
                                <summary>Side-by-side text by pillar</summary>
                                {[...PILLARS.map((p) => p.key as string), 'summary'].map((k) => (
                                    <div key={k} className="ad-compare__text">
                                        <h4>{k === 'summary' ? 'Summary' : PILLARS.find((p) => p.key === k)?.label}</h4>
                                        <div className="ad-compare__cols">
                                            <p>{current.sections_before[k] || '—'}</p>
                                            <p>{current.sections_after[k] || '—'}</p>
                                        </div>
                                    </div>
                                ))}
                            </details>
                            <div className="ad-detail__actions">
                                <button type="button" className="ad-btn ad-btn--ghost ad-btn--sm" onClick={() => onOpenRun(current.a.id)}>Open earlier run</button>
                                <button type="button" className="ad-btn ad-btn--ghost ad-btn--sm" onClick={() => onOpenRun(current.b.id)}>Open later run</button>
                            </div>
                        </>
                    )}
                </div>
            )}
        </SlideOver>
    );
};

const FactList: React.FC<{ title: string; items: string[]; tone: 'pos' | 'neg' | 'muted'; empty: string }> = ({ title, items, tone, empty }) => (
    <div className="ad-detail__section">
        <h3 className="ad-detail__h">{title}</h3>
        {items.length === 0 ? <p className="ad-muted">{empty}</p> : (
            <ul className={`ad-facts-list ad-facts-list--${tone}`}>{items.map((f) => <li key={f}>{f}</li>)}</ul>
        )}
    </div>
);

export default RunCompare;
