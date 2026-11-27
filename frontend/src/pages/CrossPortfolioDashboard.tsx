import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { PageLayout } from '../components/layout/PageLayout';
import { PageHeader } from '../components/layout/PageHeader';
import { StatTile, StatGrid } from '../components/ui/StatTile';
import { useAuth } from '../context/useAuth';
import { usePendingNavigationStore } from '../store/pendingNavigationStore';
import { useAnalysisRunsStore, openUserAnalysisStream } from '../store/analysisRunsStore';
import { analysisTasksApi, extractSessionId, type AnalysisTask } from '../services/analysisTasksApi';
import { analysisDashboardApi, type AttentionItem, type DashboardOverview } from '../services/analysisDashboardApi';
import { AttentionList } from '../features/analysis/AttentionList';
import { RunHistory } from '../features/analysis/RunHistory';
import { StanceBadge, StanceMixBar, StatusPill, TickerChips } from '../features/analysis/Badges';
import { useRunOverlays } from '../features/analysis/useRunOverlays';
import { attentionKey, money, relativeTime, runTitle, tabForTask } from '../features/analysis/format';
import '../components/project/analysis-dashboard/AnalysisDashboard.css';
import '../features/analysis/analysis.css';
import './CrossPortfolioDashboard.css';

/** The command centre across every portfolio: what to review, what moved, what needs attention, and all runs. */
export const CrossPortfolioDashboard: React.FC = () => {
    const { userId } = useAuth();
    const navigate = useNavigate();
    const { ticker: tickerParam } = useParams<{ ticker?: string }>();
    const setPendingNavigation = usePendingNavigationStore((s) => s.setPending);
    const hydrateTasks = useAnalysisRunsStore((s) => s.hydrateTasks);
    const runsByPortfolio = useAnalysisRunsStore((s) => s.runsByPortfolio);

    const [overview, setOverview] = useState<DashboardOverview | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [inbox, setInbox] = useState<AnalysisTask[]>([]);
    const [busyKey, setBusyKey] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [historyKey, setHistoryKey] = useState(0);

    const load = useCallback(async () => {
        try {
            const [ov, unread] = await Promise.all([
                analysisDashboardApi.overview(),
                analysisTasksApi.history({ unreviewed: true }, null, 8),
            ]);
            setOverview(ov);
            setInbox(unread.items);
            hydrateTasks(unread.items);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not load the Analysis Dashboard.');
        }
    }, [hydrateTasks]);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
        if (!userId) return;
        const close = openUserAnalysisStream(userId);
        return () => close();
    }, [userId]);

    // every run, live, for the history overlay (keyed by id across portfolios)
    const liveById = useMemo(() => {
        const out: Record<string, AnalysisTask> = {};
        for (const tasks of Object.values(runsByPortfolio)) Object.assign(out, tasks);
        return out;
    }, [runsByPortfolio]);

    // a run ending changes counts, inbox and alerts: reload once (debounced)
    const endedSignature = useMemo(
        () => Object.values(liveById).filter((t) => t.status !== 'PENDING' && t.status !== 'RUNNING').map((t) => `${t.id}:${t.status}`).sort().join('|'),
        [liveById],
    );
    useEffect(() => {
        if (!endedSignature) return;
        const t = setTimeout(load, 1500);
        return () => clearTimeout(t);
    }, [endedSignature, load]);

    const running = useMemo(() => Object.values(liveById).filter((t) => t.status === 'PENDING' || t.status === 'RUNNING').length, [liveById]);

    const portfolioNames = useMemo(() => {
        const m: Record<number, string> = {};
        for (const p of overview?.portfolios ?? []) m[p.id] = p.name;
        return m;
    }, [overview]);

    const openConversation = useCallback((task: AnalysisTask) => {
        if (task.portfolio_id == null) return;
        setPendingNavigation({ portfolioId: String(task.portfolio_id), tab: tabForTask(task), sessionId: extractSessionId(task) });
        navigate('/home');
    }, [navigate, setPendingNavigation]);

    const overlays = useRunOverlays({ portfolioNames, onOpenConversation: openConversation, onChanged: () => { setHistoryKey((k) => k + 1); load(); } });

    // /dashboard/ticker/:ticker opens that company's timeline (shareable link)
    const { openTimeline } = overlays;
    useEffect(() => { if (tickerParam) openTimeline(tickerParam.toUpperCase()); }, [tickerParam, openTimeline]);

    const openPortfolio = (portfolioId: number) => {
        setPendingNavigation({ portfolioId: String(portfolioId), tab: 'Overview' });
        navigate('/home');
    };

    const onAlert = async (item: AttentionItem) => {
        const key = attentionKey(item);
        const a = item.action;
        setNotice(null);
        if (a.type === 'open_run') return overlays.openRun(a.task_id, item.portfolio_id);
        if (a.type === 'compare') return overlays.openCompare(a.ticker, a.a, a.b);
        if (a.type === 'ingest') {
            if (item.portfolio_id != null) {
                setPendingNavigation({ portfolioId: String(item.portfolio_id), tab: 'Secondary Research' });
                navigate('/home');
            }
            return;
        }
        setBusyKey(key);
        try {
            const t = a.type === 'retry' ? await analysisTasksApi.retry(a.task_id) : await analysisTasksApi.rerunAlpha(item.portfolio_id!, [a.ticker]);
            useAnalysisRunsStore.getState().upsertTask(t);
            setNotice(a.type === 'retry' ? 'Retry started.' : `Started ALPHA on ${a.ticker}.`);
        } catch (e) {
            setNotice(e instanceof Error ? e.message : 'Could not start the run.');
        } finally {
            setBusyKey(null);
        }
    };

    const markAllReviewed = async () => {
        await analysisTasksApi.markReviewed({});
        load();
        setHistoryKey((k) => k + 1);
    };

    const totals = overview?.totals;
    return (
        <PageLayout className="xpf-page" mode="scroll">
            <PageHeader title="Analysis Dashboard" description="Every portfolio's research in one place: what to review, what moved, and what needs attention." />

            {error && <p className="ad-error" role="alert">{error} <button type="button" className="ad-link" onClick={load}>Try again</button></p>}
            {notice && <p className="ad-notice" role="status">{notice}</p>}

            {!overview && !error && (
                <div aria-busy="true" aria-label="Loading dashboard">
                    <div className="ad-skeleton ad-skeleton--tiles" />
                    <div className="ad-skeleton ad-skeleton--block" />
                </div>
            )}

            {overview && totals && (
                <div className="ad-command">
                    <StatGrid label="Across all portfolios">
                        <StatTile label="Portfolios" value={overview.portfolios.length} />
                        <StatTile label="To review" value={overview.inbox} tone="ready" />
                        <StatTile label="Running now" value={running} tone="running" />
                        <StatTile label="Failed (7 days)" value={totals.failed_7d} tone="failed" />
                        <StatTile label="Coverage" value={`${totals.covered}/${totals.holdings}`} hint="Holdings with a current ALPHA" />
                        <StatTile label="Spend this month" value={money(totals.spend_month_usd)} hint={`${totals.tokens_month.toLocaleString()} tokens · everything you ran (est.)`} />
                    </StatGrid>

                    <div className="ad-grid-2">
                        <section className="ad-card" aria-label="Review inbox">
                            <div className="ad-card__head">
                                <h3 className="ad-card__title">Review inbox</h3>
                                {overview.inbox > 0 && <button type="button" className="ad-btn ad-btn--ghost ad-btn--sm" onClick={markAllReviewed}>Mark all reviewed</button>}
                            </div>
                            {inbox.length === 0 ? <p className="ad-empty ad-empty--ok">You're up to date: no finished runs waiting for review.</p> : (
                                <ul className="ad-inbox">
                                    {inbox.map((t) => (
                                        <li key={t.id}>
                                            <button type="button" className="ad-inbox__item" onClick={() => overlays.openRun(t.id, t.portfolio_id)}>
                                                <StatusPill status={t.status} />
                                                <span className="ad-inbox__main">
                                                    <span className="ad-inbox__title">{runTitle(t)}</span>
                                                    <span className="ad-muted">{t.portfolio_id != null ? portfolioNames[t.portfolio_id] ?? 'Portfolio' : 'Ad-hoc'} · {relativeTime(t.finished_at ?? t.updated_at)}</span>
                                                </span>
                                                {t.outcome?.stances && Object.keys(t.outcome.stances).length === 1 && <StanceBadge stance={Object.values(t.outcome.stances)[0]} />}
                                            </button>
                                        </li>
                                    ))}
                                    {overview.inbox > inbox.length && <li className="ad-muted ad-inbox__more">+{overview.inbox - inbox.length} more in the history below (filter: Not reviewed)</li>}
                                </ul>
                            )}
                        </section>

                        <section className="ad-card" aria-label="Book-wide conviction">
                            <div className="ad-card__head"><h3 className="ad-card__title">Model stance across holdings</h3></div>
                            {Object.keys(overview.conviction).length === 0 ? <p className="ad-empty">No ALPHA runs yet. Run ALPHA from a portfolio's Overview to fill this in.</p> : (
                                <StanceMixBar mix={overview.conviction} />
                            )}
                            <h4 className="ad-card__subhead">Moved this week</h4>
                            {overview.movers.length === 0 ? <p className="ad-muted">No stance changes in the last 7 days.</p> : (
                                <ul className="ad-movers">
                                    {overview.movers.map((m) => (
                                        <li key={`${m.ticker}-${m.b}`}>
                                            <button type="button" className="ad-movers__item" onClick={() => overlays.openCompare(m.ticker, m.a, m.b)}>
                                                <strong>{m.ticker}</strong>
                                                <StanceBadge stance={m.from} /><span aria-hidden="true">→</span><StanceBadge stance={m.to} />
                                                <span className={m.change > 0 ? 'ad-pos' : 'ad-neg'}>{m.change > 0 ? '▲ Upgrade' : '▼ Downgrade'}</span>
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                            <p className="ad-footnote">Model stances are generated analysis, not investment advice.</p>
                        </section>
                    </div>

                    <section className="ad-card" aria-label="Alerts">
                        <div className="ad-card__head"><h3 className="ad-card__title">Alerts</h3>{overview.alerts.length > 0 && <span className="ad-count">{overview.alerts.length}</span>}</div>
                        <AttentionList items={overview.alerts} onAction={onAlert} busyKey={busyKey} showPortfolio limit={6}
                            emptyText="No alerts: no downgrades, new filings since the last analysis, or failed runs." />
                    </section>

                    <section aria-label="Portfolios">
                        <h3 className="ad-section-title">Portfolios</h3>
                        {overview.portfolios.length === 0 ? (
                            <p className="ad-empty">No portfolios yet. <button type="button" className="ad-link" onClick={() => navigate('/home')}>Create one on Home</button></p>
                        ) : (
                            <ul className="ad-cards">
                                {overview.portfolios.map((p) => (
                                    <li key={p.id} className="ad-pcard">
                                        <button type="button" className="ad-pcard__open" onClick={() => openPortfolio(p.id)} aria-label={`Open ${p.name}`} />
                                        <div className="ad-pcard__head">
                                            <span className="ad-pcard__name">{p.name}</span>
                                            {p.high_alerts > 0 && <span className="ad-tag ad-tag--alert">{p.high_alerts} alert{p.high_alerts > 1 ? 's' : ''}</span>}
                                        </div>
                                        <TickerChips tickers={p.tickers} max={6} onClick={overlays.openTimeline} />
                                        <StanceMixBar mix={p.stance_mix} total={p.holdings} showLegend={false} />
                                        <dl className="ad-pcard__stats">
                                            <div><dt>Coverage</dt><dd className="tabular">{p.coverage_pct}%</dd></div>
                                            <div><dt>To review</dt><dd className="tabular">{p.unreviewed}</dd></div>
                                            <div><dt>Running</dt><dd className="tabular">{p.running}</dd></div>
                                            <div><dt>This month</dt><dd className="tabular">{money(p.spend_month_usd)}</dd></div>
                                        </dl>
                                        <span className="ad-muted ad-pcard__foot">Last activity {relativeTime(p.last_activity_at)}</span>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </section>
                </div>
            )}

            <RunHistory portfolioNames={portfolioNames} live={liveById} refreshKey={historyKey} title="All runs"
                onOpen={(t) => overlays.openRun(t.id, t.portfolio_id)} onOpenTimeline={overlays.openTimeline} />

            {overlays.element}
        </PageLayout>
    );
};

export default CrossPortfolioDashboard;
