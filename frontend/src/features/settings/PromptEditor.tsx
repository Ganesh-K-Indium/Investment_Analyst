import React, { useEffect, useState } from 'react';
import { settingsApi, type EditablePrompt } from '../../services/settingsApi';

interface Props {
    overrides: Record<string, string>;
    onChange: (next: Record<string, string>) => void;
}

/** The prompts a user may change. Each shows its built-in text; saving an override replaces it, "Use default" removes the override. */
export const PromptEditor: React.FC<Props> = ({ overrides, onChange }) => {
    const [prompts, setPrompts] = useState<EditablePrompt[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [open, setOpen] = useState<string | null>(null);

    useEffect(() => {
        settingsApi.prompts().then(r => setPrompts(r.prompts)).catch(e => setError(e instanceof Error ? e.message : 'Could not load prompts'));
    }, []);

    if (error) return <p className="set-error" role="alert">{error}</p>;
    if (!prompts) return <p className="set-empty">Loading prompts…</p>;

    const set = (name: string, text: string | null) => {
        const next = { ...overrides };
        if (text === null || !text.trim()) delete next[name]; else next[name] = text;
        onChange(next);
    };

    return (
        <div className="set-fw">
            {prompts.map(p => {
                const custom = overrides[p.name] !== undefined;
                const isOpen = open === p.name;
                return (
                    <div key={p.name} className="set-card">
                        <button type="button" className="set-card__head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : p.name)}>
                            <span className="set-card__title">{p.label}</span>
                            <span className="set-card__meta">{custom ? 'customized' : 'default'}</span>
                        </button>
                        {isOpen && (
                            <div className="set-card__body">
                                <p className="set-field__help">{p.help}</p>
                                <textarea
                                    className="set-input set-input--area set-input--mono"
                                    rows={14}
                                    value={custom ? overrides[p.name] : p.default}
                                    onChange={e => set(p.name, e.target.value === p.default ? null : e.target.value)}
                                    aria-label={p.label}
                                />
                                <div className="set-row">
                                    <span className="set-field__help">{(custom ? overrides[p.name] : p.default).length.toLocaleString()} characters</span>
                                    {custom && <button type="button" className="set-link" onClick={() => set(p.name, null)}>Use default</button>}
                                </div>
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};
