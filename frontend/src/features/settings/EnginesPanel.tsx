import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { readingApi, type EngineTest, type EnginesResponse, type LocalEngine } from '../../services/readingApi';

interface Props {
    selected: string;
    onSelect: (engineId: string) => void;
    visionModels: string[];
}

const mb = (b?: number) => (b ? `${(b / (1024 * 1024)).toFixed(0)} MB` : '');

const modelText = (e: LocalEngine): string => {
    if (!e.ready) return 'Not installed';
    switch (e.models.state) {
        case 'bundled': return 'Models included';
        case 'downloaded': return `Models downloaded (${mb(e.models.bytes)})`;
        case 'missing': return `Models not downloaded yet (about ${e.models.approx_mb} MB)`;
        default: return 'Ready';
    }
};

/** How scans are read on this machine: pick the engine, download its models, and try it on a sample page. */
export const EnginesPanel: React.FC<Props> = ({ selected, onSelect, visionModels }) => {
    const [data, setData] = useState<EnginesResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [tests, setTests] = useState<Record<string, EngineTest>>({});
    const timer = useRef<number | undefined>(undefined);

    const load = useCallback(async () => {
        try { setData(await readingApi.engines()); setError(null); } catch (e) { setError(e instanceof Error ? e.message : 'Could not load the engines'); }
    }, []);
    useEffect(() => { void load(); }, [load]);

    // while a download runs, keep checking so its state turns to done or failed on its own
    const downloading = data?.local_engines.some(e => e.prepare?.state === 'running');
    useEffect(() => {
        if (!downloading) return;
        timer.current = window.setInterval(() => void load(), 3000);
        return () => window.clearInterval(timer.current);
    }, [downloading, load]);

    const run = async (key: string, kind: 'local' | 'model', id: string) => {
        setBusy(key);
        try { setTests(t => ({ ...t, [key]: { ok: false } })); const r = await readingApi.test(kind, id); setTests(t => ({ ...t, [key]: r })); }
        catch (e) { setTests(t => ({ ...t, [key]: { ok: false, error: e instanceof Error ? e.message : 'The test could not run' } })); }
        finally { setBusy(null); }
    };
    const prepare = async (id: string) => {
        setBusy(`prep-${id}`); setError(null);
        try { await readingApi.prepare(id); await load(); } catch (e) { setError(e instanceof Error ? e.message : 'Could not start the download'); } finally { setBusy(null); }
    };

    if (!data) return <p className={error ? 'set-error' : 'set-empty'} role={error ? 'alert' : undefined}>{error ?? 'Loading engines…'}</p>;
    const plan = data.plan;

    return (
        <div className="set-group">
            <div className="set-group__head">
                <h3>Reading engines</h3>
                <p>Scans are read on this machine first, and a vision model is used only where that reading is doubtful. Choose the local engine, download its models, and test it.</p>
            </div>
            <div className="eng">
                {error && <p className="set-error" role="alert">{error}</p>}
                {data.local_only && <p className="set-banner">This deployment keeps everything on the machine, so models are not downloaded from here.</p>}
                <ul className="eng__list">
                    {data.local_engines.map(e => {
                        const test = tests[e.id];
                        const isSel = (plan.local_engine ?? selected) === e.id;
                        const running = e.prepare?.state === 'running';
                        return (
                            <li key={e.id} className={`eng__item${isSel ? ' eng__item--on' : ''}`}>
                                <div className="eng__top">
                                    <div>
                                        <strong>{e.label}</strong>{isSel && <span className="pv__default">in use</span>}
                                        <p className="set-field__help">{e.summary}</p>
                                        <p className={`eng__state${e.ready ? '' : ' eng__state--off'}`}>{modelText(e)}{e.cross_checked ? ' · checked against a second reading' : ''}</p>
                                    </div>
                                    <div className="eng__actions">
                                        {e.ready && !isSel && <Button size="sm" variant="primary" onClick={() => onSelect(e.id)}>Use this engine</Button>}
                                        {e.ready && e.models.state === 'missing' && data.can_prepare && (
                                            <Button size="sm" variant="outline" loading={running || busy === `prep-${e.id}`} onClick={() => prepare(e.id)}>Download models</Button>
                                        )}
                                        {e.ready && <Button size="sm" variant="ghost" loading={busy === e.id} onClick={() => run(e.id, 'local', e.id)}>Test</Button>}
                                    </div>
                                </div>
                                {!e.ready && e.hint && (
                                    <p className="eng__install">To install on the server: <code>{e.hint}</code>, then restart the API.</p>
                                )}
                                {e.models.state === 'missing' && !data.can_prepare && <p className="set-field__help">An administrator can download its models here.</p>}
                                {e.prepare?.state === 'failed' && <p className="set-error" role="alert">{e.prepare.error}</p>}
                                {running && <p className="set-field__help" role="status">Downloading models. The first read after this is slower than later ones.</p>}
                                {test && (test.ok ? (
                                    <p className="pv__ok" role="status">Read the sample page in {test.seconds}s{test.confidence != null ? `, confidence ${Math.round(test.confidence * 100)}%` : ''}; found {Math.round((test.score ?? 0) * 100)}% of the known values.</p>
                                ) : test.error ? <p className="set-error" role="alert">{test.error}</p> : null)}
                            </li>
                        );
                    })}
                </ul>
                {plan.notes.length > 0 && <ul className="eng__notes">{plan.notes.map(n => <li key={n}>{n}</li>)}</ul>}
                <div className="eng__vision">
                    <strong>Vision models</strong>
                    <p className="set-field__help">Used only for pages the local reading is unsure of{plan.mode === 'vision' ? ' (and for every scan in Accuracy mode)' : ''}. A test sends one sample page and costs a few hundred tokens.</p>
                    {visionModels.map((m, i) => (
                        <div key={`${m}-${i}`} className="eng__model">
                            <code>{m}</code>
                            <Button size="sm" variant="ghost" loading={busy === `m-${i}`} onClick={() => { if (window.confirm(`Send one sample page to ${m}? This uses your model provider and costs a few hundred tokens.`)) void run(`m-${i}`, 'model', m); }}>Test</Button>
                            {tests[`m-${i}`]?.ok && <span className="pv__ok" role="status">Read it in {tests[`m-${i}`].seconds}s, {Math.round((tests[`m-${i}`].score ?? 0) * 100)}% of known values, {tests[`m-${i}`].tokens} tokens</span>}
                            {tests[`m-${i}`]?.error && <span className="set-error" role="alert">{tests[`m-${i}`].error}</span>}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};
