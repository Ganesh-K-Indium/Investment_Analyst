import React, { useEffect, useMemo, useState } from 'react';
import { analysisDashboardApi, type Timeline } from '../../services/analysisDashboardApi';
import { SlideOver } from './SlideOver';
import { DirectionCell, StanceBadge } from './Badges';
import { PILLARS, STANCE_ORDER, compactUsd, shortDate, stanceTone } from './format';

interface StanceTimelineProps {
    ticker: string | null;
    portfolioId?: number;
    onClose: () => void;
    onOpenRun: (taskId: string) => void;
    onCompare: (ticker: string, a: string, b: string) => void;
}

const W = 640, H = 220, PAD_L = 118, PAD_R = 16, PAD_T = 12, PAD_B = 34;
const ROWS = STANCE_ORDER;                     // Bullish (top) .. Bearish (bottom)

/** One company's model stance over every ALPHA run, read against its filing dates and monthly insider flow. */
export const StanceTimeline: React.FC<StanceTimelineProps> = ({ ticker, portfolioId, onClose, onOpenRun, onCompare }) => {
    const [data, setData] = useState<Timeline | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [hover, setHover] = useState<number | null>(null);
    const [asTable, setAsTable] = useState(false);
    const [openedAt] = useState(() => Date.now());

    useEffect(() => {
        if (!ticker) return;
        analysisDashboardApi.timeline(ticker, portfolioId).then(setData).catch((e) => setError(e instanceof Error ? e.message : 'Could not load the timeline.'));
    }, [ticker, portfolioId]);

    const geo = useMemo(() => {
        if (!data) return null;
        const pts = data.signals.filter((s) => s.created_at && s.stance_score != null);
        const times = [...pts.map((s) => new Date(s.created_at!).getTime()), ...data.filings.map((f) => new Date(f.filing_date ?? '').getTime())].filter((t) => !Number.isNaN(t));
        if (pts.length === 0) return { pts: [], filings: [], x: () => 0, y: () => 0, ticks: [] as { t: number; label: string }[] };
        const firstSignal = new Date(pts[0].created_at!).getTime();
        const t0 = Math.min(firstSignal - 30 * 86400000, ...times.filter((t) => t >= firstSignal - 365 * 86400000));
        const t1 = Math.max(openedAt, ...times);
        const x = (t: number) => PAD_L + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD_L - PAD_R);
        const y = (score: number) => PAD_T + ((2 - score) / 4) * (H - PAD_T - PAD_B);
        const ticks: { t: number; label: string }[] = [];
        const span = t1 - t0;
        const n = 4;
        for (let i = 0; i <= n; i++) {
            const t = t0 + (span * i) / n;
            ticks.push({ t, label: new Date(t).toLocaleDateString(undefined, { month: 'short', year: '2-digit' }) });
        }
        const filings = data.filings.filter((f) => f.filing_date && new Date(f.filing_date).getTime() >= t0);
        return { pts, filings, x, y, ticks };
    }, [data, openedAt]);

    if (!ticker) return null;
    const pts = geo?.pts ?? [];
    const hovered = hover != null ? pts[hover] : null;
    const latest = pts[pts.length - 1];
    const maxFlow = Math.max(1, ...(data?.insider_monthly ?? []).map((m) => Math.abs(m.net_usd)));

    return (
        <SlideOver open onClose={onClose} width="lg" title={`${ticker} thesis timeline`}
            subtitle="Model stance from every ALPHA run, with filing dates and insider flow">
            {error && <p className="ad-error" role="alert">{error}</p>}
            {!data && !error && <div className="ad-skeleton ad-skeleton--chart" aria-busy="true" aria-label="Loading timeline" />}
            {data && pts.length === 0 && <p className="ad-empty">No ALPHA run for {ticker} yet. Run one from the signal matrix to start its timeline.</p>}
            {data && geo && pts.length > 0 && (
                <div className="ad-timeline">
                    <div className="ad-timeline__head">
                        <span>Now: <StanceBadge stance={latest.stance} size="md" /></span>
                        <span className="ad-muted">{pts.length} run{pts.length === 1 ? '' : 's'}</span>
                        <button type="button" className="ad-link" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
                            {asTable ? 'Show chart' : 'Show as table'}
                        </button>
                    </div>

                    {!asTable && (
                        <div className="ad-chart" onMouseLeave={() => setHover(null)}>
                            <svg viewBox={`0 0 ${W} ${H}`} className="ad-chart__svg" role="img"
                                aria-label={`${ticker} model stance over ${pts.length} runs, latest ${latest.stance}`}>
                                {ROWS.map((s, i) => {
                                    const yy = geo.y(2 - i);
                                    return (
                                        <g key={s}>
                                            <line x1={PAD_L} x2={W - PAD_R} y1={yy} y2={yy} className={s === 'Neutral' ? 'ad-chart__zero' : 'ad-chart__grid'} />
                                            <text x={PAD_L - 8} y={yy + 4} textAnchor="end" className="ad-chart__axis">{s}</text>
                                        </g>
                                    );
                                })}
                                {geo.ticks.map((tk) => (
                                    <text key={tk.t} x={geo.x(tk.t)} y={H - 8} textAnchor="middle" className="ad-chart__axis">{tk.label}</text>
                                ))}
                                {geo.filings.map((f, i) => (
                                    <g key={`${f.filing_date}-${i}`}>
                                        <line x1={geo.x(new Date(f.filing_date!).getTime())} x2={geo.x(new Date(f.filing_date!).getTime())}
                                            y1={H - PAD_B} y2={H - PAD_B + 6} className="ad-chart__filing" />
                                        <title>{`${f.form_type ?? 'Filing'} filed ${shortDate(f.filing_date)}`}</title>
                                    </g>
                                ))}
                                <polyline fill="none" className="ad-chart__line"
                                    points={pts.map((p) => `${geo.x(new Date(p.created_at!).getTime())},${geo.y(p.stance_score!)}`).join(' ')} />
                                {pts.map((p, i) => {
                                    const cx = geo.x(new Date(p.created_at!).getTime());
                                    const cy = geo.y(p.stance_score!);
                                    return (
                                        <g key={p.task_id} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}
                                            onClick={() => onOpenRun(p.task_id)} tabIndex={0} role="button"
                                            aria-label={`${shortDate(p.created_at)}: ${p.stance}. Open run.`}
                                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenRun(p.task_id); } }}
                                            className="ad-chart__point-hit">
                                            <circle cx={cx} cy={cy} r={12} fill="transparent" />
                                            <circle cx={cx} cy={cy} r={hover === i ? 6 : 5} className={`ad-chart__point ad-stance--${stanceTone(p.stance)}`} />
                                        </g>
                                    );
                                })}
                            </svg>
                            {hovered && (
                                <div className="ad-chart__tip" style={{ left: `${(100 * geo.x(new Date(hovered.created_at!).getTime())) / W}%` }} role="status">
                                    <strong>{shortDate(hovered.created_at)}</strong>
                                    <StanceBadge stance={hovered.stance} />
                                    <span className="ad-chart__tip-pillars">
                                        {PILLARS.map((pl) => <span key={pl.key} title={pl.label}>{pl.letter}<DirectionCell direction={hovered.pillars?.[pl.key] ?? null} label={pl.label} /></span>)}
                                    </span>
                                </div>
                            )}
                            <p className="ad-footnote">Ticks under the axis mark SEC filing dates. Select a point to open that run.</p>
                        </div>
                    )}

                    {asTable && (
                        <table className="ad-mini-table">
                            <thead><tr><th scope="col">Date</th><th scope="col">Model stance</th>{PILLARS.map((p) => <th key={p.key} scope="col" title={p.label}>{p.letter}</th>)}<th scope="col"><span className="sr-only">Open</span></th></tr></thead>
                            <tbody>
                                {[...pts].reverse().map((p) => (
                                    <tr key={p.task_id}>
                                        <th scope="row">{shortDate(p.created_at)}</th>
                                        <td><StanceBadge stance={p.stance} /></td>
                                        {PILLARS.map((pl) => <td key={pl.key}><DirectionCell direction={p.pillars?.[pl.key] ?? null} label={pl.label} /></td>)}
                                        <td><button type="button" className="ad-link" onClick={() => onOpenRun(p.task_id)}>Open</button></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}

                    {pts.length >= 2 && (
                        <button type="button" className="ad-btn ad-btn--sm" onClick={() => onCompare(ticker, pts[pts.length - 2].task_id, latest.task_id)}>
                            Compare the last two runs
                        </button>
                    )}

                    {data.insider_monthly.length > 0 && (
                        <div className="ad-detail__section">
                            <h3 className="ad-detail__h">Insider open-market flow by month</h3>
                            <ul className="ad-flow" aria-label="Net insider buying (positive) or selling (negative) by month">
                                {data.insider_monthly.slice(-12).map((m) => (
                                    <li key={m.month} className="ad-flow__row">
                                        <span className="ad-flow__month">{m.month}</span>
                                        <span className="ad-flow__track">
                                            <span className={`ad-flow__bar ${m.net_usd >= 0 ? 'ad-flow__bar--pos' : 'ad-flow__bar--neg'}`}
                                                style={{ width: `${Math.max(2, (50 * Math.abs(m.net_usd)) / maxFlow)}%`, [m.net_usd >= 0 ? 'left' : 'right']: '50%' } as React.CSSProperties} />
                                        </span>
                                        <span className="ad-flow__value tabular">{compactUsd(m.net_usd)}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                    <p className="ad-footnote">Model stances are generated analysis, not investment advice.</p>
                </div>
            )}
        </SlideOver>
    );
};

export default StanceTimeline;
