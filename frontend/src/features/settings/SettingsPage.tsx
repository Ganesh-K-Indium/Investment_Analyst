import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { PageLayout } from '../../components/layout/PageLayout';
import { PageHeader } from '../../components/layout/PageHeader';
import { Button } from '../../components/ui/Button';
import { useAuth } from '../../context/useAuth';
import { useTheme } from '../../context/useTheme';
import { portfolioApi, type PortfolioResponse } from '../../services/portfolioApi';
import { settingsApi, type Framework, type Settings, type SectionSpec } from '../../services/settingsApi';
import { useSettingsStore } from '../../store/settingsStore';
import { FieldControl } from './FieldControl';
import { FrameworkEditor } from './FrameworkEditor';
import { PromptEditor } from './PromptEditor';
import { QuantHealth } from './QuantHealth';
import { EnginesPanel } from './EnginesPanel';
import { McpServersPanel } from './McpServersPanel';
import { UsagePanel } from './UsagePanel';
import { PipelineStrip } from './PipelineStrip';
import { applyPreset, type Defaults, type Preset } from './presets';
import { ProvidersPanel } from './ProvidersPanel';
import { useProvidersStore } from '../../store/providersStore';
import { joinSpec, splitSpec, type Provider } from '../../services/providersApi';
import { GROUPS, sectionSummary } from './summary';
import '../documents/DocumentsPage.css';
import './SettingsPage.css';

