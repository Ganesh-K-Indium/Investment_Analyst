import React, { useCallback, useEffect, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { useAuth } from '../../context/useAuth';
import { providersApi, type Provider, type TestResult } from '../../services/providersApi';
import { useProvidersStore } from '../../store/providersStore';

interface Props {
    defaultProvider: string;
    onDefaultProvider: (id: string) => void;
    /** Point every job at this provider (fills the form; nothing is saved until Save changes). `models` is needed for providers with no built-in choice. */
    onUseForAll: (provider: Provider, models?: { strong: string; light: string }) => void;
}

const SOURCE_TEXT: Record<string, string> = { yours: 'your key', workspace: 'shared by your workspace', server: 'the server’s key' };

const ProviderCard: React.FC<{ p: Provider; isDefault: boolean; admin: boolean; locked: boolean; localOnly: boolean; onUseForAll: Props['onUseForAll'] }> = ({ p, isDefault, admin, locked, localOnly, onUseForAll }) => {
    const replace = useProvidersStore(s => s.replace);
    const [draft, setDraft] = useState<Record<string, string>>({});
    const [editing, setEditing] = useState<Record<string, boolean>>({});
    const [shared, setShared] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [model, setModel] = useState('');
    const [found, setFound] = useState<string[]>([]);
    const [result, setResult] = useState<TestResult | null>(null);
    const [pick, setPick] = useState<{ strong: string; light: string } | null>(null);

    const blocked = p.cloud && localOnly;
    const dirty = Object.values(draft).some(v => v.trim());

    const save = async () => {
        setBusy('save'); setError(null); setResult(null);
        try {
            const values: Record<string, string | null> = {};
            Object.entries(draft).forEach(([k, v]) => { if (v.trim()) values[k] = v.trim(); });
            replace(await providersApi.save(p.id, values, shared ? 'workspace' : 'user'));
            setDraft({}); setEditing({});
        } catch (e) { setError(e instanceof Error ? e.message : 'Could not save'); } finally { setBusy(null); }
    };
    const removeField = async (name: string) => {
        if (!window.confirm(`Remove this saved value for ${p.label}?`)) return;
        setBusy(name); setError(null);
        try { replace(await providersApi.save(p.id, { [name]: null }, shared ? 'workspace' : 'user')); } catch (e) { setError(e instanceof Error ? e.message : 'Could not remove'); } finally { setBusy(null); }
    };
    const test = async () => {
        setBusy('test'); setError(null); setResult(null);
        try { setResult(await providersApi.test(p.id, model, Object.fromEntries(Object.entries(draft).filter(([, v]) => v.trim())))); }
        catch (e) { setResult({ ok: false, message: e instanceof Error ? e.message : 'The test could not run' }); } finally { setBusy(null); }
    };
    const findModels = useCallback(async () => {
        setBusy('models');
        try { const r = await providersApi.models(p.id); setFound(r.models); if (!r.models.length) setError('The server did not list any models. Enter a model name instead.'); else setError(null); }
        catch (e) { setError(e instanceof Error ? e.message : 'Could not list models'); } finally { setBusy(null); }
    }, [p.id]);

    const status = blocked ? { text: 'Switched off (this deployment keeps data on the machine)', tone: 'down' }
        : p.configured ? { text: `Connected, using ${SOURCE_TEXT[p.source ?? 'server']}`, tone: 'ok' } : { text: 'Not connected', tone: 'idle' };
    const suggestions = [...new Set([...p.models, ...found])];

    return (
        <article className={`pv${p.configured ? ' pv--on' : ''}`} aria-label={p.label}>
            <header className="pv__head">
                <div>
                    <h3 className="pv__name">{p.label}{isDefault && <span className="pv__default">default</span>}</h3>
                    <p className="pv__hint">{p.hint}</p>
                </div>
                <span className={`pv__status pv__status--${status.tone}`}><span className="set-dot" aria-hidden />{status.text}</span>
            </header>

            <p className="pv__privacy">{p.cloud ? 'Prompts and documents sent to this provider leave your machine.' : 'Runs on your own hardware or network. Nothing is sent to a third party.'}</p>
            {!p.installed && <p className="set-error" role="alert">The server is missing the library for this provider. Ask your administrator to install it.</p>}

            {p.fields.map(f => {
                const mine = f.source === 'yours';
                const open = editing[f.name] || !f.set || (f.set && f.source !== 'yours' && editing[f.name]);
                return (
                    <div key={f.name} className="pv__field">
                        <label htmlFor={`pv-${p.id}-${f.name}`}>{f.label}</label>
                        {f.set && !open ? (
                            <div className="pv__value">
                                <span className="pv__mask">{f.preview}</span>
                                <span className="pv__src">{SOURCE_TEXT[f.source ?? 'server']}</span>
                                {!locked && <button type="button" className="set-link" onClick={() => setEditing(e => ({ ...e, [f.name]: true }))}>{mine ? 'Replace' : 'Use my own'}</button>}
                                {!locked && mine && <button type="button" className="set-link" disabled={busy === f.name} onClick={() => removeField(f.name)}>Remove</button>}
                            </div>
                        ) : (
                            <input id={`pv-${p.id}-${f.name}`} className="set-input" type={f.secret ? 'password' : 'text'} autoComplete="off" spellCheck={false} disabled={locked || blocked}
                                placeholder={f.kind === 'url' ? 'http://localhost:11434' : f.secret ? 'Paste the key' : ''} value={draft[f.name] ?? ''} onChange={e => setDraft(d => ({ ...d, [f.name]: e.target.value }))} />
                        )}
                        {f.help && (!f.set || open) && <p className="set-field__help">{f.help}</p>}
                    </div>
                );
            })}

            {p.presets.length > 0 && (
                <div className="pv__presets"><span className="set-field__help">Quick fill:</span>
                    {p.presets.map(x => <button key={x.label} type="button" className="set-chip" onClick={() => setDraft(d => ({ ...d, LLM_CUSTOM_BASE_URL: x.url }))}>{x.label}</button>)}
                </div>
            )}
            {p.caveat && <p className="pv__caveat">{p.caveat}</p>}
            {error && <p className="set-error" role="alert">{error}</p>}

            <div className="pv__actions">
                {dirty && !locked && <Button size="sm" variant="primary" loading={busy === 'save'} onClick={save}>Save {p.fields.some(f => f.secret && draft[f.name]) ? 'key' : 'address'}</Button>}
                {dirty && admin && <label className="pv__share"><input type="checkbox" checked={shared} onChange={e => setShared(e.target.checked)} /> Save for everyone in the workspace</label>}
                {dirty && <Button size="sm" variant="ghost" onClick={() => { setDraft({}); setEditing({}); }}>Cancel</Button>}
            </div>

            <div className="pv__test">
                <label className="pv__test-model">
                    <span className="set-field__help">Model to test</span>
                    <input className="set-input" list={`pv-models-${p.id}`} placeholder={p.defaults ? p.defaults.light : 'model name'} value={model} onChange={e => setModel(e.target.value)} />
                    <datalist id={`pv-models-${p.id}`}>{suggestions.map(m => <option key={m} value={m} />)}</datalist>
                </label>
                <div className="pv__test-actions">
                    {(p.id === 'ollama' || p.id === 'custom' || p.id === 'openai') && p.configured && <Button size="sm" variant="ghost" loading={busy === 'models'} onClick={findModels}>Find models</Button>}
                    <Button size="sm" variant="outline" loading={busy === 'test'} disabled={blocked || (!p.configured && !dirty)} onClick={test}>Test connection</Button>
                    {p.configured && !blocked && (p.defaults
                        ? <Button size="sm" variant="primary" onClick={() => onUseForAll(p)}>Use for all jobs</Button>
                        : <Button size="sm" variant="primary" onClick={() => setPick(pick ? null : { strong: model, light: model })}>Use for all jobs…</Button>)}
                </div>
                {result && <p className={result.ok ? 'pv__ok' : 'set-error'} role="status">{result.message}{result.ok && result.latency_ms ? ` (${result.latency_ms} ms)` : ''}</p>}
            </div>

            {pick && (
                <div className="pv__pick">
                    <p className="set-field__help">{p.label} has no built-in choice. Name the model for writing and analysis, and a smaller one for routing and checking (they can be the same).</p>
                    <div className="set-grid">
                        <label className="set-stack">Writing and analysis<input className="set-input" list={`pv-models-${p.id}`} value={pick.strong} onChange={e => setPick({ ...pick, strong: e.target.value })} /></label>
                        <label className="set-stack">Routing and checking<input className="set-input" list={`pv-models-${p.id}`} value={pick.light} onChange={e => setPick({ ...pick, light: e.target.value })} /></label>
                    </div>
                    <Button size="sm" variant="primary" disabled={!pick.strong.trim() || !pick.light.trim()} onClick={() => { onUseForAll(p, { strong: pick.strong.trim(), light: pick.light.trim() }); setPick(null); }}>Apply to the form</Button>
                </div>
            )}
        </article>
    );
};

/** Connect the language models you want to use: your own keys, a local Ollama, or any OpenAI-compatible server. */
export const ProvidersPanel: React.FC<Props> = ({ defaultProvider, onDefaultProvider, onUseForAll }) => {
    const { user } = useAuth();
    const data = useProvidersStore(s => s.data);
    const error = useProvidersStore(s => s.error);
    const load = useProvidersStore(s => s.load);
    useEffect(() => { void load(true); }, [load]);

    if (error) return <p className="set-error" role="alert">{error}</p>;
    if (!data) return <p className="set-empty">Loading providers…</p>;

    return (
        <div className="pv-wrap">
            {data.local_only && <p className="set-banner">This deployment keeps everything on the machine, so cloud providers are switched off. Use Ollama or a self-hosted server.</p>}
            {data.locked && <p className="set-banner">Provider settings are managed by the server’s environment here, so keys cannot be changed from this screen.</p>}
            {!data.encryption_ok && <p className="set-error" role="alert">The server cannot store keys safely (no encryption key). Ask your administrator to set APP_SECRET_KEY.</p>}

            <div className="set-group">
                <div className="set-group__head"><h3>Default provider</h3><p>Presets and “Use for all jobs” start from this. Each job can still use a different provider under Models.</p></div>
                <div className="set-fields"><div className="set-field">
                    <div className="set-field__label"><label htmlFor="pv-default">Default provider</label></div>
                    <div className="set-field__control">
                        <select id="pv-default" className="set-input" value={defaultProvider} onChange={e => onDefaultProvider(e.target.value)}>
                            {data.providers.map(p => <option key={p.id} value={p.id}>{p.label}{p.configured ? '' : ' (not connected)'}</option>)}
                        </select>
                    </div>
                </div></div>
            </div>

            <div className="pv-list">
                {data.providers.map(p => (
                    <ProviderCard key={p.id} p={p} isDefault={p.id === defaultProvider} admin={user?.role === 'admin'} locked={data.locked} localOnly={data.local_only} onUseForAll={onUseForAll} />
                ))}
            </div>

            <div className="set-group">
                <div className="set-group__head"><h3>What still uses OpenAI</h3></div>
                <p className="pv__embed">{data.embeddings.note} Currently <strong>{data.embeddings.model}</strong> ({data.embeddings.dimensions} dimensions). SEC filing ingestion, image captions and handwritten-note transcription also use the server’s OpenAI key.</p>
            </div>
        </div>
    );
};
