import React, { useEffect, useState } from 'react';
import { settingsApi, type Usage } from '../../services/settingsApi';

const fmt = (n: number) => n.toLocaleString();

export const UsagePanel: React.FC = () => {
    const [u, setU] = useState<Usage | null>(null);
    useEffect(() => { settingsApi.usage().then(setU).catch(() => setU(null)); }, []);
    if (!u) return null;
    const pct = u.budget ? Math.min(100, Math.round((u.tokens / u.budget) * 100)) : 0;
    const roles = Object.entries(u.by_role).sort((a, b) => b[1].tokens - a[1].tokens);
    return (
        <div className="set-usage" aria-label="Usage this month">
            <div className="set-usage__top">
                <div><strong>{fmt(u.tokens)}</strong> tokens this month</div>
                <div className="set-usage__cost">≈ ${u.estimated_cost_usd.toFixed(2)} <span title="Estimated from list prices">(estimate)</span></div>
            </div>
            {u.budget > 0 && (
                <div className="set-meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Share of monthly token budget used">
                    <div className={`set-meter__bar${u.over_budget ? ' set-meter__bar--over' : ''}`} style={{ width: `${pct}%` }} />
                </div>
            )}
            {u.over_budget && <p className="set-error" role="alert">You are over your monthly token budget of {fmt(u.budget)}. Answers still run; raise the budget below or switch to cheaper models.</p>}
            {(u.unpriced_tokens ?? 0) > 0 && (
                <p className="set-usage__note">
                    {fmt(u.unpriced_tokens!)} tokens are from models with no known price and are not in the cost above.
                    An administrator can set their prices with the <code>model_prices</code> setting.
                </p>
            )}
            {(u.by_model?.length ?? 0) > 0 && (
                <ul className="set-usage__roles" aria-label="By model">
                    {u.by_model!.slice(0, 8).map((m) => (
                        <li key={`${m.model}-${m.priced}`}>
                            <span>{m.model}</span>
                            <span>{fmt(m.tokens)} · {m.priced ? `≈ $${m.cost_usd.toFixed(2)}` : 'price unknown'}</span>
                        </li>
                    ))}
                </ul>
            )}
            {roles.length > 0 && (
                <ul className="set-usage__roles" aria-label="By task">
                    {roles.slice(0, 6).map(([r, v]) => <li key={r}><span>{r.replace(/[._]/g, ' ')}</span><span>{fmt(v.tokens)}</span></li>)}
                </ul>
            )}
            {u.prices_as_of && <p className="set-usage__note">Costs are estimates from list prices checked {u.prices_as_of}.</p>}
        </div>
    );
};
