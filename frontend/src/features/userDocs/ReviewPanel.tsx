import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { userDocsApi, type ReviewItem, type ReviewState, type UserDoc } from './userDocsApi';

interface Props {
    doc: UserDoc;
    onChanged: (d: UserDoc) => void;
}

/**
 * What the readers were not sure of. Nothing doubtful is searchable until you confirm or correct it (your setting "Index low-confidence
 * text"): confirm a value as it is, pick another reading, or type the right one; a page opens up for search once nothing on it is left.
 */
export const ReviewPanel: React.FC<Props> = ({ doc, onChanged }) => {
    const [state, setState] = useState<ReviewState | null>(null);
    const [page, setPage] = useState<number | null>(null);
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [showDone, setShowDone] = useState(false);

    const load = useCallback(async () => {
        try {
            const r = await userDocsApi.review(doc.id);
            setState(r);
            setPage(prev => prev ?? r.pages.find(p => p.pending > 0)?.page ?? r.pages[0]?.page ?? null);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not load the review');
        }
    }, [doc.id]);
    useEffect(() => { void load(); }, [load, doc.status]);

    const items = useMemo(() => (state?.items ?? []).filter(i => i.page === page && (showDone || !i.resolved)), [state, page, showDone]);
    const pending = state?.pending ?? 0;
    const thisPage = state?.pages.find(p => p.page === page);

    const submit = async (key: string, body: { edits?: Record<string, string>; confirm?: string[]; confirm_pages?: number[]; skip_pages?: number[]; unskip_pages?: number[] }) => {
        setBusy(key); setError(null);
        try {
            const d = await userDocsApi.applyReview(doc.id, body);
            onChanged(d);
            await load();
            setDrafts(prev => { const n = { ...prev }; Object.keys(body.edits ?? {}).forEach(k => delete n[k]); return n; });
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not save');
        } finally {
            setBusy(null);
        }
    };

    if (!state) return <p className="ud-empty">{error ?? 'Loading…'}</p>;
    if (state.items.length === 0) return <p className="ud-empty">Nothing needs checking: every value was read with high confidence.</p>;

    const row = (it: ReviewItem) => {
        const value = drafts[it.id] ?? it.corrected_value ?? it.value ?? '';
        const changed = value !== (it.corrected_value ?? it.value ?? '');
        return (
            <li key={it.id} className={`ud-item${it.resolved ? ' ud-item--done' : ''}`}>
                <div className="ud-item__top">
                    <span className="ud-item__kind">{it.kind === 'line' ? 'Text' : it.label ?? it.kind}</span>
                    {it.resolved && <span className="ud-badge ud-badge--ready">{it.corrected_value !== null ? 'Corrected' : 'Confirmed'}</span>}
                </div>
                <label className="ud-sr" htmlFor={`rv-${it.id}`}>Value to check</label>
                <input id={`rv-${it.id}`} className="docs-input ud-item__input" value={value} onChange={e => setDrafts(d => ({ ...d, [it.id]: e.target.value }))} />
                {it.reason && <p className="ud-sub">{it.reason}</p>}
                {it.alternatives.length > 0 && (
                    <div className="ud-alts">
                        <span className="ud-sub">Other reading:</span>
                        {it.alternatives.map(a => <button key={a} type="button" className="ud-alt" onClick={() => setDrafts(d => ({ ...d, [it.id]: a }))}>{a}</button>)}
                    </div>
                )}
                <div className="ud-item__actions">
                    <Button size="sm" variant="primary" loading={busy === it.id} disabled={!changed || !value.trim()} onClick={() => submit(it.id, { edits: { [it.id]: value } })}>Save correction</Button>
                    {!it.resolved && <Button size="sm" variant="outline" disabled={changed || busy !== null} onClick={() => submit(it.id, { confirm: [it.id] })}>It is correct</Button>}
                </div>
            </li>
        );
    };

    return (
        <div className="ud-review">
            <div className="ud-review__side" role="tablist" aria-label="Pages">
                {state.pages.map(p => (
                    <button key={p.page} role="tab" aria-selected={page === p.page} className={`ud-pagebtn${page === p.page ? ' ud-pagebtn--on' : ''}`} onClick={() => setPage(p.page)}>
                        <span>Page {p.page}</span>
                        <span className={`ud-pagebtn__state${p.pending || p.unreadable ? ' ud-pagebtn__state--pending' : ''}`}>{p.skipped ? 'left out' : p.unreadable ? 'unreadable' : p.pending ? `${p.pending} to check` : p.indexed ? 'searchable' : 'clear'}</span>
                    </button>
                ))}
            </div>
            <div className="ud-review__main">
                <div className="ud-review__head">
                    <div>
                        <strong>{pending === 0 ? 'Everything is checked' : `${pending} value${pending === 1 ? '' : 's'} to check`}</strong>
                        <p className="ud-sub">{state.policy === 'flagged' ? 'Unchecked text is already searchable, marked low confidence.' : state.policy === 'never' ? 'Unchecked text stays out of search.' : 'Unchecked text stays out of search until you confirm it.'}</p>
                    </div>
                    <div className="ud-review__tools">
                        <label className="ud-check"><input type="checkbox" checked={showDone} onChange={e => setShowDone(e.target.checked)} /> Show checked</label>
                        {page !== null && !thisPage?.unreadable && (thisPage?.pending ?? 0) > 0 && (
                            <Button size="sm" variant="outline" loading={busy === `page-${page}`} onClick={() => submit(`page-${page}`, { confirm_pages: [page] })}>Confirm all on page {page}</Button>
                        )}
                    </div>
                </div>
                {error && <p className="ud-error" role="alert">{error}</p>}
                {thisPage?.skipped && (
                    <div className="ud-banner" role="status">
                        This page is left out of search.{' '}
                        <button type="button" className="set-link" disabled={busy !== null} onClick={() => submit(`unskip-${page}`, { unskip_pages: [page as number] })}>Put it back</button>
                    </div>
                )}
                {thisPage?.unreadable && (
                    <div className="ud-banner ud-banner--error" role="alert">
                        <strong>This page looks unreadable.</strong> Local OCR was unsure of {thisPage.doubtful_lines} of {thisPage.lines} lines, so checking them one by one will not help.
                        <div className="ud-actions" style={{ marginTop: 8 }}>
                            <Button size="sm" variant="primary" loading={busy === `better-${page}`} onClick={async () => {
                                if (!window.confirm('Read this page again with a vision model? This uses your model provider and may cost tokens.')) return;
                                setBusy(`better-${page}`); setError(null);
                                try { onChanged(await userDocsApi.reread(doc.id, undefined, [page as number])); } catch (e) { setError(e instanceof Error ? e.message : 'Could not start'); } finally { setBusy(null); }
                            }}>Read again with a vision model</Button>
                            <Button size="sm" variant="outline" loading={busy === `skip-${page}`} onClick={() => submit(`skip-${page}`, { skip_pages: [page as number] })}>Leave this page out</Button>
                        </div>
                    </div>
                )}
                {thisPage?.unreadable || thisPage?.skipped ? null : items.length === 0 ? <p className="ud-empty">{showDone ? 'No items on this page.' : 'Nothing left to check on this page.'}</p> : <ul className="ud-items">{items.map(row)}</ul>}
            </div>
        </div>
    );
};
