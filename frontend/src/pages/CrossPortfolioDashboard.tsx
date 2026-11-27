import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout } from '../components/layout/PageLayout';
import { PageHeader } from '../components/layout/PageHeader';
import { StatTile, StatGrid } from '../components/ui/StatTile';
import { useAuth } from '../context/useAuth';
import { usePendingNavigationStore } from '../store/pendingNavigationStore';
import { useAnalysisRunsStore, openUserAnalysisStream } from '../store/analysisRunsStore';
import { portfolioApi, type DashboardPortfolioCard, type DashboardTaskSummary } from '../services/portfolioApi';
import { extractSessionId, type AnalysisTask } from '../services/analysisTasksApi';
import '../components/project/analysis-dashboard/AnalysisDashboard.css';
import './CrossPortfolioDashboard.css';

type StatusFilter = 'ALL' | 'RUNNING' | 'READY' | 'FAILED';
type AgentFilter = 'ALL' | 'rag' | 'quant';

interface PortfolioMeta {
    id: number;
    name: string;
    tickers: string[];
    description: string | null;
}

const STATUS_META: Record<string, { label: string; className: string }> = {
    PENDING: { label: 'Queued', className: 'run-pill--pending' },
    RUNNING: { label: 'Running', className: 'run-pill--running' },
    COMPLETED: { label: 'Ready', className: 'run-pill--completed' },
    FAILED: { label: 'Failed', className: 'run-pill--failed' },
};

