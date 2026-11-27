import React, { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { analysisTasksApi, getRunProgress, type AnalysisTask } from '../../services/analysisTasksApi';
import type { TickerSignal } from '../../services/analysisDashboardApi';
import { useAuth } from '../../context/useAuth';
import { useClipboardStore } from '../../store/clipboardStore';
import { SlideOver } from './SlideOver';
import { DirectionCell, StanceBadge, StatusPill, TickerChips } from './Badges';
import { ERROR_HINTS, PILLARS, dateTime, duration, money, runTitle, TASK_LABELS } from './format';

type DetailTask = AnalysisTask & { signals?: TickerSignal[] };

interface RunDetailProps {
    taskId: string | null;
    portfolioName?: string;
    onClose: () => void;
    onOpenConversation: (task: AnalysisTask) => void;
    onCompare: (ticker: string, taskId: string) => void;
    onOpenTimeline: (ticker: string) => void;
    /** A run was started (retry / run again) or removed: the parent reloads its lists. */
    onChanged?: (newTask?: AnalysisTask) => void;
}

const TAG_RE = /\[\[([+\-~])\|([\s\S]+?)\]\]/g;
const stripTags = (s: string) => s.replace(TAG_RE, (_m, _sign, text) => text);

function answerOf(task: DetailTask): string | null {
    const r = (task.result_metadata as { response?: Record<string, unknown> } | null)?.response;
    if (!r) return null;
    if (task.task_type === 'alpha' && Array.isArray(r.results)) {
        return (r.results as { report?: string }[]).map((x) => stripTags(x.report ?? '')).join('\n\n---\n\n');
    }
    const text = (r.answer ?? r.response) as unknown;
    return typeof text === 'string' ? text : null;
}

function sessionOf(task: AnalysisTask): string | undefined {
    const r = (task.result_metadata as { response?: Record<string, unknown>; session_ref?: string } | null);
    const id = task.agent_type === 'quant' ? r?.response?.session_id : r?.response?.thread_id;
    return typeof id === 'string' ? id : r?.session_ref;
}

/** One run in full: what was asked, what came out, what it cost, and what to do next. */
export const RunDetail: React.FC<RunDetailProps> = ({ taskId, portfolioName, onClose, onOpenConversation, onCompare, onOpenTimeline, onChanged }) => {
    const { userId } = useAuth();
    const [task, setTask] = useState<DetailTask | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [tagInput, setTagInput] = useState('');

    const load = useCallback(async (id: string) => {
        setError(null);
        try {
            const t = (await analysisTasksApi.get(id)) as DetailTask;
            setTask(t);
            if (t.status === 'COMPLETED' && !t.reviewed_at) {
                analysisTasksApi.update(id, { reviewed: true }).then((u) => setTask((prev) => (prev ? { ...prev, reviewed_at: u.reviewed_at } : prev))).catch(() => {});
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not load this run.');
        }
    }, []);

    useEffect(() => {
        setTask(null); setNotice(null); setConfirmDelete(false); setTagInput('');
        if (taskId) load(taskId);
    }, [taskId, load]);

    // keep an in-flight run fresh while the panel is open
    useEffect(() => {
        if (!taskId || !task || !(task.status === 'PENDING' || task.status === 'RUNNING')) return;
        const t = setTimeout(() => load(taskId), 2500);
        return () => clearTimeout(t);
    }, [task, taskId, load]);

    const act = async (name: string, fn: () => Promise<void>) => {
        setBusy(name); setNotice(null);
        try { await fn(); } catch (e) { setNotice(e instanceof Error ? e.message : 'That did not work.'); } finally { setBusy(null); }
    };

    if (!taskId) return null;
    const answer = task ? answerOf(task) : null;
    const progress = task ? getRunProgress(task) : null;
    const failedSteps = progress?.lanes.flatMap((l) => l.steps.filter((s) => s.status === 'failed').map((s) => `${l.label !== 'main' ? `${l.label} · ` : ''}${s.label}`)) ?? [];
    const active = task && (task.status === 'PENDING' || task.status === 'RUNNING');

    const footer = task && (
        <div className="ad-detail__actions">
            {task.status === 'COMPLETED' && (
                <button type="button" className="ad-btn ad-btn--primary" onClick={() => onOpenConversation(task)} disabled={!sessionOf(task)}>
                    Open conversation
                </button>
            )}
            {active && (
                <button type="button" className="ad-btn" disabled={busy === 'cancel'}
                    onClick={() => act('cancel', async () => { setTask({ ...task, ...(await analysisTasksApi.cancel(task.id)) }); onChanged?.(); })}>
                    {busy === 'cancel' ? 'Cancelling…' : 'Cancel run'}
                </button>
            )}
            {!active && task.can_retry && (
                <button type="button" className="ad-btn" disabled={busy === 'retry'} title="Starts a new model run (uses your model budget)"
                    onClick={() => act('retry', async () => { const n = await analysisTasksApi.retry(task.id); setNotice('Started a new run. It appears in the history.'); onChanged?.(n); })}>
                    {busy === 'retry' ? 'Starting…' : task.status === 'COMPLETED' ? 'Run again' : 'Retry'}
                </button>
            )}
            {task.status === 'COMPLETED' && answer && userId && (
                <button type="button" className="ad-btn" disabled={busy === 'clip'}
                    onClick={() => act('clip', async () => {
                        await useClipboardStore.getState().addItem({
                            user_id: userId, portfolio_id: task.portfolio_id ?? undefined, item_type: 'text',
                            content: answer, source: task.agent_type === 'quant' ? 'quant' : 'rag', session_id: sessionOf(task), label: runTitle(task),
                        });
                        setNotice('Added to the report clipboard (Creation tab).');
                    })}>
                    {busy === 'clip' ? 'Adding…' : 'Add to report'}
                </button>
            )}
            <span className="ad-detail__spacer" />
            {!active && !confirmDelete && (
                <button type="button" className="ad-btn ad-btn--danger-ghost" onClick={() => setConfirmDelete(true)}>Delete run</button>
            )}
        </div>
    );

    return (
        <SlideOver open={!!taskId} onClose={onClose} width="lg"
            title={task ? runTitle(task) : 'Run'}
            subtitle={task && (
                <span className="ad-detail__subtitle">
                    <StatusPill status={task.status} />
                    <span>{TASK_LABELS[task.task_type] ?? task.task_type}</span>
                    {portfolioName && <span>· {portfolioName}</span>}
                    <span>· {dateTime(task.created_at)}</span>
                </span>
            )}
            footer={footer}>
            {error && <p className="ad-error" role="alert">{error}</p>}
            {!task && !error && <div className="ad-skeleton ad-skeleton--block" aria-busy="true" aria-label="Loading run" />}
            {task && (
                <div className="ad-detail">
                    {notice && <p className="ad-notice" role="status">{notice}</p>}
                    {confirmDelete && (
                        <div className="ad-confirm" role="alertdialog" aria-label="Confirm delete">
                            <p>Delete “{runTitle(task)}” from the history? If its conversation has no answer in it (a cancelled or failed run), the conversation is deleted too; otherwise it is kept. This cannot be undone.</p>
                            <div className="ad-confirm__actions">
                                <button type="button" className="ad-btn" onClick={() => setConfirmDelete(false)}>Keep run</button>
                                <button type="button" className="ad-btn ad-btn--danger" disabled={busy === 'delete'}
                                    onClick={() => act('delete', async () => { await analysisTasksApi.remove(task.id); onChanged?.(); onClose(); })}>
                                    {busy === 'delete' ? 'Deleting…' : `Delete “${runTitle(task).slice(0, 40)}”`}
                                </button>
                            </div>
                        </div>
                    )}

                    <dl className="ad-facts">
                        <div><dt>Duration</dt><dd className="tabular">{duration(task.duration_ms)}</dd></div>
                        <div><dt>Cost (est.)</dt><dd className="tabular">{money(task.cost_usd)}</dd></div>
                        <div><dt>Tokens</dt><dd className="tabular">{task.total_tokens?.toLocaleString() ?? '—'}</dd></div>
                        <div><dt>Finished</dt><dd>{dateTime(task.finished_at)}</dd></div>
                        {task.outcome?.sources != null && <div><dt>Sources</dt><dd className="tabular">{task.outcome.sources}{task.outcome.web ? ' + web' : ''}</dd></div>}
                    </dl>

                    {(task.tickers?.length ?? 0) > 0 && (
                        <div className="ad-detail__row"><span className="ad-field__label">Companies</span><TickerChips tickers={task.tickers} max={12} onClick={onOpenTimeline} /></div>
                    )}

                    <div className="ad-detail__row">
                        <span className="ad-field__label">Tags</span>
                        <span className="ad-chips">
                            {(task.tags ?? []).map((tag) => (
                                <button key={tag} type="button" className="ad-tag ad-tag--removable" aria-label={`Remove tag ${tag}`}
                                    onClick={() => act('tag', async () => setTask({ ...task, ...(await analysisTasksApi.update(task.id, { tags: (task.tags ?? []).filter((t) => t !== tag) })) }))}>
                                    {tag} ×
                                </button>
                            ))}
                            <form className="ad-tag-form" onSubmit={(e) => {
                                e.preventDefault();
                                const tag = tagInput.trim();
                                if (!tag) return;
                                act('tag', async () => { setTask({ ...task, ...(await analysisTasksApi.update(task.id, { tags: [...(task.tags ?? []), tag] })) }); setTagInput(''); });
                            }}>
                                <label className="sr-only" htmlFor="ad-tag-input">Add a tag</label>
                                <input id="ad-tag-input" className="ad-input ad-input--sm" value={tagInput} onChange={(e) => setTagInput(e.target.value)} placeholder="Add tag" maxLength={40} />
                            </form>
                            <button type="button" className={`ad-btn ad-btn--ghost ad-btn--sm${task.pinned ? ' is-on' : ''}`} aria-pressed={!!task.pinned}
                                onClick={() => act('pin', async () => setTask({ ...task, ...(await analysisTasksApi.update(task.id, { pinned: !task.pinned })) }))}>
                                {task.pinned ? '★ Pinned' : '☆ Pin'}
                            </button>
                        </span>
                    </div>

                    {(task.status === 'FAILED' || task.status === 'CANCELLED') && (
                        <div className="ad-failure">
                            <strong>{task.status === 'CANCELLED' ? 'Cancelled' : 'This run failed'}</strong>
                            {(task.failed_step || failedSteps[0]) && <p>Stopped at: {task.failed_step || failedSteps[0]}</p>}
                            {task.progress_message && task.status === 'FAILED' && <p className="ad-failure__raw">{task.progress_message}</p>}
                            <p>{ERROR_HINTS[task.error_kind ?? 'other']}</p>
                        </div>
                    )}

                    {active && progress && (
                        <div className="ad-steps">
                            {progress.lanes.map((lane) => (
                                <div key={lane.id} className="ad-steps__lane">
                                    {lane.label !== 'main' && <span className="ad-steps__lane-label">{lane.label}</span>}
                                    <ol className="ad-steps__list">
                                        {lane.steps.map((s) => <li key={s.id} className={`ad-step ad-step--${s.status}`}>{s.label}{s.detail ? <span className="ad-muted"> · {s.detail}</span> : null}</li>)}
                                    </ol>
                                </div>
                            ))}
                        </div>
                    )}
                    {active && !progress && <p className="ad-muted">{task.progress_message || 'Waiting for a worker to pick this up…'}</p>}

                    {task.signals && task.signals.length > 0 && (
                        <div className="ad-detail__section">
                            <h3 className="ad-detail__h">Outcome by company</h3>
                            <table className="ad-mini-table">
                                <thead><tr><th scope="col">Company</th><th scope="col">Model stance</th>{PILLARS.map((p) => <th key={p.key} scope="col" title={p.label}>{p.letter}</th>)}<th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                                <tbody>
                                    {task.signals.map((s) => (
                                        <tr key={s.ticker}>
                                            <th scope="row">{s.ticker}</th>
                                            <td><StanceBadge stance={s.stance} /></td>
                                            {PILLARS.map((p) => <td key={p.key}><DirectionCell direction={s.pillars?.[p.key] ?? null} label={`${s.ticker} ${p.label}`}
                                                score={s.detail?.pillars?.[p.key]?.score} verdict={s.detail?.pillars?.[p.key]?.recommendation} /></td>)}
                                            <td><button type="button" className="ad-link" onClick={() => onCompare(s.ticker, task.id)}>Compare…</button></td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            {task.signals.filter((s) => s.key_risk).map((s) => (
                                <p key={s.ticker} className="ad-risk"><strong>{s.ticker} main risk:</strong> {s.key_risk}</p>
                            ))}
                            {task.signals.filter((s) => s.detail?.partial).map((s) => (
                                <p key={`${s.ticker}-partial`} className="ad-risk">
                                    <strong>{s.ticker}:</strong> partial analysis, {(s.detail?.missing ?? []).map((m) => PILLARS.find((p) => p.key === m)?.label ?? m).join(', ')} could not be completed{s.stance ? '; the stance uses the other pillars' : ', so no stance was given'}.
                                </p>
                            ))}
                            {task.signals.some((s) => s.detail?.pillars) && (
                                <ul className="ad-verdicts">
                                    {task.signals.flatMap((s) => PILLARS.map((p) => {
                                        const d = s.detail?.pillars?.[p.key];
                                        if (!d?.recommendation) return null;
                                        return (
                                            <li key={`${s.ticker}-${p.key}`}>
                                                <strong>{task.signals!.length > 1 ? `${s.ticker} · ` : ''}{p.label}</strong>
                                                {' '}({d.score != null && d.score > 0 ? '+' : ''}{d.score ?? '—'}){d.source === 'market_data' ? ' · from live market data' : ''}: {d.recommendation}
                                                {d.issues ? <span className="ad-muted"> · {d.issues} figure{d.issues > 1 ? 's' : ''} unconfirmed</span> : null}
                                            </li>
                                        );
                                    }))}
                                </ul>
                            )}
                        </div>
                    )}

                    {task.summary && !task.signals?.length && <p className="ad-detail__summary">{task.summary}</p>}

                    {answer && (
                        <details className="ad-detail__answer" open={task.task_type !== 'alpha'}>
                            <summary>{task.task_type === 'alpha' ? 'Full ALPHA report' : 'Answer'}</summary>
                            <div className="ad-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown></div>
                        </details>
                    )}
                    {task.task_type === 'alpha' && <p className="ad-footnote">Model stances are generated analysis, not investment advice.</p>}
                </div>
            )}
        </SlideOver>
    );
};

export default RunDetail;
