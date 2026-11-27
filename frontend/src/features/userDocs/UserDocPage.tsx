import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Sidebar } from '../../components/layout/Sidebar';
import { Header } from '../../components/layout/Header';
import { Button } from '../../components/ui/Button';
import { ReviewPanel } from './ReviewPanel';
import { PdfViewer } from '../documents/PdfViewer';
import { API_BASE } from '../documents/documentsApi';
import { STATUS_LABEL, userDocsApi, type DocContent, type DocTypes, type UserDoc } from './userDocsApi';
import '../documents/DocumentsPage.css';
import '../documents/Inspector.css';
import '../library/LibraryTitle.css';
import './MyDocuments.css';

type Tab = 'content' | 'review' | 'details';
const NO_OVERLAYS: never[] = [];
const BUSY = new Set(['queued', 'processing']);

/** /documents/mine/:id: what was read (any format), what needs checking, and the document's details. */
export const UserDocPage: React.FC = () => {
    const { id = '' } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const [params, setParams] = useSearchParams();
    const [doc, setDoc] = useState<UserDoc | null>(null);
    const [types, setTypes] = useState<DocTypes | null>(null);
    const [content, setContent] = useState<DocContent | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [edit, setEdit] = useState<{ doc_type: string; entity: string; period: string; tags: string; label: string } | null>(null);
    const [busy, setBusy] = useState(false);
    const columnRef = useRef<HTMLDivElement>(null);
    const [visible, setVisible] = useState(1);
    const [pdfError, setPdfError] = useState<string | null>(null);
    const tab = (params.get('tab') as Tab) || 'content';
    const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });

    const load = useCallback(async () => {
        try { setDoc(await userDocsApi.get(id)); setError(null); } catch (e) { setError(e instanceof Error ? e.message : 'Could not load the document'); }
    }, [id]);
    useEffect(() => { void load(); userDocsApi.types().then(setTypes).catch(() => undefined); }, [load]);
    useEffect(() => {
        if (!doc || !BUSY.has(doc.status)) return;
        const t = window.setInterval(() => void load(), 2000);
        return () => window.clearInterval(t);
    }, [doc, load]);
    useEffect(() => {
        if (doc && !BUSY.has(doc.status) && doc.status !== 'failed') userDocsApi.content(id).then(setContent).catch(() => setContent(null));
    }, [id, doc?.status, doc?.review_pending]); // eslint-disable-line react-hooks/exhaustive-deps

    const open = async (kind: 'pdf' | 'file') => {
        try {
            const blob = await (kind === 'pdf' ? userDocsApi.pdf(id) : userDocsApi.file(id));
            const url = URL.createObjectURL(blob);
            if (kind === 'pdf') window.open(url, '_blank', 'noopener');
            else { const a = document.createElement('a'); a.href = url; a.download = doc?.filename ?? 'document'; a.click(); }
            window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
        } catch (e) { setError(e instanceof Error ? e.message : 'Could not open the file'); }
    };

    const onVisible = useCallback((n: number) => setVisible(n), []);
    const approve = async () => {
        setBusy(true);
        try { setDoc(await userDocsApi.approve(id)); setError(null); } catch (e) { setError(e instanceof Error ? e.message : 'Could not approve'); } finally { setBusy(false); }
    };
    useEffect(() => {
        const col = columnRef.current, el = document.getElementById(`ud-page-${visible}`);
        if (col && el) col.scrollTo({ top: el.offsetTop - col.offsetTop - 8, behavior: 'smooth' });
    }, [visible]);

    const saveDetails = async () => {
        if (!edit) return;
        setBusy(true);
        try {
            setDoc(await userDocsApi.patch(id, { doc_type: edit.doc_type || undefined, entity: edit.entity, period: edit.period, label: edit.label, tags: edit.tags.split(',').map(t => t.trim()).filter(Boolean) }));
            setEdit(null);
        } catch (e) { setError(e instanceof Error ? e.message : 'Could not save'); } finally { setBusy(false); }
    };

    if (!doc) return (
        <div className="docs-page ud-page"><Sidebar /><Header /><main className="docs-page__main">{error ? <p className="ud-error" role="alert">{error}</p> : <p className="ud-empty">Loading…</p>}
            <Button variant="outline" onClick={() => navigate('/documents/mine')}>Back to My documents</Button></main></div>
    );

    return (
        <div className="docs-page ud-page">
            <Sidebar />
            <Header />
            <main className="docs-page__main">
                <div className="docs-head">
                    <div>
                        <h1 className="docs-title">
                            <Link to="/documents/mine" className="lib-crumb">My documents</Link><span className="lib-crumb-sep" aria-hidden="true">/</span><span className="lib-crumb-current" aria-current="page" title={doc.filename}>{doc.filename}</span>
                        </h1>
                        <p className="docs-subtitle">
                            <span className={`ud-badge ud-badge--${doc.status}`}>{STATUS_LABEL[doc.status]}</span>{' '}
                            {doc.doc_type_label ?? 'Document'}{doc.entity ? ` · ${doc.entity}` : ''}{doc.period ? ` · ${doc.period}` : ''} · {doc.pages} page{doc.pages === 1 ? '' : 's'}, {doc.pages_indexed} searchable
                        </p>
                    </div>
                    {doc.status === 'awaiting_approval' && doc.owner && (
                        <Button variant="primary" loading={busy} onClick={approve}>Approve and index</Button>
                    )}
                </div>

                {error && <p className="ud-error" role="alert">{error}</p>}
                {doc.status === 'awaiting_approval' && doc.owner && (
                    <p className="ud-banner" role="status">Not searchable yet. Check the extraction against the original, fix anything under Review, then approve.</p>
                )}
                {BUSY.has(doc.status) && <p className="ud-banner" role="status">Reading this document ({doc.stage ?? 'working'}{doc.progress.total ? `, ${doc.progress.done ?? 0} of ${doc.progress.total}` : ''}). You can leave this page; it continues in the background.</p>}
                {doc.status === 'failed' && (
                    <p className="ud-banner ud-banner--error" role="alert">This document could not be read: {doc.error}
                        {doc.owner && <> <Button size="sm" variant="outline" onClick={async () => { setDoc(await userDocsApi.reread(id)); }}>Try again</Button></>}</p>
                )}

                <div className="ud-tabs" role="tablist">
                    {(['content', ...(doc.owner ? ['review'] : []), 'details'] as Tab[]).map(t => (
                        <button key={t} role="tab" aria-selected={tab === t} className={`ud-tab${tab === t ? ' ud-tab--on' : ''}`} onClick={() => setTab(t)}>
                            {t === 'content' ? 'What was read' : t === 'review' ? `Review${doc.review_pending ? ` (${doc.review_pending})` : ''}` : 'Details'}
                        </button>
                    ))}
                </div>

                {tab === 'content' && (
                    content ? (
                        <div className={`ud-split${doc.has_pdf ? ' ud-split--pdf' : ''}`}>
                            {doc.has_pdf && (
                                <div className="ud-orig" aria-label="Original document">
                                    <PdfViewer key={doc.id} url={`${API_BASE}/user-documents/${doc.id}/pdf`} scale={1} overlays={NO_OVERLAYS} onPageCount={() => undefined} onVisiblePage={onVisible} onError={setPdfError} />
                                    {pdfError && <div className="docs-error" role="alert">{pdfError}</div>}
                                </div>
                            )}
                            <div className="ud-content" ref={columnRef}>
                                {content.pages.map(p => (
                                    <section key={p.page} id={`ud-page-${p.page}`} className={`ud-pagecard${doc.has_pdf && p.page === visible ? ' ud-pagecard--on' : ''}`} aria-label={`Page ${p.page}`}>
                                        <h3>Page {p.page}{p.label ? <span className="ud-sub"> · {p.label}</span> : null}</h3>
                                        {p.blocks.length ? p.blocks.map((b, i) => <Block key={i} b={b} />) : <pre className="ud-pre">{p.text}</pre>}
                                    </section>
                                ))}
                            </div>
                        </div>
                    ) : !BUSY.has(doc.status) && doc.status !== 'failed' ? <p className="ud-empty">Loading content…</p> : null
                )}

                {tab === 'review' && doc.owner && <ReviewPanel doc={doc} onChanged={setDoc} />}

                {tab === 'details' && (
                    <div className="ud-details">
                        {!edit ? (
                            <>
                                <dl className="ud-dl">
                                    <dt>Type</dt><dd>{doc.doc_type_label ?? '—'}</dd>
                                    <dt>Company</dt><dd>{doc.entity ?? '—'}</dd>
                                    <dt>Period</dt><dd>{doc.period ?? '—'}</dd>
                                    <dt>Title</dt><dd>{doc.label ?? '—'}</dd>
                                    <dt>Tags</dt><dd>{doc.tags.length ? doc.tags.join(', ') : '—'}</dd>
                                    <dt>Who can search it</dt><dd>{doc.visibility === 'private' ? 'Only the owner' : 'Everyone here'}</dd>
                                    <dt>How it was read</dt><dd>{String((doc.reading as { strategy?: string }).strategy ?? 'native')}{(doc.reading as { uses_models?: boolean }).uses_models ? ', with a vision model for doubtful pages' : ''}</dd>
                                    <dt>Index policy</dt><dd>{doc.index_policy === 'after_review' ? 'Doubtful text waits for review' : doc.index_policy === 'flagged' ? 'Doubtful text indexed, marked low confidence' : 'Doubtful text kept out'}</dd>
                                </dl>
                                {doc.owner && (
                                    <div className="ud-actions">
                                        {doc.has_pdf && <Button variant="outline" size="sm" onClick={() => open('pdf')}>Open PDF</Button>}
                                        <Button variant="outline" size="sm" onClick={() => open('file')}>Download original</Button>
                                        <Button variant="outline" size="sm" onClick={() => setEdit({ doc_type: doc.doc_type ?? '', entity: doc.entity ?? '', period: doc.period ?? '', tags: doc.tags.join(', '), label: doc.label ?? '' })}>Edit details</Button>
                                        {doc.has_pdf && <Button variant="outline" size="sm" onClick={async () => { setDoc(await userDocsApi.reread(id, 'accuracy')); }}>Read again with higher accuracy</Button>}
                                    </div>
                                )}
                            </>
                        ) : (
                            <div className="ud-opts">
                                <label>Type<select className="docs-select" value={edit.doc_type} onChange={e => setEdit({ ...edit, doc_type: e.target.value })}>
                                    {(types?.types ?? []).filter(t => t.id !== 'auto').map(t => <option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
                                <label>Company / ticker<input className="docs-input" value={edit.entity} maxLength={20} onChange={e => setEdit({ ...edit, entity: e.target.value })} /></label>
                                <label>Period<input className="docs-input" value={edit.period} maxLength={40} onChange={e => setEdit({ ...edit, period: e.target.value })} /></label>
                                <label>Title<input className="docs-input" value={edit.label} maxLength={200} onChange={e => setEdit({ ...edit, label: e.target.value })} /></label>
                                <label>Tags<input className="docs-input" value={edit.tags} onChange={e => setEdit({ ...edit, tags: e.target.value })} /></label>
                                <div className="ud-actions"><Button variant="primary" size="sm" loading={busy} onClick={saveDetails}>Save</Button><Button variant="ghost" size="sm" onClick={() => setEdit(null)}>Cancel</Button></div>
                            </div>
                        )}
                    </div>
                )}
            </main>
        </div>
    );
};

const Block: React.FC<{ b: DocContent['pages'][number]['blocks'][number] }> = ({ b }) => {
    if (b.type === 'heading') return <h4 className="ud-h">{b.text}</h4>;
    if (b.type === 'table') {
        return (
            <div className="ud-tablewrap">
                {(b.title || b.range) && <div className="ud-sub">{b.title}{b.range ? ` · ${b.range}` : ''}</div>}
                <table className="ud-grid">
                    <thead><tr>{(b.columns ?? []).map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
                    <tbody>{(b.rows ?? []).map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
                </table>
            </div>
        );
    }
    if (b.type === 'list') return <ul className="ud-ul">{(b.items ?? []).map((it, i) => <li key={i}>{typeof it === 'string' ? it : JSON.stringify(it)}</li>)}</ul>;
    if (b.type === 'key_value') {
        const items = (b as unknown as { items?: { label: string; value: string }[] }).items ?? [];
        return <dl className="ud-dl ud-dl--tight">{items.map((it, i) => <React.Fragment key={i}><dt>{it.label}</dt><dd>{it.value}</dd></React.Fragment>)}</dl>;
    }
    if (b.type === 'figure') return <p className="ud-sub">[figure]</p>;
    return b.text ? <p className="ud-p">{b.text}</p> : null;
};

export default UserDocPage;
