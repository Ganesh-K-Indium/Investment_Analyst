import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAnalysisRunsStore, openAnalysisStream } from '../../store/analysisRunsStore';
import { analysisTasksApi, extractSessionId, type AnalysisTask } from '../../services/analysisTasksApi';
import { analysisDashboardApi, type AttentionItem, type DashboardConfig, type PortfolioPulse as Pulse } from '../../services/analysisDashboardApi';
import { StatGrid, StatTile } from '../../components/ui/StatTile';
import { SignalMatrix } from './SignalMatrix';
import { AttentionList } from './AttentionList';
import { LiveRuns } from './LiveRuns';
import { RunHistory } from './RunHistory';
import { SchedulesPanel } from './SchedulesPanel';
import { StanceMixBar } from './Badges';
import { useRunOverlays } from './useRunOverlays';
import { attentionKey, money, relativeTime, tabForTask } from './format';
import './analysis.css';

interface PortfolioPulseProps {
    portfolioId: number;
    onNavigateToTab: (tab: 'Secondary Research' | 'Quantitative Research', focusSessionId?: string) => void;
}

/** A portfolio's Overview: what we think of each holding, what needs attention, what is running, and every run so far. */
export const PortfolioPulse: React.FC<PortfolioPulseProps> = ({ portfolioId, onNavigateToTab }) => {
    const loadInitial = useAnalysisRunsStore((s) => s.loadInitial);
    // the raw map, never a derived array (see analysisRunsStore.ts: a fresh array per render loops forever)
    const runsMap = useAnalysisRunsStore((s) => s.runsByPortfolio[portfolioId]);
    const [pulse, setPulse] = useState<Pulse | null>(null);
    const [config, setConfig] = useState<DashboardConfig | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busyKey, setBusyKey] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [confirmAll, setConfirmAll] = useState(false);
    const [historyKey, setHistoryKey] = useState(0);
    const [, setParams] = useSearchParams();

    useEffect(() => {
        loadInitial(portfolioId);
        const close = openAnalysisStream(portfolioId);
        return () => close();
    }, [portfolioId, loadInitial]);

    const loadPulse = useCallback(async () => {
        try {
            setPulse(await analysisDashboardApi.pulse(portfolioId));
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not load the portfolio overview.');
        }
    }, [portfolioId]);

    useEffect(() => { setPulse(null); loadPulse(); }, [loadPulse]);
    useEffect(() => { analysisDashboardApi.config().then(setConfig).catch(() => setConfig(null)); }, []);

    // When a run ends, its stance and the attention list change: reload them once (debounced).
    const endedSignature = useMemo(
        () => Object.values(runsMap ?? {}).filter((t) => t.status !== 'PENDING' && t.status !== 'RUNNING').map((t) => `${t.id}:${t.status}`).sort().join('|'),
        [runsMap],
    );
    useEffect(() => {
        if (!endedSignature) return;
        const t = setTimeout(loadPulse, 1200);
        return () => clearTimeout(t);
    }, [endedSignature, loadPulse]);

    const liveRuns = useMemo(
        () => Object.values(runsMap ?? {}).filter((t) => t.status === 'PENDING' || t.status === 'RUNNING').sort((a, b) => b.created_at.localeCompare(a.created_at)),
        [runsMap],
    );

    const openConversation = useCallback((t: AnalysisTask) => onNavigateToTab(tabForTask(t), extractSessionId(t)), [onNavigateToTab]);
    const overlays = useRunOverlays({ portfolioId, onOpenConversation: openConversation, onChanged: () => { setHistoryKey((k) => k + 1); loadPulse(); } });

    const startAlpha = async (tickers: string[] | undefined, key: string) => {
        setBusyKey(key); setNotice(null);
        try {
            const task = await analysisTasksApi.rerunAlpha(portfolioId, tickers);
            useAnalysisRunsStore.getState().upsertTask(task);
            setNotice(`Started ALPHA on ${tickers?.join(', ') ?? 'every holding'}. Follow it under Running now.`);
        } catch (e) {
            setNotice(e instanceof Error ? e.message : 'Could not start ALPHA.');
        } finally {
            setBusyKey(null);
        }
    };

    const onAttention = async (item: AttentionItem) => {
        const key = attentionKey(item);
        const a = item.action;
        if (a.type === 'run_alpha') return startAlpha([a.ticker], key);
        if (a.type === 'ingest') return onNavigateToTab('Secondary Research');
        if (a.type === 'open_run') return overlays.openRun(a.task_id);
        if (a.type === 'compare') return overlays.openCompare(a.ticker, a.a, a.b);
        if (a.type === 'retry') {
            setBusyKey(key);
            try {
                const t = await analysisTasksApi.retry(a.task_id);
                useAnalysisRunsStore.getState().upsertTask(t);
                setNotice('Retry started.');
            } catch (e) {
                setNotice(e instanceof Error ? e.message : 'Could not retry.');
            } finally {
                setBusyKey(null);
            }
        }
    };

    const showUnreviewed = () => {
        setParams((prev) => { const n = new URLSearchParams(prev); n.set('h_unreviewed', '1'); return n; }, { replace: true });
        document.getElementById(`ad-history-${portfolioId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    const k = pulse?.kpis;
    return (
        <section className="ad-pulse" aria-label="Portfolio overview">
            <header className="ad-pulse__header">
                <div>
                    <h2 className="ad-pulse__title">Portfolio pulse</h2>
                    <p className="ad-pulse__sub">Where the latest analysis stands on each holding, what changed, and what needs you.</p>
                </div>
                <div className="ad-pulse__actions">
                    {confirmAll ? (
                        <span className="ad-inline-confirm" role="group" aria-label="Confirm ALPHA on every holding">
                            <span>Run ALPHA on all {k?.holdings ?? ''} holdings? This uses your model budget.</span>
                            <button type="button" className="ad-btn ad-btn--ghost ad-btn--sm" onClick={() => setConfirmAll(false)}>Not now</button>
                            <button type="button" className="ad-btn ad-btn--primary ad-btn--sm" disabled={busyKey === 'all'}
                                onClick={() => { setConfirmAll(false); startAlpha(undefined, 'all'); }}>Run ALPHA</button>
                        </span>
                    ) : (
                        <button type="button" className="ad-btn" onClick={() => setConfirmAll(true)} disabled={!k?.holdings || busyKey === 'all'}>
                            {busyKey === 'all' ? 'Starting…' : 'Run ALPHA on all holdings'}
                        </button>
                    )}
                </div>
            </header>

            {notice && <p className="ad-notice" role="status">{notice}</p>}
            {error && <p className="ad-error" role="alert">{error} <button type="button" className="ad-link" onClick={loadPulse}>Try again</button></p>}

            {!pulse && !error && (
                <div aria-busy="true" aria-label="Loading overview">
                    <div className="ad-skeleton ad-skeleton--tiles" />
                    <div className="ad-skeleton ad-skeleton--block" />
                </div>
            )}

            {pulse && k && (
                <>
                    <StatGrid label="Portfolio summary">
                        <StatTile label="Coverage" value={`${k.covered}/${k.holdings}`} hint={`ALPHA in the last ${pulse.stale_days} days`} />
                        <StatTile label="Model stance mix" value={<StanceMixBar mix={k.stance_mix} total={k.holdings} showLegend={false} />}
                            hint={Object.entries(k.stance_mix).map(([s, n]) => `${n} ${s}`).join(' · ') || 'No ALPHA yet'} />
                        <StatTile label="Last full ALPHA" value={relativeTime(k.last_alpha_at)} />
                        <StatTile label="Running now" value={liveRuns.length} tone="running" />
                        <StatTile label="Not reviewed" value={k.unreviewed} tone="ready" onClick={k.unreviewed ? showUnreviewed : undefined} />
                        <StatTile label="Spend this month" value={money(k.spend_month_usd)}
                            hint={`${k.tokens_month.toLocaleString()} tokens · all chats and runs here (est.)`} />
                    </StatGrid>

                    <section className="ad-card" aria-label="Signal matrix">
                        <div className="ad-card__head">
                            <h3 className="ad-card__title">Signal matrix</h3>
                            <span className="ad-card__sub">Latest ALPHA per holding. Select a ticker for its thesis timeline.</span>
                        </div>
                        <SignalMatrix rows={pulse.tickers} staleDays={pulse.stale_days} busyTicker={busyKey?.startsWith('t:') ? busyKey.slice(2) : null}
                            onOpenRun={(id) => overlays.openRun(id)} onOpenTimeline={overlays.openTimeline}
                            onRunAlpha={(t) => startAlpha([t], `t:${t}`)} />
                    </section>

                    <div className="ad-grid-2">
                        <section className="ad-card" aria-label="Needs attention">
                            <div className="ad-card__head"><h3 className="ad-card__title">Needs attention</h3>
                                {pulse.attention.length > 0 && <span className="ad-count">{pulse.attention.length}</span>}</div>
                            <AttentionList items={pulse.attention} onAction={onAttention} busyKey={busyKey} />
                        </section>
                        <section className="ad-card" aria-label="Running now">
                            <div className="ad-card__head"><h3 className="ad-card__title">Running now</h3>
                                {liveRuns.length > 0 && <span className="ad-count">{liveRuns.length}</span>}</div>
                            <LiveRuns runs={liveRuns} onOpen={(t) => overlays.openRun(t.id)} />
                        </section>
                    </div>
                </>
            )}

            <div id={`ad-history-${portfolioId}`}>
                <RunHistory portfolioId={portfolioId} live={runsMap} refreshKey={historyKey}
                    onOpen={(t) => overlays.openRun(t.id)} onOpenTimeline={overlays.openTimeline} title="Analysis history" />
            </div>

            <SchedulesPanel portfolioId={portfolioId} allowed={config?.schedules_allowed ?? false} />

            {overlays.element}
        </section>
    );
};

export default PortfolioPulse;
