import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { analysisTasksApi, type AnalysisTask, type HistoryFilters } from '../../services/analysisTasksApi';
import { StanceBadge, StatusPill, TickerChips } from './Badges';
import { STANCE_ORDER, dateGroupFor, duration, money, relativeTime, runTitle } from './format';

interface RunHistoryProps {
    /** Limit to one portfolio; omit for every run of the user. */
    portfolioId?: number;
    /** Portfolio names, for the cross-portfolio view (adds a portfolio column and filter). */
    portfolioNames?: Record<number, string>;
    /** Live copies of runs from the SSE store, keyed by id: rows show these when newer. */
    live?: Record<string, AnalysisTask>;
    onOpen: (task: AnalysisTask) => void;
    onOpenTimeline?: (ticker: string) => void;
    /** Bump to reload from the first page. */
    refreshKey?: number;
    title?: string;
}

type TypeFilter = '' | 'ask' | 'compare' | 'alpha' | 'query';

const P = 'h_';   // URL parameter prefix, so the history's filters can sit next to a page's own parameters

/** Every run, newest first, with filters kept in the address bar (shareable, survive reloads) and paging from the server. */
export const RunHistory: React.FC<RunHistoryProps> = ({ portfolioId, portfolioNames, live, onOpen, onOpenTimeline, refreshKey = 0, title = 'History' }) => {
    const [params, setParams] = useSearchParams();
    const get = (k: string) => params.get(P + k) ?? '';
    const [qInput, setQInput] = useState(get('q'));
    const [items, setItems] = useState<AnalysisTask[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const listRef = useRef<HTMLDivElement>(null);
    const sentinelRef = useRef<HTMLDivElement>(null);

    const filters: HistoryFilters = useMemo(() => {
        const pf = params.get(P + 'portfolio');
        return {
            portfolio_id: portfolioId ?? (pf && pf !== 'adhoc' ? Number(pf) : undefined),
            adhoc: portfolioId == null && pf === 'adhoc' ? true : undefined,
            status: params.get(P + 'status') || undefined,
            task_type: params.get(P + 'type') || undefined,
            ticker: params.get(P + 'ticker') || undefined,
            stance: params.get(P + 'stance') || undefined,
            q: params.get(P + 'q') || undefined,
            pinned: params.get(P + 'pinned') === '1' ? true : undefined,
            unreviewed: params.get(P + 'unreviewed') === '1' ? true : undefined,
        };
    }, [params, portfolioId]);
    const filterKey = JSON.stringify(filters);

    const setFilter = useCallback((k: string, v: string) => {
        setParams((prev) => {
            const next = new URLSearchParams(prev);
            if (v) next.set(P + k, v); else next.delete(P + k);
            return next;
        }, { replace: true });
    }, [setParams]);

    // debounce the search box into the URL
    useEffect(() => {
        const t = setTimeout(() => { if (qInput !== get('q')) setFilter('q', qInput.trim()); }, 300);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps -- only the typed text drives this
    }, [qInput]);

    const load = useCallback(async (from: string | null) => {
        const parsed: HistoryFilters = JSON.parse(filterKey);
        if (from) setLoadingMore(true); else setLoading(true);
        try {
            const page = await analysisTasksApi.history(parsed, from);
            setItems((prev) => (from ? [...prev, ...page.items.filter((i) => !prev.some((p) => p.id === i.id))] : page.items));
            setCursor(page.next_cursor);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not load the history.');
        } finally {
            setLoading(false);
            setLoadingMore(false);
        }
    }, [filterKey]);

    useEffect(() => { load(null); }, [load, refreshKey]);

    // infinite scroll, with the "Load more" button kept for keyboard users
    useEffect(() => {
        const el = sentinelRef.current;
        if (!el || !cursor || typeof IntersectionObserver === 'undefined') return;
        const io = new IntersectionObserver((entries) => {
            if (entries.some((e) => e.isIntersecting) && !loadingMore) load(cursor);
        }, { rootMargin: '200px' });
        io.observe(el);
        return () => io.disconnect();
    }, [cursor, load, loadingMore]);

    const anyFilter = Object.entries(filters).some(([k, v]) => k !== 'portfolio_id' || portfolioId == null ? v !== undefined : false);

    // rows: the newest live copy of each run, plus runs started after the list loaded (when no filter could exclude them)
    const rows = useMemo(() => {
        const byId = new Map(items.map((i) => [i.id, i]));
        const merged = items.map((i) => {
            const l = live?.[i.id];
            return l && l.updated_at >= i.updated_at ? { ...i, ...l } : i;
        });
        if (live && !anyFilter) {
            const newest = items[0]?.created_at ?? '';
            const fresh = Object.values(live).filter((t) => !byId.has(t.id) && t.created_at > newest
                && (portfolioId == null || t.portfolio_id === portfolioId));
            merged.unshift(...fresh.sort((a, b) => b.created_at.localeCompare(a.created_at)));
        }
        return merged;
    }, [items, live, anyFilter, portfolioId]);

    const groups = useMemo(() => {
        const map = new Map<string, AnalysisTask[]>();
        for (const r of rows) {
            const k = dateGroupFor(r.created_at);
            if (!map.has(k)) map.set(k, []);
            map.get(k)!.push(r);
        }
        return Array.from(map.entries());
    }, [rows]);

    const onListKey = (e: React.KeyboardEvent) => {
        if (!['j', 'k', 'ArrowDown', 'ArrowUp'].includes(e.key)) return;
        const buttons = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('.ad-run__open') ?? []);
        const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const next = e.key === 'j' || e.key === 'ArrowDown' ? Math.min(i + 1, buttons.length - 1) : Math.max(i - 1, 0);
        if (buttons[next]) { e.preventDefault(); buttons[next].focus(); }
    };

    const clearAll = () => {
        setQInput('');
        setParams((prev) => {
            const next = new URLSearchParams(prev);
            for (const k of Array.from(next.keys())) if (k.startsWith(P)) next.delete(k);
            return next;
        }, { replace: true });
    };

    const markAllReviewed = async () => {
        await analysisTasksApi.markReviewed({ portfolio_id: portfolioId });
        load(null);
    };

    return (
        <section className="ad-card ad-history" aria-label={title}>
            <div className="ad-card__head">
                <h3 className="ad-card__title">{title}</h3>
                {filters.unreviewed && rows.length > 0 && (
                    <button type="button" className="ad-btn ad-btn--ghost ad-btn--sm" onClick={markAllReviewed}>Mark all reviewed</button>
                )}
            </div>

            <div className="ad-filters" role="search">
                <label className="ad-field ad-field--grow">
                    <span className="ad-field__label">Search</span>
                    <input type="search" className="ad-input" value={qInput} onChange={(e) => setQInput(e.target.value)}
                        placeholder="Question, company or result" />
                </label>
                {portfolioNames && portfolioId == null && (
                    <label className="ad-field">
                        <span className="ad-field__label">Portfolio</span>
                        <select className="ad-input" value={get('portfolio')} onChange={(e) => setFilter('portfolio', e.target.value)}>
                            <option value="">All portfolios</option>
                            {Object.entries(portfolioNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
                            <option value="adhoc">Ad-hoc (no portfolio)</option>
                        </select>
                    </label>
                )}
                <label className="ad-field">
                    <span className="ad-field__label">Kind</span>
                    <select className="ad-input" value={get('type') as TypeFilter} onChange={(e) => setFilter('type', e.target.value)}>
                        <option value="">All kinds</option>
                        <option value="alpha">ALPHA</option>
                        <option value="ask">Research questions</option>
                        <option value="compare">Comparisons</option>
                        <option value="query">Quant</option>
                    </select>
                </label>
                <label className="ad-field">
                    <span className="ad-field__label">Status</span>
                    <select className="ad-input" value={get('status')} onChange={(e) => setFilter('status', e.target.value)}>
                        <option value="">All statuses</option>
                        <option value="RUNNING">Running</option>
                        <option value="COMPLETED">Ready</option>
                        <option value="FAILED">Failed</option>
                        <option value="CANCELLED">Cancelled</option>
                    </select>
                </label>
                <label className="ad-field">
                    <span className="ad-field__label">Stance</span>
                    <select className="ad-input" value={get('stance')} onChange={(e) => setFilter('stance', e.target.value)}>
                        <option value="">Any stance</option>
                        {STANCE_ORDER.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                </label>
                <label className="ad-field ad-field--narrow">
                    <span className="ad-field__label">Ticker</span>
                    <input className="ad-input" value={get('ticker')} placeholder="e.g. NVDA"
                        onChange={(e) => setFilter('ticker', e.target.value.toUpperCase().replace(/[^A-Z0-9.-]/g, ''))} />
                </label>
                <div className="ad-filters__toggles">
                    <label className="ad-check"><input type="checkbox" checked={!!filters.unreviewed} onChange={(e) => setFilter('unreviewed', e.target.checked ? '1' : '')} /> Not reviewed</label>
                    <label className="ad-check"><input type="checkbox" checked={!!filters.pinned} onChange={(e) => setFilter('pinned', e.target.checked ? '1' : '')} /> Pinned</label>
                    {anyFilter && <button type="button" className="ad-link" onClick={clearAll}>Clear filters</button>}
                </div>
            </div>

            {error && <p className="ad-error" role="alert">{error} <button type="button" className="ad-link" onClick={() => load(null)}>Try again</button></p>}

            {loading && rows.length === 0 && (
                <div className="ad-skeleton-list" aria-busy="true" aria-label="Loading history">
                    {Array.from({ length: 4 }).map((_, i) => <div key={i} className="ad-skeleton ad-skeleton--row" />)}
                </div>
            )}

            {!loading && rows.length === 0 && !error && (
                <p className="ad-empty">
                    {anyFilter ? 'No runs match these filters.' : 'No runs yet. Ask a question or run ALPHA from the research tabs and it will appear here as soon as it is queued.'}
                    {anyFilter && <> <button type="button" className="ad-link" onClick={clearAll}>Clear filters</button></>}
                </p>
            )}

            <div ref={listRef} className="ad-runs" onKeyDown={onListKey}>
                {groups.map(([group, runs]) => (
                    <div key={group} className="ad-runs__group">
                        <h4 className="ad-runs__heading">{group}</h4>
                        <ul className="ad-runs__list">
                            {runs.map((t) => <RunRow key={t.id} task={t} portfolioName={portfolioNames && t.portfolio_id != null ? portfolioNames[t.portfolio_id] : undefined}
                                showPortfolio={!!portfolioNames} onOpen={onOpen} onOpenTimeline={onOpenTimeline} />)}
                        </ul>
                    </div>
                ))}
            </div>

            {cursor && (
                <div ref={sentinelRef} className="ad-history__more">
                    <button type="button" className="ad-btn ad-btn--ghost" onClick={() => load(cursor)} disabled={loadingMore}>
                        {loadingMore ? 'Loading…' : 'Load more'}
                    </button>
                </div>
            )}
        </section>
    );
};

const RunRow: React.FC<{ task: AnalysisTask; portfolioName?: string; showPortfolio: boolean; onOpen: (t: AnalysisTask) => void; onOpenTimeline?: (t: string) => void }> =
    ({ task, portfolioName, showPortfolio, onOpen, onOpenTimeline }) => {
        const stances = task.outcome?.stances ? Object.values(task.outcome.stances).filter(Boolean) : [];
        const unreviewed = task.status === 'COMPLETED' && !task.reviewed_at;
        const active = task.status === 'PENDING' || task.status === 'RUNNING';
        return (
            <li className={`ad-run ad-run--${task.status.toLowerCase()}${unreviewed ? ' ad-run--unreviewed' : ''}`}>
                <button type="button" className="ad-run__open" onClick={() => onOpen(task)} aria-label={`Open ${runTitle(task)}`} />
                <div className="ad-run__status">
                    <StatusPill status={task.status} />
                    {unreviewed && <span className="ad-dot" title="Not reviewed yet" aria-label="Not reviewed yet" />}
                </div>
                <div className="ad-run__main">
                    <span className="ad-run__title">
                        {task.pinned && <span className="ad-run__pin" aria-label="Pinned" title="Pinned">★</span>}
                        {runTitle(task)}
                    </span>
                    <span className="ad-run__sub">
                        {active ? (task.progress_message || 'Waiting for a worker…')
                            : task.status === 'FAILED' ? (task.failed_step ? `Failed at ${task.failed_step}` : task.progress_message || 'Failed')
                                : task.summary || task.progress_message || ''}
                    </span>
                    <span className="ad-run__meta">
                        {showPortfolio && <span className="ad-run__portfolio">{portfolioName ?? (task.portfolio_id == null ? 'Ad-hoc' : 'Portfolio')}</span>}
                        <TickerChips tickers={task.tickers} onClick={onOpenTimeline} />
                        {task.tags?.map((tag) => <span key={tag} className="ad-tag">{tag}</span>)}
                    </span>
                </div>
                <div className="ad-run__outcome">
                    {stances.length === 1 && <StanceBadge stance={stances[0]} />}
                    {stances.length > 1 && <span className="ad-muted">{task.summary}</span>}
                </div>
                <div className="ad-run__numbers tabular">
                    <span title={task.created_at}>{relativeTime(task.created_at)}</span>
                    <span className="ad-muted">{[task.duration_ms != null ? duration(task.duration_ms) : null, task.cost_usd ? money(task.cost_usd) : null].filter(Boolean).join(' · ')}</span>
                </div>
            </li>
        );
    };

export default RunHistory;
