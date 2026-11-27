import React, { useCallback, useEffect, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { mcpServersApi, type McpList, type McpServer } from '../../services/mcpServersApi';

const EMPTY = { name: '', url: '', description: '', auth_name: 'Authorization', auth_value: '', allow_actions: false };

/** Servers you connect yourself. The quant agent can use their tools for your questions; nobody else sees them. */
export const McpServersPanel: React.FC = () => {
    const [data, setData] = useState<McpList | null>(null);
    const [form, setForm] = useState<typeof EMPTY | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [open, setOpen] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try { setData(await mcpServersApi.list()); setError(null); } catch (e) { setError(e instanceof Error ? e.message : 'Could not load your servers'); }
    }, []);
    useEffect(() => { void load(); }, [load]);

    const run = async (key: string, fn: () => Promise<unknown>) => {
        setBusy(key); setError(null);
        try { await fn(); await load(); } catch (e) { setError(e instanceof Error ? e.message : 'That did not work'); } finally { setBusy(null); }
    };
    const add = () => form && run('add', async () => {
        await mcpServersApi.add({ ...form, auth_value: form.auth_value || undefined, description: form.description || undefined });
        setForm(null);
    });
    const toggleActions = (s: McpServer) => {
        if (!s.allow_actions && !window.confirm(`Allow "${s.name}" to make changes?\n\nIts tools that change something outside this app will be able to run, each one only after you confirm it in the chat.`)) return;
        void run(s.id, () => mcpServersApi.patch(s.id, { allow_actions: !s.allow_actions }));
    };

    return (
        <div className="eng">
            <div className="eng__top">
                <p className="set-panel__help">Connect an MCP server (a remote address that speaks the Model Context Protocol) and the quant agent can use its tools when you ask. Tools that only read run freely; anything that changes something is blocked until you allow it.</p>
                {data && !form && data.servers.length < data.max && <Button variant="primary" size="sm" onClick={() => setForm({ ...EMPTY })}>Connect a server</Button>}
            </div>
            {error && <p className="set-error" role="alert">{error}</p>}

            {form && (
                <div className="eng__item">
                    <label className="set-label">Name<input className="docs-input" value={form.name} maxLength={60} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
                    <label className="set-label">Address<input className="docs-input" placeholder="https://tools.example.com/mcp" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} /></label>
                    <label className="set-label">What it is for<input className="docs-input" placeholder="Our internal pricing models" value={form.description} maxLength={500} onChange={e => setForm({ ...form, description: e.target.value })} /></label>
                    <label className="set-label">Sign-in header (optional)
                        <span style={{ display: 'flex', gap: 8 }}>
                            <input className="docs-input" style={{ maxWidth: 160 }} value={form.auth_name} onChange={e => setForm({ ...form, auth_name: e.target.value })} aria-label="Header name" />
                            <input className="docs-input" type="password" autoComplete="off" placeholder="Bearer …" value={form.auth_value} onChange={e => setForm({ ...form, auth_value: e.target.value })} aria-label="Header value" />
                        </span>
                    </label>
                    <div className="eng__actions">
                        <Button variant="primary" size="sm" loading={busy === 'add'} disabled={!form.name.trim() || !form.url.trim()} onClick={add}>Connect and test</Button>
                        <Button variant="ghost" size="sm" onClick={() => setForm(null)}>Cancel</Button>
                    </div>
                </div>
            )}

            {data && data.servers.length === 0 && !form && <p className="eng__install">No servers yet.{data.private_allowed ? '' : ' Servers on a private network or this machine are not allowed on this installation.'}</p>}
            <ul className="mcp-list">
                {data?.servers.map(s => (
                    <li key={s.id} className={`mcp-row${s.enabled ? '' : ' mcp-row--off'}`}>
                        <div className="mcp-row__main">
                            <div className="mcp-row__title">
                                <strong>{s.name}</strong>
                                <span className={`mcp-pill${s.last_error ? ' mcp-pill--bad' : s.enabled ? ' mcp-pill--ok' : ''}`}>{s.last_error ? 'Needs attention' : s.enabled ? 'On' : 'Off'}</span>
                            </div>
                            <p className="mcp-row__meta">{s.description || s.url} · {s.tools.length} tool{s.tools.length === 1 ? '' : 's'}{s.allow_actions ? ' · can make changes' : ''}</p>
                            {s.last_error && <p className="set-error" role="alert">{s.last_error}</p>}
                            {open === s.id && (
                                <div className="mcp-detail">
                                    <p className="mcp-row__meta"><code>{s.url}</code>{s.has_auth ? ' · signed in' : ''}</p>
                                    <ul className="mcp-tools">
                                        {s.tools.map(t => <li key={t.name}><code>{t.name}</code><span>{t.read_only ? 'reads' : 'changes things'}</span></li>)}
                                    </ul>
                                    <label className="mcp-check">
                                        <input type="checkbox" checked={s.allow_actions} disabled={busy === s.id} onChange={() => toggleActions(s)} />
                                        Allow tools that change things (each is confirmed in the chat first)
                                    </label>
                                    <div className="eng__actions">
                                        <Button size="sm" variant="outline" loading={busy === s.id} onClick={() => run(s.id, () => mcpServersApi.test(s.id))}>Test again</Button>
                                        <Button size="sm" variant="outline" disabled={busy === s.id} onClick={() => run(s.id, () => mcpServersApi.patch(s.id, { enabled: !s.enabled }))}>{s.enabled ? 'Turn off' : 'Turn on'}</Button>
                                        <Button size="sm" variant="ghost" disabled={busy === s.id} onClick={() => { if (window.confirm(`Remove "${s.name}"?`)) void run(s.id, () => mcpServersApi.remove(s.id)); }}>Remove</Button>
                                    </div>
                                </div>
                            )}
                        </div>
                        <button type="button" className="mcp-row__toggle" aria-expanded={open === s.id} onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? 'Hide' : 'Manage'}</button>
                    </li>
                ))}
            </ul>
        </div>
    );
};