function relativeTime(iso: string | null): string {
    if (!iso) return '—';
    const diffMs = Date.now() - new Date(iso).getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.round(hours / 24)}d ago`;
}

function dateGroupFor(iso: string | null): string {
    if (!iso) return 'Undated';
    const d = new Date(iso);
    const now = new Date();
    const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diffDays = Math.round((startOf(now) - startOf(d)) / 86400000);
    if (diffDays <= 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return 'This week';
    return 'Older';
}

function tabForTask(task: { agent_type: string }): 'Secondary Research' | 'Quantitative Research' {
    return task.agent_type === 'quant' ? 'Quantitative Research' : 'Secondary Research';
}

function matchesStatus(status: string, filter: StatusFilter): boolean {
    if (filter === 'ALL') return true;
    if (filter === 'RUNNING') return status === 'PENDING' || status === 'RUNNING';
    if (filter === 'READY') return status === 'COMPLETED';
    return status === 'FAILED';
}

function epoch(iso: string | null | undefined): number {
    if (!iso) return 0;
    const t = new Date(iso).getTime();
    return Number.isNaN(t) ? 0 : t;
}

function initialsFor(name: string): string {
    const parts = name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '?';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
}

const ICONS = {
    search: (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
    ),
    quant: (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="20" x2="18" y2="10" />
            <line x1="12" y1="20" x2="12" y2="4" />
            <line x1="6" y1="20" x2="6" y2="14" />
        </svg>
    ),
    rag: (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14,2 14,8 20,8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
        </svg>
    ),
    inbox: (
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
            <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
        </svg>
    ),
};

// The dashboard REST response and the SSE store use two different shapes
// (DashboardTaskSummary vs AnalysisTask). Convert once at the boundary so
// the store becomes the single source of truth for status/timestamps —
// same pattern the per-portfolio dashboard uses (loadInitial + SSE both
// write into useAnalysisRunsStore, nothing else tracks task state).
function toAnalysisTask(t: DashboardTaskSummary, userId: string): AnalysisTask {
    return {
        id: t.id,
        user_id: userId,
        portfolio_id: t.portfolio_id,
        agent_type: t.agent_type as AnalysisTask['agent_type'],
        task_type: t.task_type as AnalysisTask['task_type'],
        status: t.status as AnalysisTask['status'],
        progress_message: t.progress_message,
        result_metadata: t.session_id
            ? { response: { thread_id: t.session_id, session_id: t.session_id } }
            : null,
        created_at: t.created_at ?? '',
        updated_at: t.updated_at ?? '',
    };
}

function fromAnalysisTask(t: AnalysisTask, portfolioName: string | null): DashboardTaskSummary {
    return {
        id: t.id,
        portfolio_id: t.portfolio_id,
        portfolio_name: portfolioName,
        agent_type: t.agent_type,
        task_type: t.task_type,
        status: t.status,
        progress_message: t.progress_message,
        session_id: extractSessionId(t) ?? null,
        created_at: t.created_at || null,
        updated_at: t.updated_at || null,
    };
}

export const CrossPortfolioDashboard: React.FC = () => {
    const { userId } = useAuth();
    const navigate = useNavigate();
    const setPendingNavigation = usePendingNavigationStore((s) => s.setPending);

    const [portfolios, setPortfolios] = useState<PortfolioMeta[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [hasActivity, setHasActivity] = useState(false);

    const [selectedPortfolioIds, setSelectedPortfolioIds] = useState<Set<number>>(new Set());
    const [statusFilter, setStatusFilter] = useState<StatusFilter>('ALL');
    const [agentFilter, setAgentFilter] = useState<AgentFilter>('ALL');
    const [search, setSearch] = useState('');

    // The store is the ONLY place task status/timestamps live once hydrated —
    // both the initial REST snapshot and every SSE event write here, so there
    // is nothing to reconcile and no "is my copy stale" guard logic needed.
    const runsByPortfolio = useAnalysisRunsStore((s) => s.runsByPortfolio);
    const hydrateTasks = useAnalysisRunsStore((s) => s.hydrateTasks);

    const fetchDashboard = useCallback(async () => {
        if (!userId) return;
        try {
            const data = await portfolioApi.getCrossPortfolioDashboard(userId);
            setPortfolios(data.portfolios.map((p: DashboardPortfolioCard) => ({
                id: p.id,
                name: p.name,
                tickers: p.tickers,
                description: p.description,
            })));
            setHasActivity(data.recent_activity.length > 0 || data.portfolios.some((p) => p.latest_task != null));

            const seed: AnalysisTask[] = [
                ...data.portfolios.flatMap((p) => (p.latest_task ? [toAnalysisTask(p.latest_task, userId)] : [])),
                ...data.recent_activity.map((t) => toAnalysisTask(t, userId)),
            ];
            hydrateTasks(seed);
            setError(null);
        } catch (err) {
            console.error('Failed to load cross-portfolio dashboard:', err);
            setError('Could not load the Analysis Dashboard. Please try again.');
        } finally {
            setIsLoading(false);
        }
    }, [userId, hydrateTasks]);

    useEffect(() => {
        fetchDashboard();
    }, [fetchDashboard]);

    useEffect(() => {
        if (!userId) return;
        const close = openUserAnalysisStream(userId);
        return () => close();
    }, [userId]);

    const portfolioNameById = useMemo(() => {
        const m = new Map<number, string>();
        for (const p of portfolios) m.set(p.id, p.name);
        return m;
    }, [portfolios]);

    // Derived straight from the store — reacts to every SSE event automatically.
    const latestTaskByPortfolio = useMemo(() => {
        const map = new Map<number, DashboardTaskSummary>();
        for (const p of portfolios) {
            const runs = runsByPortfolio[p.id];
            if (!runs) continue;
            const values = Object.values(runs);
            if (values.length === 0) continue;
            const latest = values.reduce((a, b) => (epoch(a.updated_at) >= epoch(b.updated_at) ? a : b));
            map.set(p.id, fromAnalysisTask(latest, p.name));
        }
        return map;
    }, [portfolios, runsByPortfolio]);

    const allActivity = useMemo(() => {
        const flat: AnalysisTask[] = [];
        for (const tasks of Object.values(runsByPortfolio)) flat.push(...Object.values(tasks));
        return flat
            .map((t) => fromAnalysisTask(t, t.portfolio_id != null ? portfolioNameById.get(t.portfolio_id) ?? null : null))
            .sort((a, b) => epoch(b.updated_at) - epoch(a.updated_at))
            .slice(0, 100);
    }, [runsByPortfolio, portfolioNameById]);

    const visiblePortfolios = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return portfolios;
        return portfolios.filter(
            (p) => p.name.toLowerCase().includes(q) || p.tickers.some((t) => t.toLowerCase().includes(q))
        );
    }, [portfolios, search]);

    const scopedActivity = useMemo(() => {
        return allActivity.filter((t) => {
            if (selectedPortfolioIds.size > 0 && (t.portfolio_id == null || !selectedPortfolioIds.has(t.portfolio_id))) return false;
            if (agentFilter !== 'ALL' && t.agent_type !== agentFilter) return false;
            return true;
        });
    }, [allActivity, selectedPortfolioIds, agentFilter]);

    const kpis = useMemo(() => ({
        portfolios: portfolios.length,
        running: scopedActivity.filter((t) => matchesStatus(t.status, 'RUNNING')).length,
        ready: scopedActivity.filter((t) => matchesStatus(t.status, 'READY')).length,
        failed: scopedActivity.filter((t) => matchesStatus(t.status, 'FAILED')).length,
    }), [portfolios, scopedActivity]);

    const filteredActivity = useMemo(
        () => scopedActivity.filter((t) => matchesStatus(t.status, statusFilter)),
        [scopedActivity, statusFilter]
    );

    const groupedActivity = useMemo(() => {
        const groups = new Map<string, DashboardTaskSummary[]>();
        for (const t of filteredActivity) {
            const key = dateGroupFor(t.updated_at);
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key)!.push(t);
        }
        const order = ['Today', 'Yesterday', 'This week', 'Older', 'Undated'];
        return order.filter((k) => groups.has(k)).map((k) => [k, groups.get(k)!] as const);
    }, [filteredActivity]);

    const activeFilterCount =
        (selectedPortfolioIds.size > 0 ? 1 : 0) + (statusFilter !== 'ALL' ? 1 : 0) + (agentFilter !== 'ALL' ? 1 : 0);

    const clearAllFilters = () => {
        setSelectedPortfolioIds(new Set());
        setStatusFilter('ALL');
        setAgentFilter('ALL');
    };

    const toggleKpiStatus = (filter: StatusFilter) => {
        setStatusFilter((prev) => (prev === filter ? 'ALL' : filter));
    };

    const selectOnlyPortfolio = (portfolioId: number) => {
        setSelectedPortfolioIds((prev) => (prev.size === 1 && prev.has(portfolioId) ? new Set() : new Set([portfolioId])));
    };

    const togglePortfolioMulti = (portfolioId: number, e: React.MouseEvent) => {
        e.stopPropagation();
        setSelectedPortfolioIds((prev) => {
            const next = new Set(prev);
            if (next.has(portfolioId)) next.delete(portfolioId);
            else next.add(portfolioId);
            return next;
        });
    };

    const goToTask = (task: DashboardTaskSummary) => {
        if (task.portfolio_id == null) return;
        setPendingNavigation({
            portfolioId: String(task.portfolio_id),
            tab: tabForTask(task),
            sessionId: task.session_id ?? undefined,
        });
        navigate('/home');
    };

    const goToPortfolio = (portfolioId: number) => {
        setPendingNavigation({ portfolioId: String(portfolioId), tab: 'Secondary Research' });
        navigate('/home');
    };

    return (
        <PageLayout className="xpf-page" mode="fill">
            <PageHeader
                title="Analysis Dashboard"
                description="Every portfolio's research and quant activity in one place."
            />

            {error && <p className="xpf-page__error">{error}</p>}

            <StatGrid label="Activity summary">
                <StatTile label="Portfolios" value={kpis.portfolios} />
                <StatTile label="Running" value={kpis.running} tone="running"
                    onClick={() => toggleKpiStatus('RUNNING')} pressed={statusFilter === 'RUNNING'} />
                <StatTile label="Ready to review" value={kpis.ready} tone="ready"
                    onClick={() => toggleKpiStatus('READY')} pressed={statusFilter === 'READY'} />
                <StatTile label="Failed" value={kpis.failed} tone="failed"
                    onClick={() => toggleKpiStatus('FAILED')} pressed={statusFilter === 'FAILED'} />
            </StatGrid>

                <div className="xpf-layout">
                    <aside className="xpf-rail">
                        <div className="xpf-rail__search-wrap">
                            <span className="xpf-rail__search-icon">{ICONS.search}</span>
                            <input
                                className="xpf-rail__search"
                                type="text"
                                placeholder="Search portfolios or tickers…"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                            />
                        </div>

                        {isLoading && <p className="analysis-dashboard__empty">Loading portfolios…</p>}
                        {!isLoading && visiblePortfolios.length === 0 && (
                            <div className="xpf-empty-state">
                                <span className="xpf-empty-state__icon">{ICONS.inbox}</span>
                                <p className="xpf-empty-state__text">
                                    {portfolios.length === 0 ? 'No portfolios yet — create one from Home.' : 'No portfolios match your search.'}
                                </p>
                            </div>
                        )}

                        <ul className="xpf-rail__list">
                            {visiblePortfolios.map((p) => {
                                const latest = latestTaskByPortfolio.get(p.id);
                                const meta = latest ? STATUS_META[latest.status] : null;
                                const selected = selectedPortfolioIds.has(p.id);
                                return (
                                    <li
                                        key={p.id}
                                        className={`xpf-rail__row ${selected ? 'xpf-rail__row--selected' : ''}`}
                                        onClick={() => selectOnlyPortfolio(p.id)}
                                    >
                                        <input
                                            type="checkbox"
                                            className="xpf-rail__checkbox"
                                            checked={selected}
                                            onClick={(e) => togglePortfolioMulti(p.id, e)}
                                            onChange={() => {}}
                                            aria-label={`Include ${p.name} in filter`}
                                        />
                                        <span className="xpf-rail__avatar">
                                            {initialsFor(p.name)}
                                        </span>
                                        <div className="xpf-rail__row-body">
                                            <span className="xpf-rail__row-name">{p.name}</span>
                                            <span className="xpf-rail__row-meta">
                                                {meta ? (
                                                    <span className={`run-pill ${meta.className}`}>{meta.label}</span>
                                                ) : (
                                                    <span className="xpf-rail__row-empty">No runs yet</span>
                                                )}
                                            </span>
                                        </div>
                                        <button
                                            className="xpf-rail__open-btn"
                                            onClick={(e) => { e.stopPropagation(); goToPortfolio(p.id); }}
                                            title="Open in Home"
                                        >
                                            Open →
                                        </button>
                                    </li>
                                );
                            })}
                        </ul>
                    </aside>

                    <section className="xpf-panel">
                        <div className="xpf-panel__filterbar">
                            <select
                                className="xpf-select"
                                value={agentFilter}
                                onChange={(e) => setAgentFilter(e.target.value as AgentFilter)}
                            >
                                <option value="ALL">All agents</option>
                                <option value="rag">Secondary Research</option>
                                <option value="quant">Quantitative Research</option>
                            </select>
                            <select
                                className="xpf-select"
                                value={statusFilter}
                                onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
                            >
                                <option value="ALL">All statuses</option>
                                <option value="RUNNING">Running</option>
                                <option value="READY">Ready</option>
                                <option value="FAILED">Failed</option>
                            </select>

                            {activeFilterCount > 0 && (
                                <div className="xpf-active-filters">
                                    {selectedPortfolioIds.size > 0 && (
                                        <span className="xpf-filter-chip">
                                            {selectedPortfolioIds.size} portfolio{selectedPortfolioIds.size > 1 ? 's' : ''}
                                        </span>
                                    )}
                                    <button className="xpf-clear-btn" onClick={clearAllFilters}>Clear all</button>
                                </div>
                            )}
                        </div>

                        <div className="xpf-panel__scroll">
                            {!isLoading && filteredActivity.length === 0 && (
                                <div className="xpf-empty-state xpf-empty-state--panel">
                                    <span className="xpf-empty-state__icon">{ICONS.inbox}</span>
                                    <p className="xpf-empty-state__text">
                                        {!hasActivity
                                            ? "No analyses run yet across any portfolio. Kick one off from Home — it'll show up here immediately."
                                            : 'No activity matches the current filters.'}
                                    </p>
                                </div>
                            )}

                            {groupedActivity.map(([group, tasks]) => (
                                <div key={group} className="xpf-activity-group">
                                    <h3 className="xpf-activity-group__heading">{group}</h3>
                                    <div className="xpf-activity-list">
                                        {tasks.map((task) => {
                                            const meta = STATUS_META[task.status];
                                            const clickable = task.status === 'COMPLETED';
                                            return (
                                                <div
                                                    key={task.id}
                                                    className={`xpf-activity-row xpf-activity-row--${task.status.toLowerCase()} ${clickable ? 'xpf-activity-row--clickable' : ''}`}
                                                    onClick={clickable ? () => goToTask(task) : undefined}
                                                    role={clickable ? 'button' : undefined}
                                                    tabIndex={clickable ? 0 : undefined}
                                                >
                                                    <span className={`run-pill ${meta.className}`}>{meta.label}</span>
                                                    <span className={`xpf-activity-row__agent-icon xpf-activity-row__agent-icon--${task.agent_type}`}>
                                                        {task.agent_type === 'quant' ? ICONS.quant : ICONS.rag}
                                                    </span>
                                                    <div className="xpf-activity-row__main">
                                                        <span className="xpf-activity-row__title">
                                                            {task.portfolio_name ?? 'Unknown portfolio'} — {task.agent_type === 'quant' ? 'Quantitative Research' : 'Secondary Research'}
                                                        </span>
                                                        {task.progress_message && (
                                                            <span className="xpf-activity-row__progress">{task.progress_message}</span>
                                                        )}
                                                    </div>
                                                    <span className="xpf-activity-row__time">{relativeTime(task.updated_at)}</span>
                                                    <span className={`xpf-activity-row__action ${clickable ? '' : 'xpf-activity-row__action--muted'}`}>
                                                        {clickable ? 'View →' : '—'}
                                                    </span>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </section>
                </div>
        </PageLayout>
    );
};

export default CrossPortfolioDashboard;
