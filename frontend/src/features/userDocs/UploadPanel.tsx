import React, { useCallback, useRef, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { useSetting } from '../../store/settingsStore';
import { userDocsApi, formatBytes, type DocTypes, type Reading, type UploadResult, type Visibility } from './userDocsApi';

interface Props {
    types: DocTypes | null;
    onUploaded: () => void;
}

const READING_HELP: Record<Reading, string> = {
    economy: 'Local OCR only. Private and free; best for clean printed scans.',
    balanced: 'Local OCR first; a vision model only for pages it reads poorly.',
    accuracy: 'Two vision models read every scanned page. Slowest and costliest.',
};

/** Drop files, say what they are (or let the system guess), upload. Several at once; each file reports its own result. */
export const UploadPanel: React.FC<Props> = ({ types, onUploaded }) => {
    const defaultReading = useSetting<Reading>('documents', 'reading_strategy', 'balanced');
    const defaultVisibility = useSetting<Visibility>('documents', 'default_visibility', 'private');
    const maxMb = useSetting<number>('documents', 'max_upload_mb', 50);
    const input = useRef<HTMLInputElement>(null);
    const [files, setFiles] = useState<File[]>([]);
    const [drag, setDrag] = useState(false);
    const [docType, setDocType] = useState('auto');
    const [entity, setEntity] = useState('');
    const [period, setPeriod] = useState('');
    const [tags, setTags] = useState('');
    const [visibility, setVisibility] = useState<Visibility | ''>('');
    const [reading, setReading] = useState<Reading | ''>('');
    const [busy, setBusy] = useState(false);
    const [results, setResults] = useState<UploadResult[]>([]);
    const [error, setError] = useState<string | null>(null);

    const add = useCallback((list: FileList | File[]) => {
        const allowed = new Set(types?.formats ?? []);
        const next: File[] = [];
        const rejected: string[] = [];
        for (const f of Array.from(list)) {
            const ext = f.name.split('.').pop()?.toLowerCase() ?? '';
            if (types && !allowed.has(ext)) rejected.push(`${f.name}: .${ext} files are not supported`);
            else if (f.size > maxMb * 1024 * 1024) rejected.push(`${f.name}: larger than ${maxMb} MB`);
            else if (f.size === 0) rejected.push(`${f.name}: the file is empty`);
            else next.push(f);
        }
        setFiles(prev => [...prev, ...next.filter(n => !prev.some(p => p.name === n.name && p.size === n.size))]);
        setError(rejected.length ? rejected.join('. ') : null);
        setResults([]);
    }, [types, maxMb]);

    const submit = async () => {
        if (!files.length) return;
        setBusy(true); setError(null);
        try {
            const r = await userDocsApi.upload(files, { doc_type: docType, entity, period, tags, visibility: visibility || undefined, reading: reading || undefined });
            setResults(r.results);
            setFiles(prev => prev.filter(f => !r.results.some(x => x.ok && x.filename === f.name)));
            if (r.results.some(x => x.ok)) onUploaded();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Upload failed');
        } finally {
            setBusy(false);
        }
    };

    return (
        <section className="ud-upload" aria-label="Upload documents">
            <div
                className={`ud-drop${drag ? ' ud-drop--over' : ''}`}
                onDragOver={e => { e.preventDefault(); setDrag(true); }}
                onDragLeave={() => setDrag(false)}
                onDrop={e => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
            >
                <p className="ud-drop__title">Drop files here</p>
                <p className="ud-drop__hint">PDF, scans and photos, Excel/CSV, Word, PowerPoint, HTML, text. Up to {maxMb} MB each.</p>
                <Button variant="outline" size="sm" onClick={() => input.current?.click()}>Choose files</Button>
                <input ref={input} type="file" multiple hidden accept={types ? types.formats.map(f => `.${f}`).join(',') : undefined}
                    onChange={e => { if (e.target.files) add(e.target.files); e.target.value = ''; }} />
            </div>

            {files.length > 0 && (
                <>
                    <ul className="ud-files">
                        {files.map(f => (
                            <li key={`${f.name}-${f.size}`}>
                                <span className="ud-files__name">{f.name}</span><span className="ud-files__size">{formatBytes(f.size)}</span>
                                <button type="button" className="ud-x" aria-label={`Remove ${f.name}`} onClick={() => setFiles(prev => prev.filter(x => x !== f))}>×</button>
                            </li>
                        ))}
                    </ul>
                    <div className="ud-opts">
                        <label>Type
                            <select className="docs-select" value={docType} onChange={e => setDocType(e.target.value)}>
                                {(types?.types ?? [{ id: 'auto', label: 'Detect automatically' }]).map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
                            </select>
                        </label>
                        <label>Company / ticker
                            <input className="docs-input" value={entity} placeholder="Detected if left empty" maxLength={20} onChange={e => setEntity(e.target.value)} />
                        </label>
                        <label>Period
                            <input className="docs-input" value={period} placeholder="e.g. FY2025, Q3 2025" maxLength={40} onChange={e => setPeriod(e.target.value)} />
                        </label>
                        <label>Tags
                            <input className="docs-input" value={tags} placeholder="comma separated" onChange={e => setTags(e.target.value)} />
                        </label>
                        <label>Who can search it
                            <select className="docs-select" value={visibility} onChange={e => setVisibility(e.target.value as Visibility | '')}>
                                <option value="">Default ({defaultVisibility === 'private' ? 'only me' : 'everyone here'})</option>
                                <option value="private">Only me</option>
                                <option value="workspace">Everyone here</option>
                            </select>
                        </label>
                        <label>Reading of scans
                            <select className="docs-select" value={reading} onChange={e => setReading(e.target.value as Reading | '')} title={READING_HELP[(reading || defaultReading) as Reading]}>
                                <option value="">Default ({defaultReading})</option>
                                <option value="economy">Economy</option>
                                <option value="balanced">Balanced</option>
                                <option value="accuracy">Accuracy</option>
                            </select>
                        </label>
                    </div>
                    <p className="ud-help">{READING_HELP[(reading || defaultReading) as Reading]} Spreadsheets, Word and PowerPoint files are read directly, with no OCR.</p>
                    <div className="ud-actions">
                        <Button variant="primary" loading={busy} onClick={submit}>Upload {files.length} file{files.length === 1 ? '' : 's'}</Button>
                        <Button variant="ghost" disabled={busy} onClick={() => setFiles([])}>Clear</Button>
                    </div>
                </>
            )}

            {error && <p className="ud-error" role="alert">{error}</p>}
            {results.some(r => !r.ok) && (
                <ul className="ud-results" role="status">
                    {results.filter(r => !r.ok).map(r => <li key={r.filename}><strong>{r.filename}</strong>: {r.error}</li>)}
                </ul>
            )}
        </section>
    );
};
