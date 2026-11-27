import React from 'react';
import type { AttentionAction, AttentionItem } from '../../services/analysisDashboardApi';
import { attentionKey } from './format';

const ACTION_LABEL: Record<AttentionAction['type'], string> = {
    run_alpha: 'Run ALPHA',
    ingest: 'Ingest filings',
    retry: 'Retry run',
    open_run: 'Open run',
    compare: 'See what changed',
};

const KIND_ICON: Record<AttentionItem['kind'], string> = {
    stance_downgrade: '▼',
    filing_after_analysis: '◆',
    insider_spike: '$',
    failed_run: '!',
    never_analysed: '○',
    stale_analysis: '◷',
    no_filings: '∅',
};

interface AttentionListProps {
    items: AttentionItem[];
    onAction: (item: AttentionItem) => void;
    busyKey?: string | null;
    showPortfolio?: boolean;
    limit?: number;
    emptyText?: string;
}

/** Ranked things that need the analyst: downgrades and new filings first, then gaps and failures. */
export const AttentionList: React.FC<AttentionListProps> = ({ items, onAction, busyKey, showPortfolio, limit = 8, emptyText }) => {
    const [expanded, setExpanded] = React.useState(false);
    if (items.length === 0) {
        return <p className="ad-empty ad-empty--ok">{emptyText ?? 'Nothing needs attention: every holding has a current analysis.'}</p>;
    }
    const shown = expanded ? items : items.slice(0, limit);
    return (
        <>
            <ul className="ad-attention">
                {shown.map((item) => {
                    const key = attentionKey(item);
                    const costs = item.action.type === 'run_alpha' || item.action.type === 'retry';
                    return (
                        <li key={key} className={`ad-attention__item ad-attention__item--${item.severity}`}>
                            <span className="ad-attention__icon" aria-hidden="true">{KIND_ICON[item.kind]}</span>
                            <div className="ad-attention__body">
                                <span className="ad-attention__msg">{item.message}</span>
                                {showPortfolio && item.portfolio_name && <span className="ad-attention__meta">{item.portfolio_name}</span>}
                            </div>
                            <button type="button" className="ad-btn ad-btn--sm" onClick={() => onAction(item)} disabled={busyKey === key}
                                title={costs ? 'Starts a model run (uses your model budget)' : undefined}>
                                {busyKey === key ? 'Starting…' : ACTION_LABEL[item.action.type]}
                            </button>
                        </li>
                    );
                })}
            </ul>
            {items.length > limit && (
                <button type="button" className="ad-link ad-attention__more" onClick={() => setExpanded((v) => !v)}>
                    {expanded ? 'Show fewer' : `Show all ${items.length}`}
                </button>
            )}
        </>
    );
};

export default AttentionList;
