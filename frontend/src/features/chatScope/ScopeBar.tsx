import React, { useEffect, useState } from 'react';
import { useSettingsStore } from '../../store/settingsStore';
import { userDocsApi, type UserDoc } from '../userDocs/userDocsApi';
import type { Framework } from '../../services/settingsApi';
import { DEFAULT_SCOPE, SOURCE_LABEL, type ChatScope } from './scope';
import './ScopeBar.css';

interface Props {
    scope: ChatScope;
    onChange: (s: ChatScope) => void;
    disabled?: boolean;
}

/** What this question may use: sources, one of your frameworks, and which of your documents. Collapsed to one line until opened. */
export const ScopeBar: React.FC<Props> = ({ scope, onChange, disabled }) => {
    const eff = useSettingsStore(s => s.effective);
    const loadSettings = useSettingsStore(s => s.load);
    const loaded = useSettingsStore(s => s.loaded);
    const [open, setOpen] = useState(false);
    const [docs, setDocs] = useState<UserDoc[]>([]);

    useEffect(() => { if (!loaded) void loadSettings(); }, [loaded, loadSettings]);
    useEffect(() => { if (open && docs.length === 0) userDocsApi.list({ status: 'ready' }).then(r => setDocs(r.documents)).catch(() => setDocs([])); }, [open, docs.length]);

    const saved = (eff.retrieval?.sources as string[] | undefined) ?? ['filings', 'uploads', 'notes', 'web', 'macro'];
    const sources = scope.sources ?? saved;
    const frameworks = ((eff.modules?.custom as Framework[] | undefined) ?? []).filter(f => f.enabled);
    const changed = scope.sources !== null || scope.framework !== '' || scope.documentIds.length > 0;
    const summary = [
        sources.map(s => SOURCE_LABEL[s] ?? s).join(', ') || 'No sources',
        scope.framework ? `Framework: ${frameworks.find(f => f.id === scope.framework)?.name ?? scope.framework}` : null,
        scope.documentIds.length ? `${scope.documentIds.length} document${scope.documentIds.length === 1 ? '' : 's'}` : null,
    ].filter(Boolean).join(' · ');

    return (
        <div className="scope">
            <button type="button" className="scope__toggle" aria-expanded={open} onClick={() => setOpen(o => !o)} disabled={disabled}>
                <span className="scope__label">Using</span><span className="scope__summary">{summary}</span>
                <span className="scope__chev" aria-hidden>{open ? '▴' : '▾'}</span>
            </button>
            {open && (
                <div className="scope__panel">
                    <div className="scope__group" role="group" aria-label="Sources">
                        <span className="scope__title">Sources</span>
                        <div className="scope__chips">
                            {Object.keys(SOURCE_LABEL).map(s => {
                                const on = sources.includes(s);
                                return (
                                    <button key={s} type="button" aria-pressed={on} className={`scope__chip${on ? ' scope__chip--on' : ''}`}
                                        onClick={() => onChange({ ...scope, sources: on ? sources.filter(x => x !== s) : [...sources, s] })}>{SOURCE_LABEL[s]}</button>
                                );
                            })}
                        </div>
                    </div>
                    <label className="scope__group">
                        <span className="scope__title">Framework</span>
                        <select className="scope__select" value={scope.framework} onChange={e => onChange({ ...scope, framework: e.target.value })}>
                            <option value="">Standard analysis</option>
                            {frameworks.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
                        </select>
                    </label>
                    <div className="scope__group">
                        <span className="scope__title">My documents</span>
                        {docs.length === 0 ? <span className="scope__hint">None searchable yet. Upload files under Documents, My documents.</span> : (
                            <div className="scope__chips">
                                <button type="button" aria-pressed={scope.documentIds.length === 0} className={`scope__chip${scope.documentIds.length === 0 ? ' scope__chip--on' : ''}`} onClick={() => onChange({ ...scope, documentIds: [] })}>All</button>
                                {docs.map(d => {
                                    const on = scope.documentIds.includes(d.id);
                                    return (
                                        <button key={d.id} type="button" aria-pressed={on} title={d.filename} className={`scope__chip${on ? ' scope__chip--on' : ''}`}
                                            onClick={() => onChange({ ...scope, documentIds: on ? scope.documentIds.filter(x => x !== d.id) : [...scope.documentIds, d.id] })}>
                                            {d.filename.length > 28 ? `${d.filename.slice(0, 26)}…` : d.filename}
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                    {changed && <button type="button" className="scope__reset" onClick={() => onChange(DEFAULT_SCOPE)}>Back to my settings</button>}
                </div>
            )}
        </div>
    );
};
