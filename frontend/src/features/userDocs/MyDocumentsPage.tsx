import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { PageHeader } from '../../components/layout/PageHeader';
import { Button } from '../../components/ui/Button';
import { StatTile, StatGrid } from '../../components/ui/StatTile';
import { useSettingsStore } from '../../store/settingsStore';
import { UploadPanel } from './UploadPanel';
import { ImportPanel } from './ImportPanel';
import { STATUS_LABEL, userDocsApi, formatBytes, type DocTypes, type UserDoc } from './userDocsApi';
import '../documents/DocumentsPage.css';
import { LibraryTabs } from '../library/LibraryTabs';
import './MyDocuments.css';

const BUSY = new Set(['queued', 'processing']);

/** /documents/mine: everything you brought, in one library. Upload, see what is being read, review what needs a person, open, share, delete. */
export const MyDocumentsPage: React.FC = () => {
    const navigate = useNavigate();
    const loadSettings = useSettingsStore(s => s.load);
    const [docs, setDocs] = useState<UserDoc[]>([]);
    const [types, setTypes] = useState<DocTypes | null>(null);
    const [needsReview, setNeedsReview] = useState(0);
    const [status, setStatus] = useState('');
    const [docType, setDocType] = useState('');
    const [q, setQ] = useState('');
    const [panel, setPanel] = useState<'upload' | 'import' | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<string | null>(null);
    const timer = useRef<number | undefined>(undefined);

    const flash = (m: string) => { setToast(m); window.setTimeout(() => setToast(null), 3000); };

    const refresh = useCallback(async () => {
        try {
            const r = await userDocsApi.list({ status, doc_type: docType, q });
            setDocs(r.documents); setNeedsReview(r.needs_review); setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not load your documents');
        } finally {
            setLoading(false);
        }
    }, [status, docType, q]);

    useEffect(() => { void loadSettings(); userDocsApi.types().then(setTypes).catch(() => setTypes(null)); }, [loadSettings]);
    useEffect(() => { const t = window.setTimeout(() => void refresh(), q ? 250 : 0); return () => window.clearTimeout(t); }, [refresh, q]);

    // Poll while anything is being read, so progress and the final state appear without a reload.
    const anyBusy = docs.some(d => BUSY.has(d.status));
    useEffect(() => {
        if (!anyBusy) return;
        timer.current = window.setInterval(() => void refresh(), 2500);
        return () => window.clearInterval(timer.current);
    }, [anyBusy, refresh]);

    const stats = useMemo(() => ({
        total: docs.length, ready: docs.filter(d => d.status === 'ready').length, busy: docs.filter(d => BUSY.has(d.status)).length,
        review: docs.filter(d => d.status === 'needs_review' || d.status === 'awaiting_approval').length,
    }), [docs]);

    const remove = async (d: UserDoc) => {
        if (!window.confirm(`Delete "${d.filename}"? Its file and everything indexed from it are removed, and it will no longer inform your answers.`)) return;
        try { await userDocsApi.remove(d.id); flash('Document deleted'); void refresh(); } catch (e) { setError(e instanceof Error ? e.message : 'Could not delete'); }
    };
    const share = async (d: UserDoc) => {
        const next = d.visibility === 'private' ? 'workspace' : 'private';
        if (next === 'workspace' && !window.confirm('Let everyone here search this document in their analyses?')) return;
        try { await userDocsApi.patch(d.id, { visibility: next }); void refresh(); } catch (e) { setError(e instanceof Error ? e.message : 'Could not change sharing'); }
    };
    const reread = async (d: UserDoc) => {
        try { await userDocsApi.reread(d.id); flash('Reading again'); void refresh(); } catch (e) { setError(e instanceof Error ? e.message : 'Could not start'); }
    };

    return (
        <PageLayout className="docs-page ud-page">
            <PageHeader
                title="Documents"
                description="Bring your own statements, broker notes, models, decks and scans. They are read, checked and searched together with the filings. Only you see a document unless you share it."
                actions={
                    <>
                        <Button variant="outline" onClick={() => setPanel(v => v === 'import' ? null : 'import')} aria-expanded={panel === 'import'}>{panel === 'import' ? 'Close import' : 'Import from a source'}</Button>
                        <Button variant="primary" onClick={() => setPanel(v => v === 'upload' ? null : 'upload')} aria-expanded={panel === 'upload'}>{panel === 'upload' ? 'Close upload' : 'Upload documents'}</Button>
                    </>
                }
            />
            <LibraryTabs />

                {panel === 'upload' && <UploadPanel types={types} onUploaded={() => { void refresh(); flash('Upload started'); }} />}
                {panel === 'import' && <ImportPanel onImported={() => { void refresh(); flash('Import started'); }} />}

                <StatGrid label="Document summary">
                    <StatTile label="Documents" value={stats.total} />
                    <StatTile label="Searchable" value={stats.ready} />
                    <StatTile label="Being read" value={stats.busy} tone={stats.busy ? 'running' : 'default'} />
                    <StatTile label="Need your review" value={needsReview} tone={needsReview ? 'failed' : 'default'}
                        onClick={() => setStatus(status === 'needs_review' ? '' : 'needs_review')} pressed={status === 'needs_review'} />
                </StatGrid>

                <div className="docs-filters">
                    <input className="docs-input" type="search" placeholder="Search name, title or company" value={q} onChange={e => setQ(e.target.value)} aria-label="Search documents" />
                    <select className="docs-select" value={status} onChange={e => setStatus(e.target.value)} aria-label="Status">
                        <option value="">Any status</option>
                        {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                    <select className="docs-select" value={docType} onChange={e => setDocType(e.target.value)} aria-label="Document type">
                        <option value="">Any type</option>
                        {(types?.types ?? []).filter(t => t.id !== 'auto').map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
                    </select>
                    {(status || docType || q) && <Button variant="ghost" size="sm" onClick={() => { setStatus(''); setDocType(''); setQ(''); }}>Clear filters</Button>}
                </div>

                {error && <p className="ud-error" role="alert">{error}</p>}

                {loading ? <p className="ud-empty">Loading…</p> : docs.length === 0 ? (
                    <div className="ud-empty-card">
                        <h2>{status || docType || q ? 'No documents match these filters' : 'No documents yet'}</h2>
                        <p>{status || docType || q ? 'Clear the filters to see everything.' : 'Upload a file and ask questions about it in the chat, next to the SEC filings.'}</p>
                        {!(status || docType || q) && <Button variant="primary" onClick={() => setPanel('upload')}>Upload your first document</Button>}
                    </div>
                ) : (
                    <div className="ud-table-wrap">
                        <table className="ud-table">
                            <thead><tr><th>Document</th><th>Type</th><th>Company</th><th>Period</th><th>Pages</th><th>Status</th><th><span className="ud-sr">Actions</span></th></tr></thead>
                            <tbody>
                                {docs.map(d => (
                                    <tr key={d.id} className="ud-row" onClick={() => navigate(`/documents/mine/${d.id}`)}>
                                        <td>
                                            <button type="button" className="ud-name" onClick={e => { e.stopPropagation(); navigate(`/documents/mine/${d.id}`); }}>{d.filename}</button>
                                            <div className="ud-sub">{d.label && d.label !== d.filename ? `${d.label} · ` : ''}{formatBytes(d.size_bytes)}{!d.owner ? ' · shared with you' : d.visibility === 'workspace' ? ' · shared' : ''}</div>
                                        </td>
                                        <td>{d.doc_type_label ?? '—'}</td>
                                        <td>{d.entity ?? '—'}</td>
                                        <td>{d.period ?? '—'}</td>
                                        <td>{d.pages || '—'}{d.status === 'needs_review' && d.pages ? <span className="ud-sub"> · {d.pages_indexed} searchable</span> : null}</td>
                                        <td>
                                            <span className={`ud-badge ud-badge--${d.status}`}>{STATUS_LABEL[d.status]}</span>
                                            {BUSY.has(d.status) && (
                                                <div className="ud-progress" role="progressbar" aria-label="Reading progress" aria-valuenow={d.progress.done ?? 0} aria-valuemax={d.progress.total || undefined}>
                                                    <div className="ud-progress__bar" style={{ width: d.progress.total ? `${Math.round(((d.progress.done ?? 0) / d.progress.total) * 100)}%` : '12%' }} />
                                                </div>
                                            )}
                                            {BUSY.has(d.status) && <div className="ud-sub">{d.stage ?? 'working'}{d.progress.total ? ` · ${d.progress.done ?? 0}/${d.progress.total}` : ''}</div>}
                                            {d.status === 'awaiting_approval' && <div className="ud-sub">Not searchable until you approve</div>}
                                            {d.status === 'needs_review' && <div className="ud-sub">{d.review_pending} to check</div>}
                                            {d.status === 'failed' && <div className="ud-sub ud-sub--error">{d.error}</div>}
                                        </td>
                                        <td className="ud-actions-cell" onClick={e => e.stopPropagation()}>
                                            {d.status === 'awaiting_approval' && d.owner && <Button size="sm" variant="primary" onClick={() => navigate(`/documents/mine/${d.id}`)}>Review and approve</Button>}
                                            {d.status === 'needs_review' && d.owner && <Button size="sm" variant="primary" onClick={() => navigate(`/documents/mine/${d.id}?tab=review`)}>Review</Button>}
                                            {d.owner && d.status === 'failed' && <Button size="sm" variant="outline" onClick={() => reread(d)}>Try again</Button>}
                                            {d.owner && d.status !== 'processing' && d.status !== 'queued' && <Button size="sm" variant="ghost" onClick={() => share(d)}>{d.visibility === 'private' ? 'Share' : 'Make private'}</Button>}
                                            {d.owner && <Button size="sm" variant="ghost" onClick={() => remove(d)}>Delete</Button>}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            {toast && <div className="ud-toast" role="status">{toast}</div>}
        </PageLayout>
    );
};

export default MyDocumentsPage;
