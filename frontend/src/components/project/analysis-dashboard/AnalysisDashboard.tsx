import React, { useEffect } from 'react';
import { useAnalysisRunsStore, openAnalysisStream } from '../../../store/analysisRunsStore';
import { extractSessionId, type AnalysisTask } from '../../../services/analysisTasksApi';
import './AnalysisDashboard.css';

interface AnalysisDashboardProps {
    portfolioId: number;
    onNavigateToTab: (tab: 'Secondary Research' | 'Quantitative Research', focusSessionId?: string) => void;
}

const TASK_LABELS: Record<AnalysisTask['task_type'], string> = {
    ask: 'Secondary Research — Ask',
    compare: 'Secondary Research — Compare',
    alpha: 'Secondary Research — ALPHA',
    query: 'Quantitative Research',
};

const STATUS_META: Record<AnalysisTask['status'], { label: string; className: string }> = {
    PENDING: { label: 'Queued', className: 'run-pill--pending' },
    RUNNING: { label: 'Running', className: 'run-pill--running' },
    COMPLETED: { label: 'Ready', className: 'run-pill--completed' },
    FAILED: { label: 'Failed', className: 'run-pill--failed' },
};

function relativeTime(iso: string): string {
    const diffMs = Date.now() - new Date(iso).getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.round(hours / 24)}d ago`;
}

function tabForTask(task: AnalysisTask): 'Secondary Research' | 'Quantitative Research' {
    return task.agent_type === 'quant' ? 'Quantitative Research' : 'Secondary Research';
}

export const AnalysisDashboard: React.FC<AnalysisDashboardProps> = ({ portfolioId, onNavigateToTab }) => {
    const loadInitial = useAnalysisRunsStore((s) => s.loadInitial);
    // Raw, stable map — see the long comment in analysisRunsStore.ts for why
    // this must NOT be store.runsFor(portfolioId) called as a selector.
    const runsMap = useAnalysisRunsStore((s) => s.runsByPortfolio[portfolioId]);
    const isLoading = useAnalysisRunsStore((s) => s.isLoading[portfolioId]);

    useEffect(() => {
        loadInitial(portfolioId);
        // Each effect instance opens and closes its OWN stream — safe under
        // React 18 StrictMode's dev-mode double-invoke, unlike a stream
        // handle stored in shared Zustand state (see analysisRunsStore.ts).
        const close = openAnalysisStream(portfolioId);
        return () => close();
    }, [portfolioId, loadInitial]);

    const runs = React.useMemo(
        () => Object.values(runsMap ?? {}).sort((a, b) => b.created_at.localeCompare(a.created_at)),
        [runsMap]
    );

    const running = runs.filter((t) => t.status === 'PENDING' || t.status === 'RUNNING');
    const ready = runs.filter((t) => t.status === 'COMPLETED');
    const failed = runs.filter((t) => t.status === 'FAILED');
    const recent = runs.slice(0, 12);

    return (
        <section className="analysis-dashboard">
            <header className="analysis-dashboard__header">
                <div className="analysis-dashboard__heading-block">
                    <h2 className="analysis-dashboard__title">Analysis Activity</h2>
                    <p className="analysis-dashboard__subtitle">
                        Every research and quant run for this portfolio — kicked off here keeps
                        running even if you navigate away.
                    </p>
                </div>
                <div className="analysis-dashboard__actions">
                    {/* <button className="analysis-quick-action" onClick={() => onNavigateToTab('Secondary Research')}>
                        Secondary Research
                    </button>
                    <button className="analysis-quick-action" onClick={() => onNavigateToTab('Quantitative Research')}>
                        Quantitative Research
                    </button> */}
                </div>
            </header>

            <div className="analysis-dashboard__summary">
                <div className="analysis-summary-tile">
                    <span className="analysis-summary-tile__count">{running.length}</span>
                    <span className="analysis-summary-tile__label">Running</span>
                </div>
                <div className="analysis-summary-tile__divider" />
                <div className="analysis-summary-tile">
                    <span className="analysis-summary-tile__count analysis-summary-tile__count--ready">{ready.length}</span>
                    <span className="analysis-summary-tile__label">Ready to review</span>
                </div>
                <div className="analysis-summary-tile__divider" />
                <div className="analysis-summary-tile">
                    <span className="analysis-summary-tile__count analysis-summary-tile__count--failed">{failed.length}</span>
                    <span className="analysis-summary-tile__label">Failed</span>
                </div>
            </div>

            <div className="analysis-dashboard__list-section">
                <h3 className="analysis-dashboard__list-heading">Recent runs</h3>

                {isLoading && recent.length === 0 && (
                    <p className="analysis-dashboard__empty">Loading analysis history…</p>
                )}

                {!isLoading && recent.length === 0 && (
                    <p className="analysis-dashboard__empty">
                        No analyses run yet. Start one above — it'll show up here the moment it's queued.
                    </p>
                )}

                <div className="analysis-run-list">
                    {recent.map((task) => {
                        const meta = STATUS_META[task.status];
                        return (
                            <div key={task.id} className="analysis-run-row">
                                <span className={`run-pill ${meta.className}`}>{meta.label}</span>
                                <div className="analysis-run-row__body">
                                    <span className="analysis-run-row__title">{TASK_LABELS[task.task_type]}</span>
                                    {task.progress_message && (
                                        <span className="analysis-run-row__progress">{task.progress_message}</span>
                                    )}
                                </div>
                                <span className="analysis-run-row__time">{relativeTime(task.updated_at)}</span>
                                {task.status === 'COMPLETED' ? (
                                    <button
                                        className="analysis-run-row__view-btn"
                                        onClick={() => onNavigateToTab(tabForTask(task), extractSessionId(task))}
                                    >
                                        View →
                                    </button>
                                ) : (
                                    <span className="analysis-run-row__view-btn-spacer" />
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        </section>
    );
};

export default AnalysisDashboard;
