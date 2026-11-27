import React, { useEffect } from 'react';
import { joinSpec, splitSpec } from '../../services/providersApi';
import { useProvidersStore } from '../../store/providersStore';

interface Props {
    id: string;
    value: string;
    onChange: (spec: string) => void;
    /** Administrator-restricted list of allowed specs; when present the picker offers only these. */
    restrictTo?: string[];
}

/** Provider and model for one job. Providers that are not connected are marked, so a job is never silently pointed at one you cannot use. */
export const ModelPicker: React.FC<Props> = ({ id, value, onChange, restrictTo }) => {
    const data = useProvidersStore(s => s.data);
    const load = useProvidersStore(s => s.load);
    useEffect(() => { void load(); }, [load]);

    if (restrictTo?.length) {
        return (
            <select id={id} className="set-input" value={value} onChange={e => onChange(e.target.value)}>
                {!restrictTo.includes(value) && <option value={value}>{value}</option>}
                {restrictTo.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
        );
    }
    const providers = data?.providers ?? [];
    const known = providers.map(p => p.id);
    const { provider, model } = splitSpec(value, known.length ? known : ['openai', 'anthropic', 'gemini', 'ollama', 'custom']);
    const current = providers.find(p => p.id === provider);
    const suggestions = current?.models ?? [];
    const free = !current || current.models.length === 0 || !current.models.includes(model);

    return (
        <div className="mp">
            <div className="mp__row">
                <select aria-label="Provider" className="set-input mp__provider" value={provider} onChange={e => {
                    const next = providers.find(p => p.id === e.target.value);
                    onChange(joinSpec(e.target.value, next?.models[0] ?? model));
                }}>
                    {providers.map(p => <option key={p.id} value={p.id}>{p.label}{p.configured ? '' : ' (not connected)'}</option>)}
                    {providers.length === 0 && <option value={provider}>{provider}</option>}
                </select>
                {suggestions.length > 0 && (
                    <select id={id} aria-label="Model" className="set-input" value={free ? '__other__' : model} onChange={e => onChange(joinSpec(provider, e.target.value === '__other__' ? '' : e.target.value))}>
                        {suggestions.map(m => <option key={m} value={m}>{m}</option>)}
                        <option value="__other__">Other…</option>
                    </select>
                )}
            </div>
            {(suggestions.length === 0 || free) && (
                <input id={suggestions.length === 0 ? id : undefined} aria-label="Model name" className="set-input" placeholder={provider === 'ollama' ? 'e.g. llama3.1:8b' : 'model name'} value={model}
                    onChange={e => onChange(joinSpec(provider, e.target.value.trim()))} />
            )}
            {current && !current.configured && <p className="mp__warn" role="status">{current.label} is not connected. Add its key under AI providers or pick another provider.</p>}
        </div>
    );
};
