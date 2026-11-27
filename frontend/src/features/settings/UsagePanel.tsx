import React, { useEffect, useState } from 'react';
import { settingsApi, type Usage } from '../../services/settingsApi';

const fmt = (n: number) => n.toLocaleString();
const usd = (n: number) => (n > 0 && n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`);

const PROVIDERS: Record<string, string> = { anthropic: 'Anthropic', gemini: 'Gemini', ollama: 'Ollama', custom: 'Self-hosted' };

/** "anthropic:claude-sonnet-5-5" -> { name: "claude-sonnet-5-5", provider: "Anthropic" }; a bare name is OpenAI. */
function splitModel(spec: string): { name: string; provider: string } {
    const i = spec.indexOf(':');
    const head = i > 0 ? spec.slice(0, i) : '';
    if (head && PROVIDERS[head]) return { name: spec.slice(i + 1), provider: PROVIDERS[head] };
    return { name: spec, provider: 'OpenAI' };
}

interface ModelRow { model: string; tokens: number; cost: number; priced: boolean }

/** Tokens and estimated cost this month, in total and per model. */
export const UsagePanel: React.FC = () => {
    const [u, setU] = useState<Usage | null>(null);
    useEffect(() => { settingsApi.usage().then(setU).catch(() => setU(null)); }, []);
    if (!u) return null;

    const rows = new Map<string, ModelRow>();
    for (const m of u.by_model ?? []) {
        const r = rows.get(m.model) ?? { model: m.model, tokens: 0, cost: 0, priced: false };
        r.tokens += m.tokens;
        r.cost += m.cost_usd;
        r.priced = r.priced || m.priced;
        rows.set(m.model, r);
    }
    const models = [...rows.values()].sort((a, b) => b.tokens - a.tokens);
    const pct = u.budget ? Math.min(100, Math.round((u.tokens / u.budget) * 100)) : 0;
    const month = new Date(`${u.month_start}T00:00:00`).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });

    return (
        <section className="set-usage" aria-label="Usage this month">
            <header className="set-usage__head">
                <h3 className="set-usage__title">Usage</h3>
                <span className="set-usage__period">{month}</span>
            </header>

            <dl className="set-usage__figures">
                <div className="set-usage__figure">
                    <dt>Tokens</dt>
                    <dd className="tabular">{fmt(u.tokens)}</dd>
                </div>
                <div className="set-usage__figure">
                    <dt>Estimated cost</dt>
                    <dd className="tabular">{usd(u.estimated_cost_usd)}</dd>
                </div>
            </dl>

            {u.budget > 0 && (
                <div className="set-usage__budget">
                    <div className="set-meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Share of monthly token budget used">
                        <div className={`set-meter__bar${u.over_budget ? ' set-meter__bar--over' : ''}`} style={{ width: `${pct}%` }} />
                    </div>
                    <span className="set-usage__budget-label">{pct}% of your {fmt(u.budget)}-token monthly budget</span>
                </div>
            )}
            {u.over_budget && <p className="set-error" role="alert">You are over your monthly token budget of {fmt(u.budget)}. Answers still run; raise the budget below or switch to cheaper models.</p>}

            {models.length > 0 ? (
                <table className="set-usage__table">
                    <thead>
                        <tr>
                            <th scope="col">Model</th>
                            <th scope="col" className="num">Tokens</th>
                            <th scope="col" className="num" title="This model's part of all tokens used this month">Share</th>
                            <th scope="col" className="num">Cost</th>
                        </tr>
                    </thead>
                    <tbody>
                        {models.map((m) => {
                            const { name, provider } = splitModel(m.model);
                            const share = u.tokens ? Math.round((100 * m.tokens) / u.tokens) : 0;
                            return (
                                <tr key={m.model}>
                                    <th scope="row">
                                        <span className="set-usage__model">{name}</span>
                                        <span className="set-usage__provider">{provider}</span>
                                    </th>
                                    <td className="num tabular">{fmt(m.tokens)}</td>
                                    <td className="num tabular set-usage__muted">{share}%</td>
                                    <td className="num tabular">{m.priced ? usd(m.cost) : <span className="set-usage__muted" title="This model has no known price">—</span>}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            ) : (
                <p className="set-usage__muted">No model usage yet this month.</p>
            )}
        </section>
    );
};