type Scope = 'user' | number;

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** /settings: every option of the pipeline, rendered from the server's schema, for you or for one portfolio. */
export const SettingsPage: React.FC = () => {
    const { userId } = useAuth();
    const { theme, toggleTheme } = useTheme();
    const schema = useSettingsStore(s => s.schema);
    const loadStore = useSettingsStore(s => s.load);
    const applyStore = useSettingsStore(s => s.apply);

    const [scope, setScope] = useState<Scope>('user');
    const [portfolios, setPortfolios] = useState<PortfolioResponse[]>([]);
    const [saved, setSaved] = useState<Settings>({});
    const [effective, setEffective] = useState<Settings>({});
    const [draft, setDraft] = useState<Settings>({});
    const [active, setActive] = useState<string>('providers');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    useEffect(() => { void loadStore(); }, [loadStore]);
    useEffect(() => {
        if (userId) portfolioApi.getAllPortfolios(String(userId)).then(setPortfolios).catch(() => setPortfolios([]));
    }, [userId]);

    const load = useCallback(async (s: Scope) => {
        setError(null);
        try {
            const r = s === 'user' ? await settingsApi.get() : await settingsApi.getPortfolio(s);
            setSaved(r.saved); setEffective(r.effective); setDraft(clone(r.effective));
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not load settings');
        }
    }, []);
    useEffect(() => { void load(scope); }, [scope, load]);

    const providerList = useProvidersStore(s => s.data?.providers);
    const providers = useMemo(() => providerList ?? [], [providerList]);   // never `?? []` inside the selector: a new array each call makes the store re-render forever
    const loadProviders = useProvidersStore(s => s.load);
    useEffect(() => { void loadProviders(); }, [loadProviders]);
    const sections = useMemo<SectionSpec[]>(() => schema ? [{ id: 'providers', label: 'AI providers', help: 'Connect the language models you want to use: OpenAI, Anthropic, Google, a local Ollama, or any OpenAI-compatible server. Keys are encrypted and never shown again.', fields: [] }, ...schema.sections] : [], [schema]);
    const section: SectionSpec | undefined = sections.find(s => s.id === active) ?? sections[0];

    const patch = useMemo(() => {
        const out: Settings = {};
        for (const sec of sections) {
            for (const f of sec.fields) {
                const a = draft[sec.id]?.[f.key], b = effective[sec.id]?.[f.key];
                if (a !== undefined && !same(a, b)) (out[sec.id] ??= {})[f.key] = a;
            }
        }
        return out;
    }, [draft, effective, sections]);
    const dirtySections = new Set(Object.keys(patch));
    const dirty = dirtySections.size > 0;

    const setValue = (sec: string, key: string, value: unknown) => setDraft(d => ({ ...d, [sec]: { ...d[sec], [key]: value } }));
    const overridden = (sec: string, key: string) => saved[sec]?.[key] !== undefined;

    const afterChange = (r: { saved: Settings; effective: Settings }) => {
        setSaved(r.saved); setEffective(r.effective); setDraft(clone(r.effective));
        if (scope === 'user') {
            applyStore(r);
            const t = r.effective.ui?.theme;
            if ((t === 'light' || t === 'dark') && t !== theme) toggleTheme();
        }
    };

    const save = async () => {
        setBusy(true); setError(null); setNotice(null);
        try {
            const r = scope === 'user' ? await settingsApi.save(patch) : await settingsApi.savePortfolio(scope, patch);
            afterChange(r);
            setNotice('Saved. New questions use these settings.');
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not save');
        } finally {
            setBusy(false);
        }
    };

    const resetSection = async () => {
        if (!section || !window.confirm(`Reset "${section.label}" to the defaults${scope === 'user' ? '' : ' for this portfolio (your own settings apply again)'}?`)) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            const r = scope === 'user' ? await settingsApi.reset(section.id) : await settingsApi.resetPortfolio(scope, section.id);
            afterChange(r);
            setNotice(`${section.label} reset.`);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not reset');
        } finally {
            setBusy(false);
        }
    };

    const defaultProviderId = (draft.models?.default_provider as string | undefined) ?? 'openai';
    const knownIds = providers.map(p => p.id);
    // What a preset or "use for all" means for the default provider: its own writing/smaller models, else what the form already has.
    const defaults: Defaults = useMemo(() => {
        const p = providers.find(x => x.id === defaultProviderId);
        if (p?.defaults) return p.defaults;
        return { strong: String(draft.models?.generator_model ?? 'gpt-4o'), light: String(draft.models?.router_model ?? 'gpt-4o-mini') };
    }, [providers, defaultProviderId, draft.models]);

    const useForAll = (p: Provider, chosen?: { strong: string; light: string }) => {
        const strong = p.defaults?.strong ?? joinSpec(p.id, chosen?.strong ?? '');
        const light = p.defaults?.light ?? joinSpec(p.id, chosen?.light ?? '');
        setDraft(d => {
            const m: Record<string, unknown> = { ...d.models, default_provider: p.id };
            ['generator', 'analysis', 'macro_writer', 'quant_supervisor', 'quant_agent'].forEach(r => { m[`${r}_model`] = strong; });
            ['router', 'grader', 'verifier', 'rewriter', 'macro_planner', 'summarizer'].forEach(r => { m[`${r}_model`] = light; });
            const docs = p.vision ? { ...d.documents, vision_model_primary: strong, vision_model_secondary: light } : d.documents;
            return { ...d, models: m, documents: docs };
        });
        setActive('models');
        setNotice(null);
    };

    // Jobs pointed at a provider that is not connected: say so up front instead of letting a question fail.
    const disconnected = useMemo(() => {
        if (!providers.length) return [] as { job: string; provider: string }[];
        const out: { job: string; provider: string }[] = [];
        for (const [k, v] of Object.entries(draft.models ?? {})) {
            if (!k.endsWith('_model') || typeof v !== 'string') continue;
            const prov = providers.find(p => p.id === splitSpec(v, knownIds).provider);
            if (prov && !prov.configured) out.push({ job: k.slice(0, -6).replace(/_/g, ' '), provider: prov.label });
        }
        return out;
    }, [draft.models, providers, knownIds]);

    const frameworks = (draft.modules?.custom as Framework[] | undefined) ?? [];
    const overrides = (draft.prompts?.overrides as Record<string, string> | undefined) ?? {};

    return (
        <>
        <PageLayout className="docs-page settings-page">
            <PageHeader
                title="Settings"
                description="Choose how your analyses are run: models, retrieval, answers, your own frameworks and prompts, your documents and the quant agents."
                actions={
                    <label className="set-scope">
                        <span>Applies to</span>
                        <select className="docs-select" value={String(scope)} onChange={e => { if (!dirty || window.confirm('Discard your unsaved changes?')) setScope(e.target.value === 'user' ? 'user' : Number(e.target.value)); }}>
                            <option value="user">All my analyses</option>
                            {portfolios.map(p => <option key={p.id} value={p.id}>Portfolio: {p.name}</option>)}
                        </select>
                    </label>
                }
            />

                {scope !== 'user' && <p className="set-banner">Settings here apply only to this portfolio and win over your general settings.</p>}

                {!schema ? <p className="set-empty">Loading settings…</p> : (
                    <>
                        {disconnected.length > 0 && (
                            <p className="set-banner set-banner--warn" role="status">
                                {[...new Set(disconnected.map(d => d.provider))].join(', ')} {new Set(disconnected.map(d => d.provider)).size === 1 ? 'is' : 'are'} not connected, but {disconnected.length} job{disconnected.length === 1 ? '' : 's'} use{disconnected.length === 1 ? 's' : ''} {new Set(disconnected.map(d => d.provider)).size === 1 ? 'it' : 'them'} ({disconnected.slice(0, 4).map(d => d.job).join(', ')}{disconnected.length > 4 ? ', …' : ''}). Questions will not run until you{' '}
                                <button type="button" className="set-link" onClick={() => setActive('providers')}>connect it</button> or pick another model.
                            </p>
                        )}
                        <PipelineStrip draft={draft} dirtySections={dirtySections} activeSection={section?.id ?? ''} onOpen={setActive}
                            onPreset={(p: Preset) => { setDraft(d => applyPreset(d, p, defaults)); setNotice(null); }} defaults={defaults} />

                        <div className="set-layout">
                            <nav className="set-tabs" aria-label="Settings sections">
                                {sections.map(sec => (
                                    <button key={sec.id} type="button" className={`set-tab${section?.id === sec.id ? ' set-tab--on' : ''}`} aria-current={section?.id === sec.id ? 'page' : undefined} onClick={() => setActive(sec.id)}>
                                        <span className="set-tab__name">{sec.label}{dirtySections.has(sec.id) && <span className="set-tab__dot" title="Unsaved changes" />}</span>
                                        <span className="set-tab__sum">{sectionSummary(sec.id, draft)}</span>
                                    </button>
                                ))}
                            </nav>

                            {section && (
                                <section className="set-panel" aria-labelledby="set-section-title">
                                    <header className="set-panel__head">
                                        <div>
                                            <h2 id="set-section-title">{section.label}</h2>
                                            {section.help && <p className="set-panel__help">{section.help}</p>}
                                        </div>
                                        <Button variant="ghost" size="sm" disabled={busy || !section.fields.some(f => overridden(section.id, f.key))} onClick={resetSection}>Reset section</Button>
                                    </header>

                                    {section.id === 'providers' && (
                                        <ProvidersPanel defaultProvider={defaultProviderId} onUseForAll={useForAll}
                                            onDefaultProvider={id => setValue('models', 'default_provider', id)} />
                                    )}
                                    {section.id === 'models' && scope === 'user' && <UsagePanel />}

                                    {(() => {
                                        const visible = section.fields.filter(f => f.type !== 'json');
                                        const groups = GROUPS[section.id];
                                        const placed = new Set(groups?.flatMap(g => g.keys) ?? []);
                                        const blocks = groups
                                            ? [...groups.map(g => ({ ...g, fields: g.keys.map(k => visible.find(f => f.key === k)).filter((f): f is NonNullable<typeof f> => Boolean(f)) })),
                                               { title: 'More', hint: undefined, keys: [], fields: visible.filter(f => !placed.has(f.key)) }]
                                            : [{ title: '', hint: undefined, keys: [], fields: visible }];
                                        return blocks.filter(g => g.fields.length).map(g => (
                                            <div key={g.title || 'all'} className="set-group">
                                                {g.title && <div className="set-group__head"><h3>{g.title}</h3>{g.hint && <p>{g.hint}</p>}</div>}
                                                <div className="set-fields">
                                                    {g.fields.map(f => (
                                                        <FieldControl key={f.key} spec={f} value={draft[section.id]?.[f.key]} onChange={v => setValue(section.id, f.key, v)} />
                                                    ))}
                                                </div>
                                            </div>
                                        ));
                                    })()}

                                    {section.id === 'modules' && (
                                        <div className="set-group">
                                            <div className="set-group__head"><h3>Your frameworks</h3><p>A repeatable way of analysing a company: when it runs, what it covers, and how it is written.</p></div>
                                            <FrameworkEditor frameworks={frameworks} onChange={next => setValue('modules', 'custom', next)} />
                                        </div>
                                    )}
                                    {section.id === 'prompts' && (
                                        <div className="set-group">
                                            <div className="set-group__head"><h3>Editable prompts</h3><p>Only prompts that are free text can be changed. Prompts that code parses stay fixed.</p></div>
                                            <PromptEditor overrides={overrides} onChange={next => setValue('prompts', 'overrides', next)} />
                                        </div>
                                    )}
                                    {section.id === 'documents' && (
                                        <EnginesPanel selected={String(draft.documents?.local_engine ?? 'tesseract')} onSelect={id => setValue('documents', 'local_engine', id)}
                                            visionModels={[String(draft.documents?.vision_model_primary ?? ''), String(draft.documents?.vision_model_secondary ?? '')].filter(Boolean)} />
                                    )}
                                    {section.id === 'quant' && <QuantHealth />}
                                    {section.id === 'quant' && (
                                        <div className="set-group">
                                            <div className="set-group__head"><h3>Your tools</h3><p>Your own MCP servers, available to the quant agent.</p></div>
                                            <McpServersPanel />
                                        </div>
                                    )}
                                </section>
                            )}
                        </div>
                    </>
                )}
        </PageLayout>

            {(dirty || error || notice) && (
                <div className="set-bar" role="region" aria-label="Save changes">
                    <span className={error ? 'set-bar__msg set-bar__msg--error' : 'set-bar__msg'} role={error ? 'alert' : 'status'}>
                        {error ?? (dirty ? `Unsaved changes in ${[...dirtySections].map(id => sections.find(s => s.id === id)?.label ?? id).join(', ')}` : notice)}
                    </span>
                    {dirty && (
                        <>
                            <Button variant="ghost" size="sm" disabled={busy} onClick={() => { setDraft(clone(effective)); setError(null); }}>Discard</Button>
                            <Button variant="primary" size="sm" loading={busy} onClick={save}>Save changes</Button>
                        </>
                    )}
                </div>
            )}
        </>
    );
};

export default SettingsPage;
