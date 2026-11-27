import React, { useCallback, useEffect, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { settingsApi } from '../../services/settingsApi';

const LABELS: Record<string, string> = {
    stock_info: 'Stock information', technical: 'Technical analysis', research: 'Research', options: 'Options intelligence', postgres: 'Postgres',
};

/** Which market-data servers answer right now (the quant agents depend on them). */
export const QuantHealth: React.FC = () => {
    const [servers, setServers] = useState<Record<string, boolean> | null>(null);
    const [agentsReady, setAgentsReady] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const check = useCallback(async () => {
        setBusy(true);
        try {
            const h = await settingsApi.quantHealth();
            setServers(h.servers_ready);
            setAgentsReady(h.agents_ready);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not check the quant servers');
        } finally {
            setBusy(false);
        }
    }, []);
    useEffect(() => { void check(); }, [check]);

    return (
        <div className="set-health">
            <div className="set-row">
                <strong>Market-data servers</strong>
                <Button variant="ghost" size="sm" loading={busy} onClick={check}>Check again</Button>
            </div>
            {error && <p className="set-error" role="alert">{error}</p>}
            {servers && (
                <ul className="set-health__list">
                    {Object.entries(servers).map(([k, ok]) => (
                        <li key={k}><span className={`set-dot${ok ? ' set-dot--ok' : ' set-dot--down'}`} aria-hidden /><span>{LABELS[k] ?? k}</span><span className="set-health__state">{ok ? 'running' : 'not responding'}</span></li>
                    ))}
                </ul>
            )}
            {servers && !agentsReady && <p className="set-field__help">The quant agents are not ready. Start the servers with <code>scripts/start_mcp_servers.sh</code> and restart the API.</p>}
        </div>
    );
};
