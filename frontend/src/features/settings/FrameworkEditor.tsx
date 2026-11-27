import React, { useState } from 'react';
import { Button } from '../../components/ui/Button';
import { settingsApi, type Framework } from '../../services/settingsApi';

const SOURCES = ['filings', 'uploads', 'notes', 'web', 'macro'];

interface Props {
    frameworks: Framework[];
    onChange: (next: Framework[]) => void;
}

const lines = (s: string) => s.split('\n').map(x => x.trim()).filter(Boolean);

/** Build your own analysis framework: a name, trigger phrases, the sections to cover, which sources to search, and the writing instructions. */
export const FrameworkEditor: React.FC<Props> = ({ frameworks, onChange }) => {
    const [openId, setOpenId] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const add = async () => {
        setBusy(true);
        try {
            const t = await settingsApi.frameworkTemplate();
            let id = t.id, n = 2;
            while (frameworks.some(f => f.id === id)) id = `${t.id}-${n++}`;
            onChange([...frameworks, { ...t, id }]);
            setOpenId(id);
        } finally {
            setBusy(false);
        }
    };
    const patch = (id: string, p: Partial<Framework>) => onChange(frameworks.map(f => (f.id === id ? { ...f, ...p } : f)));

    return (
        <div className="set-fw">
            {frameworks.length === 0 && <p className="set-empty">No custom frameworks yet. A framework is a repeatable way of analysing a company, for example a credit review or a DCF checklist.</p>}
            {frameworks.map(f => {
                const open = openId === f.id;
                return (
                    <div key={f.id} className="set-card">
                        <button type="button" className="set-card__head" aria-expanded={open} onClick={() => setOpenId(open ? null : f.id)}>
                            <span className="set-card__title">{f.name || f.id}</span>
                            <span className="set-card__meta">{f.sections.length} sections{f.enabled ? '' : ' · off'}</span>
                        </button>
                        {open && (
                            <div className="set-card__body">
                                <div className="set-grid">
                                    <label className="set-stack">Name
                                        <input className="set-input" value={f.name} maxLength={80} onChange={e => patch(f.id, { name: e.target.value })} />
                                    </label>
                                    <label className="set-stack">Runs when the question contains (one phrase per line)
                                        <textarea className="set-input set-input--area" rows={3} value={f.triggers.join('\n')} onChange={e => patch(f.id, { triggers: lines(e.target.value) })} />
                                    </label>
                                </div>
                                <label className="set-stack">Sections of the report (one per line, in order)
                                    <textarea className="set-input set-input--area" rows={5} value={f.sections.join('\n')} onChange={e => patch(f.id, { sections: lines(e.target.value) })} />
                                </label>
                                <div className="set-stack">
                                    <span>Search these sources</span>
                                    <div className="set-chips">
                                        {SOURCES.map(s => {
                                            const on = f.sources.includes(s);
                                            return (
                                                <button key={s} type="button" aria-pressed={on} className={`set-chip${on ? ' set-chip--on' : ''}`}
                                                    onClick={() => patch(f.id, { sources: on ? f.sources.filter(x => x !== s) : [...f.sources, s] })}>{s}</button>
                                            );
                                        })}
                                    </div>
                                </div>
                                <label className="set-stack">Instructions for the writer
                                    <textarea className="set-input set-input--area" rows={5} value={f.prompt} maxLength={20000} onChange={e => patch(f.id, { prompt: e.target.value })} />
                                </label>
                                <div className="set-row">
                                    <label className="set-switch">
                                        <input type="checkbox" checked={f.enabled} onChange={e => patch(f.id, { enabled: e.target.checked })} />
                                        <span className="set-switch__track" aria-hidden />
                                        <span className="set-switch__text">{f.enabled ? 'On' : 'Off'}</span>
                                    </label>
                                    <Button variant="ghost" size="sm" onClick={() => { if (window.confirm(`Delete the framework "${f.name}"?`)) { onChange(frameworks.filter(x => x.id !== f.id)); setOpenId(null); } }}>
                                        Delete framework
                                    </Button>
                                </div>
                            </div>
                        )}
                    </div>
                );
            })}
            <Button variant="outline" size="sm" loading={busy} onClick={add}>Add framework</Button>
        </div>
    );
};
