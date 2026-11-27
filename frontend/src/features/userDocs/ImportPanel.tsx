import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '../../components/ui/Button';
import { useAuth } from '../../context/useAuth';
import { integrationApi, type IntegrationResponse, type RemoteFile } from '../../services/integrationApi';
import { userDocsApi, formatBytes, type UploadResult } from './userDocsApi';

interface Props {
    onImported: () => void;
}

const MAX_FILES = 20;

/** Pick files from a connected source (SharePoint, S3, Azure Blob, Drive, SFTP). They go through the same reading, review and approval as an upload. */
export const ImportPanel: React.FC<Props> = ({ onImported }) => {
    const { userId } = useAuth();
    const navigate = useNavigate();
    const [sources, setSources] = useState<IntegrationResponse[] | null>(null);
    const [sourceId, setSourceId] = useState<number | null>(null);
    const [path, setPath] = useState<string | null>(null);
    const [files, setFiles] = useState<RemoteFile[]>([]);
    const [picked, setPicked] = useState<string[]>([]);
    const [entity, setEntity] = useState('');
    const [browsing, setBrowsing] = useState(false);
    const [busy, setBusy] = useState(false);
    const [results, setResults] = useState<UploadResult[]>([]);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!userId) return;
        integrationApi.getUserIntegrations(userId)
            .then(list => {
                const usable = list.filter(i => i.vendor !== 'postgresql' && i.status !== 'disconnected');
                setSources(usable);
                if (usable.length) setSourceId(usable[0].id);
            })
            .catch(() => { setSources([]); setError('Could not load your sources. Check the connection and try again.'); });
    }, [userId]);

    useEffect(() => {
        if (sourceId == null) return;
        let live = true;
        setBrowsing(true); setError(null);
        integrationApi.browseFiles(sourceId, path)
            .then(r => { if (live) setFiles(r.files); })
            .catch(() => { if (live) { setFiles([]); setError('Could not open this folder. Check the source in Data Sources.'); } })
            .finally(() => { if (live) setBrowsing(false); });
        return () => { live = false; };
    }, [sourceId, path]);

    const crumbs = (path ?? '').split('/').filter(Boolean);
    const toggle = (p: string) => setPicked(prev => prev.includes(p) ? prev.filter(x => x !== p) : prev.length >= MAX_FILES ? prev : [...prev, p]);

    const submit = async () => {
        if (sourceId == null || !picked.length) return;
        setBusy(true); setError(null);
        try {
            const r = await userDocsApi.importFromSource(sourceId, picked, entity.trim() || undefined);
            setResults(r.results);
            const done = new Set(r.results.filter(x => x.ok).map(x => x.filename));
            setPicked(prev => prev.filter(p => !done.has(p.split('/').pop() ?? p)));
            if (r.results.some(x => x.ok)) onImported();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Import failed. Try again.');
        } finally {
            setBusy(false);
        }
    };

    if (sources && !sources.length) {
        return (
            <section className="ud-upload" aria-label="Import from a source">
                <p className="ud-drop__title">No sources connected</p>
                <p className="ud-drop__hint">Connect SharePoint, S3, Azure Blob, Google Drive or SFTP in Data Sources, then import files from here.</p>
                <div><Button variant="outline" size="sm" onClick={() => navigate('/data-sources')}>Open Data Sources</Button></div>
            </section>
        );
    }

    return (
        <section className="ud-upload" aria-label="Import from a source">
            <div className="ud-opts">
                <label>Source
                    <select className="docs-select" value={sourceId ?? ''} onChange={e => { setSourceId(Number(e.target.value)); setPath(null); setPicked([]); setResults([]); }}>
                        {(sources ?? []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                </label>
                <label>Company / ticker
                    <input className="docs-input" value={entity} placeholder="Detected if left empty" maxLength={20} onChange={e => setEntity(e.target.value)} />
                </label>
            </div>

            <nav className="ud-crumbs" aria-label="Folder">
                <button type="button" onClick={() => setPath(null)}>Top</button>
                {crumbs.map((c, i) => (
                    <React.Fragment key={i}><span aria-hidden="true">/</span><button type="button" onClick={() => setPath('/' + crumbs.slice(0, i + 1).join('/'))}>{c}</button></React.Fragment>
                ))}
            </nav>

            <ul className="ud-files ud-browse" aria-busy={browsing}>
                {browsing && <li className="ud-files__size">Loading…</li>}
                {!browsing && !files.length && <li className="ud-files__size">This folder is empty.</li>}
                {!browsing && files.map(f => f.is_directory ? (
                    <li key={f.path}><button type="button" className="ud-folder" onClick={() => setPath(f.path)}>{f.name}</button></li>
                ) : (
                    <li key={f.path}>
                        <label className="ud-pick">
                            <input type="checkbox" checked={picked.includes(f.path)} onChange={() => toggle(f.path)} />
                            <span className="ud-files__name">{f.name}</span>
                        </label>
                        <span className="ud-files__size">{f.size != null ? formatBytes(f.size) : ''}</span>
                    </li>
                ))}
            </ul>

            {error && <p className="ud-error" role="alert">{error}</p>}
            {results.length > 0 && (
                <ul className="ud-results">
                    {results.map(r => <li key={r.filename} className={r.ok ? 'ud-ok' : 'ud-bad'}>{r.filename}: {r.ok ? 'added and being read' : r.duplicate_of ? 'already in My documents' : r.error}</li>)}
                </ul>
            )}
            <div className="ud-actions">
                <Button variant="primary" onClick={submit} disabled={busy || !picked.length}>
                    {busy ? 'Importing…' : picked.length ? `Import ${picked.length} file${picked.length === 1 ? '' : 's'}` : 'Choose files to import'}
                </Button>
                <span className="ud-files__size">Imported files are read, then held for your approval, like uploads.{picked.length >= MAX_FILES ? ` Up to ${MAX_FILES} at a time.` : ''}</span>
            </div>
        </section>
    );
};
