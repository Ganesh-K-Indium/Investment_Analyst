import React, { useMemo, useState } from 'react';
import { Modal } from '../../ui/Modal';
import { Button } from '../../ui/Button';
import { analysisTasksApi } from '../../../services/analysisTasksApi';
import { portfolioApi } from '../../../services/portfolioApi';
import type { Company } from '../../../data/fortune500Companies';
import { allCompanies } from '../../../data/foreignCompanies';
import './BatchRunModal.css';

type BatchAgent = 'ask' | 'alpha' | 'compare';

export interface BatchTargetPortfolio {
    id: string;
    name: string;
    tickers: string[];
    companies: Company[];
}

interface BatchRunModalProps {
    isOpen: boolean;
    onClose: () => void;
    userId: string;
    portfolios: BatchTargetPortfolio[];
}

const AGENT_OPTIONS: { key: BatchAgent; label: string; hint: string }[] = [
    { key: 'ask', label: 'Secondary Research — Ask', hint: 'Runs the same question against each selected portfolio’s documents.' },
    { key: 'alpha', label: 'Secondary Research — ALPHA', hint: 'Runs the full ALPHA framework for every company already in each selected portfolio. No question needed.' },
    { key: 'compare', label: 'Secondary Research — Compare', hint: 'Compares 2-3 companies (same SIC) for a single portfolio you pick below.' },
];

function generateId(prefix: string): string {
    const timestamp = new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14);
    const random = Math.random().toString(36).substring(2, 8);
    return `${prefix}_${timestamp}_${random}`;
}

