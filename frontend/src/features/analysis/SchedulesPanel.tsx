import React, { useCallback, useEffect, useState } from 'react';
import { analysisDashboardApi, type Schedule, type ScheduleTrigger } from '../../services/analysisDashboardApi';
import { dateTime } from './format';

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const TRIGGER_LABEL: Record<ScheduleTrigger, string> = {
    daily: 'Every day',
    weekly: 'Every week',
    monthly: 'First day of each month',
    new_filing: 'When a new filing arrives',
};

function describe(s: Schedule): string {
    if (s.trigger === 'new_filing') return 'ALPHA on a holding when a new 10-K/10-Q for it is ingested (at most once a day)';
    const at = `${String(s.hour_utc).padStart(2, '0')}:00 UTC`;
    if (s.trigger === 'weekly') return `ALPHA every ${WEEKDAYS[s.weekday ?? 0]} at ${at}`;
    if (s.trigger === 'monthly') return `ALPHA on the 1st of each month at ${at}`;
    return `ALPHA every day at ${at}`;
}

/** Opt-in re-runs. Every schedule is created switched off; turning one on is the only way automation spends model budget. */
export const SchedulesPanel: React.FC<{ portfolioId: number; allowed: boolean }> = ({ portfolioId, allowed }) => {
    const [items, setItems] = useState<Schedule[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [adding, setAdding] = useState(false);
    const [draft, setDraft] = useState<{ trigger: ScheduleTrigger; hour_utc: number; weekday: number }>({ trigger: 'weekly', hour_utc: 6, weekday: 0 });
    const [confirmId, setConfirmId] = useState<number | null>(null);

    const load = useCallback(() => {
        analysisDashboardApi.schedules(portfolioId).then(setItems).catch((e) => setError(e instanceof Error ? e.message : 'Could not load schedules.'));
    }, [portfolioId]);
    useEffect(load, [load]);

    const run = async (fn: () => Promise<unknown>) => {
        try { await fn(); setError(null); load(); } catch (e) { setError(e instanceof Error ? e.message : 'That did not work.'); }
    };

    return (
        <section className="ad-card" aria-label="Automatic re-runs">
            <div className="ad-card__head">
                <div>
                    <h3 className="ad-card__title">Automatic re-runs</h3>
                    <p className="ad-card__sub">Off unless you switch a schedule on. Each run uses your model budget and is skipped once the monthly budget is used up.</p>
                </div>
                {allowed && !adding && <button type="button" className="ad-btn ad-btn--sm" onClick={() => setAdding(true)}>Add schedule</button>}
            </div>
            {!allowed && <p className="ad-muted">Automatic re-runs are turned off for this deployment by an administrator.</p>}
            {error && <p className="ad-error" role="alert">{error}</p>}

            {adding && (
                <form className="ad-schedule-form" onSubmit={(e) => {
                    e.preventDefault();
                    run(async () => { await analysisDashboardApi.createSchedule(portfolioId, { ...draft, enabled: false }); setAdding(false); });
                }}>
                    <label className="ad-field"><span className="ad-field__label">When</span>
                        <select className="ad-input" value={draft.trigger} onChange={(e) => setDraft({ ...draft, trigger: e.target.value as ScheduleTrigger })}>
                            {(Object.keys(TRIGGER_LABEL) as ScheduleTrigger[]).map((t) => <option key={t} value={t}>{TRIGGER_LABEL[t]}</option>)}
                        </select>
                    </label>
                    {draft.trigger === 'weekly' && (
                        <label className="ad-field"><span className="ad-field__label">Day</span>
                            <select className="ad-input" value={draft.weekday} onChange={(e) => setDraft({ ...draft, weekday: Number(e.target.value) })}>
                                {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                            </select>
                        </label>
                    )}
                    {draft.trigger !== 'new_filing' && (
                        <label className="ad-field"><span className="ad-field__label">Hour (UTC)</span>
                            <select className="ad-input" value={draft.hour_utc} onChange={(e) => setDraft({ ...draft, hour_utc: Number(e.target.value) })}>
                                {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                            </select>
                        </label>
                    )}
                    <div className="ad-schedule-form__actions">
                        <button type="button" className="ad-btn ad-btn--ghost" onClick={() => setAdding(false)}>Discard</button>
                        <button type="submit" className="ad-btn">Save (switched off)</button>
                    </div>
                </form>
            )}

            {items.length === 0 && !adding && allowed && <p className="ad-muted">No schedules. Analyses run only when you start them.</p>}
            {items.length > 0 && (
                <ul className="ad-schedules">
                    {items.map((s) => (
                        <li key={s.id} className="ad-schedule">
                            <div className="ad-schedule__body">
                                <span className="ad-schedule__title">{describe(s)}</span>
                                <span className="ad-muted">
                                    {s.enabled ? (s.next_run_at ? `Next: ${dateTime(s.next_run_at)}` : 'Waiting for a new filing') : 'Switched off'}
                                    {s.last_run_at ? ` · last: ${dateTime(s.last_run_at)}` : ''}
                                    {s.last_skip_reason ? ` · ${s.last_skip_reason}` : ''}
                                </span>
                            </div>
                            {confirmId === s.id ? (
                                <span className="ad-schedule__confirm">
                                    <span>Switch on? It will start ALPHA runs that use your model budget.</span>
                                    <button type="button" className="ad-btn ad-btn--sm ad-btn--ghost" onClick={() => setConfirmId(null)}>Keep off</button>
                                    <button type="button" className="ad-btn ad-btn--sm ad-btn--primary"
                                        onClick={() => { setConfirmId(null); run(() => analysisDashboardApi.updateSchedule(s.id, { enabled: true })); }}>Switch on</button>
                                </span>
                            ) : (
                                <label className="ad-switch">
                                    <input type="checkbox" role="switch" checked={s.enabled} disabled={!allowed && !s.enabled}
                                        onChange={(e) => (e.target.checked ? setConfirmId(s.id) : run(() => analysisDashboardApi.updateSchedule(s.id, { enabled: false })))} />
                                    <span>{s.enabled ? 'On' : 'Off'}</span>
                                </label>
                            )}
                            <button type="button" className="ad-btn ad-btn--sm ad-btn--danger-ghost" aria-label={`Delete schedule: ${describe(s)}`}
                                onClick={() => run(() => analysisDashboardApi.deleteSchedule(s.id))}>Delete</button>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
};

export default SchedulesPanel;