export const BatchRunModal: React.FC<BatchRunModalProps> = ({ isOpen, onClose, userId, portfolios }) => {
    const [agent, setAgent] = useState<BatchAgent>('ask');
    const [query, setQuery] = useState('');
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
    const [compareCompany1, setCompareCompany1] = useState('');
    const [compareCompany2, setCompareCompany2] = useState('');
    const [compareCompany3, setCompareCompany3] = useState('');
    const [compareYear, setCompareYear] = useState<number>(2024);
    const [scheduleEnabled, setScheduleEnabled] = useState(false);
    const [scheduleAt, setScheduleAt] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState<{ queued: number; failed: { name: string; error: string }[] } | null>(null);

    const reset = () => {
        setAgent('ask');
        setQuery('');
        setSelectedIds(new Set());
        setCompareCompany1('');
        setCompareCompany2('');
        setCompareCompany3('');
        setCompareYear(2024);
        setScheduleEnabled(false);
        setScheduleAt('');
        setResult(null);
    };

    const handleClose = () => {
        if (submitting) return;
        reset();
        onClose();
    };

    const toggleSelected = (id: string) => {
        // Compare is scoped to a single portfolio — its company dropdowns are
        // drawn from that one portfolio's SIC universe, so picking a
        // different portfolio invalidates whatever was already chosen there.
        if (agent === 'compare') {
            setSelectedIds(new Set([id]));
            setCompareCompany1('');
            setCompareCompany2('');
            setCompareCompany3('');
            return;
        }
        setSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const selectCompareAgent = () => {
        setAgent('compare');
        setSelectedIds((prev) => (prev.size === 1 ? prev : new Set(prev.size > 0 ? [Array.from(prev)[0]] : [])));
        setCompareCompany1('');
        setCompareCompany2('');
        setCompareCompany3('');
    };

    const toggleSelectAll = () => {
        setSelectedIds((prev) => (prev.size === portfolios.length ? new Set() : new Set(portfolios.map((p) => p.id))));
    };

    const selectedCompareTarget = agent === 'compare'
        ? portfolios.find((p) => selectedIds.has(p.id)) ?? null
        : null;

    const compareAnchorSic = selectedCompareTarget?.companies[0]?.sic ?? null;

    // Same restriction the interactive Stock Compare bar uses: anchor SIC off
    // the portfolio's first company, then only offer other companies that
    // share it. Falls back to the portfolio's own holdings when no SIC is
    // known for them (ticker not in the curated company lists) so the
    // feature still works instead of showing an empty dropdown.
    const compareCompanyOptions = useMemo(() => {
        if (!selectedCompareTarget) return [];
        if (compareAnchorSic) {
            const sameSic = allCompanies.filter((c) => c.sic === compareAnchorSic);
            if (sameSic.length > 0) return sameSic;
        }
        return selectedCompareTarget.companies;
    }, [selectedCompareTarget, compareAnchorSic]);

    const needsQuery = agent === 'ask';
    const needsCompare = agent === 'compare';
    const canSubmit = selectedIds.size > 0
        && (!needsQuery || query.trim().length > 0)
        && (!needsCompare || (compareCompany1 !== '' && compareCompany2 !== '' && compareCompany1 !== compareCompany2))
        && !submitting;

    const handleSubmit = async () => {
        if (!canSubmit) return;
        setSubmitting(true);
        setResult(null);

        const scheduledAtIso = scheduleEnabled && scheduleAt ? new Date(scheduleAt).toISOString() : undefined;
        const targets = portfolios.filter((p) => selectedIds.has(p.id));

        const runOne = async (portfolio: BatchTargetPortfolio) => {
            const portfolioIdNum = parseInt(portfolio.id, 10);
            if (agent === 'ask') {
                const threadId = generateId('thread');
                await portfolioApi.createSession({ portfolio_id: portfolioIdNum, user_id: userId, thread_id: threadId });
                await analysisTasksApi.enqueueAsk({ query, thread_id: threadId, scheduled_at: scheduledAtIso });
            } else if (agent === 'compare') {
                const threadId = generateId('thread');
                await portfolioApi.createSession({ portfolio_id: portfolioIdNum, user_id: userId, thread_id: threadId });
                await analysisTasksApi.enqueueCompare({
                    company1: compareCompany1, company2: compareCompany2, company3: compareCompany3 || undefined,
                    user_id: userId, thread_id: threadId, year: compareYear, scheduled_at: scheduledAtIso,
                });
            } else {
                if (portfolio.tickers.length === 0) {
                    throw new Error('No companies in this portfolio');
                }
                const threadId = generateId('thread');
                await portfolioApi.createSession({ portfolio_id: portfolioIdNum, user_id: userId, thread_id: threadId });
                await analysisTasksApi.enqueueAlpha({
                    tickers: portfolio.tickers, user_id: userId, thread_id: threadId, scheduled_at: scheduledAtIso,
                });
            }
        };

        const outcomes = await Promise.allSettled(targets.map((p) => runOne(p)));
        const failed: { name: string; error: string }[] = [];
        let queued = 0;
        outcomes.forEach((outcome, idx) => {
            if (outcome.status === 'fulfilled') {
                queued += 1;
            } else {
                const reason = outcome.reason;
                failed.push({
                    name: targets[idx].name,
                    error: reason instanceof Error ? reason.message : 'Failed to queue',
                });
            }
        });

        setResult({ queued, failed });
        setSubmitting(false);
    };

    return (
        <Modal isOpen={isOpen} onClose={handleClose} title="Batch Run" size="lg">
            <div className="batch-run">
                <p className="batch-run__subtitle">
                    Queue the same run across many portfolios at once — everything shows up in each
                    portfolio's Overview tab as it finishes, so you can walk away and check back later.
                </p>

                {result ? (
                    <div className="batch-run__result">
                        <p className="batch-run__result-summary">
                            Queued {result.queued} of {result.queued + result.failed.length} run{result.queued + result.failed.length === 1 ? '' : 's'}.
                        </p>
                        {result.failed.length > 0 && (
                            <ul className="batch-run__result-failures">
                                {result.failed.map((f) => (
                                    <li key={f.name}><strong>{f.name}</strong> — {f.error}</li>
                                ))}
                            </ul>
                        )}
                        <div className="batch-run__actions">
                            <Button variant="outline" onClick={() => { reset(); }}>Queue another batch</Button>
                            <Button variant="primary" onClick={handleClose}>Done</Button>
                        </div>
                    </div>
                ) : (
                    <>
                        <div className="batch-run__field">
                            <label className="batch-run__label">Agent</label>
                            <div className="batch-run__agent-options">
                                {AGENT_OPTIONS.map((opt) => (
                                    <button
                                        key={opt.key}
                                        type="button"
                                        className={`batch-run__agent-option ${agent === opt.key ? 'batch-run__agent-option--active' : ''}`}
                                        onClick={() => (opt.key === 'compare' ? selectCompareAgent() : setAgent(opt.key))}
                                    >
                                        <span className="batch-run__agent-option-label">{opt.label}</span>
                                        <span className="batch-run__agent-option-hint">{opt.hint}</span>
                                    </button>
                                ))}
                            </div>
                        </div>

                        {needsQuery && (
                            <div className="batch-run__field">
                                <label className="batch-run__label">Question</label>
                                <textarea
                                    className="input-field"
                                    rows={3}
                                    placeholder="e.g. What were the biggest risk factors mentioned in the latest 10-K?"
                                    value={query}
                                    onChange={(e) => setQuery(e.target.value)}
                                />
                            </div>
                        )}

                        {needsCompare && (
                            <div className="batch-run__field">
                                <div className="batch-run__label-row">
                                    <label className="batch-run__label">Companies to compare</label>
                                    {selectedCompareTarget && (
                                        <span className="batch-run__sic-badge">
                                            {compareAnchorSic ? `SIC: ${compareAnchorSic}` : 'No SIC data — showing all portfolio companies'}
                                        </span>
                                    )}
                                </div>
                                {!selectedCompareTarget ? (
                                    <p className="batch-run__empty">Pick a portfolio below first.</p>
                                ) : (
                                    <div className="batch-run__compare-selects">
                                        <select
                                            className="input-field"
                                            value={compareCompany1}
                                            onChange={(e) => setCompareCompany1(e.target.value)}
                                        >
                                            <option value="">Company 1…</option>
                                            {compareCompanyOptions.map((c) => (
                                                <option key={c.id} value={c.name}>{c.name} ({c.ticker})</option>
                                            ))}
                                        </select>
                                        <select
                                            className="input-field"
                                            value={compareCompany2}
                                            onChange={(e) => setCompareCompany2(e.target.value)}
                                        >
                                            <option value="">Company 2…</option>
                                            {compareCompanyOptions.map((c) => (
                                                <option key={c.id} value={c.name}>{c.name} ({c.ticker})</option>
                                            ))}
                                        </select>
                                        <select
                                            className="input-field"
                                            value={compareCompany3}
                                            onChange={(e) => setCompareCompany3(e.target.value)}
                                        >
                                            <option value="">Company 3 (optional)…</option>
                                            {compareCompanyOptions.map((c) => (
                                                <option key={c.id} value={c.name}>{c.name} ({c.ticker})</option>
                                            ))}
                                        </select>
                                        <select
                                            className="input-field"
                                            value={compareYear}
                                            onChange={(e) => setCompareYear(Number(e.target.value))}
                                        >
                                            {Array.from({ length: 10 }, (_, i) => 2016 + i).map((y) => (
                                                <option key={y} value={y}>{y}</option>
                                            ))}
                                        </select>
                                    </div>
                                )}
                            </div>
                        )}

                        <div className="batch-run__field">
                            <div className="batch-run__label-row">
                                <label className="batch-run__label">
                                    {needsCompare ? 'Portfolio (pick one)' : `Portfolios (${selectedIds.size} selected)`}
                                </label>
                                {!needsCompare && (
                                    <button type="button" className="batch-run__select-all" onClick={toggleSelectAll}>
                                        {selectedIds.size === portfolios.length ? 'Clear all' : 'Select all'}
                                    </button>
                                )}
                            </div>
                            <div className="batch-run__portfolio-list">
                                {portfolios.length === 0 && (
                                    <p className="batch-run__empty">No portfolios available yet.</p>
                                )}
                                {portfolios.map((p) => (
                                    <label key={p.id} className="batch-run__portfolio-row">
                                        <input
                                            type={needsCompare ? 'radio' : 'checkbox'}
                                            name={needsCompare ? 'batch-run-compare-portfolio' : undefined}
                                            checked={selectedIds.has(p.id)}
                                            onChange={() => toggleSelected(p.id)}
                                        />
                                        <span className="batch-run__portfolio-name">{p.name}</span>
                                        {agent === 'alpha' && (
                                            <span className="batch-run__portfolio-meta">
                                                {p.tickers.length > 0 ? `${p.tickers.length} companies` : 'No companies'}
                                            </span>
                                        )}
                                    </label>
                                ))}
                            </div>
                        </div>

                        <div className="batch-run__field">
                            <label className="batch-run__checkbox-row">
                                <input
                                    type="checkbox"
                                    checked={scheduleEnabled}
                                    onChange={(e) => setScheduleEnabled(e.target.checked)}
                                />
                                Schedule for later (e.g. run overnight)
                            </label>
                            {scheduleEnabled && (
                                <input
                                    type="datetime-local"
                                    className="input-field batch-run__schedule-input"
                                    value={scheduleAt}
                                    onChange={(e) => setScheduleAt(e.target.value)}
                                />
                            )}
                        </div>

                        <div className="batch-run__actions">
                            <Button variant="outline" onClick={handleClose} disabled={submitting}>Cancel</Button>
                            <Button variant="primary" onClick={handleSubmit} disabled={!canSubmit} loading={submitting}>
                                {scheduleEnabled && scheduleAt ? 'Schedule Batch' : 'Run Batch Now'}
                            </Button>
                        </div>
                    </>
                )}
            </div>
        </Modal>
    );
};

export default BatchRunModal;
