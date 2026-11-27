// Shared labels and formatting for the analysis dashboards.
import type { AnalysisTask, PillarDirection, Stance, TaskStatus } from '../../services/analysisTasksApi';
import type { AttentionItem, PillarKey } from '../../services/analysisDashboardApi';

export const STATUS_META: Record<TaskStatus, { label: string; className: string }> = {
    PENDING: { label: 'Queued', className: 'run-pill--pending' },
    RUNNING: { label: 'Running', className: 'run-pill--running' },
    COMPLETED: { label: 'Ready', className: 'run-pill--completed' },
    FAILED: { label: 'Failed', className: 'run-pill--failed' },
    CANCELLED: { label: 'Cancelled', className: 'run-pill--cancelled' },
};

export const TASK_LABELS: Record<string, string> = {
    ask: 'Research question',
    compare: 'Comparison',
    alpha: 'ALPHA',
    query: 'Quant',
};

export const PILLARS: { key: PillarKey; letter: string; label: string }[] = [
    { key: 'alignment', letter: 'A', label: 'Alignment' },
    { key: 'liquidity', letter: 'L', label: 'Liquidity' },
    { key: 'performance', letter: 'P', label: 'Performance' },
    { key: 'horizon', letter: 'H', label: 'Horizon' },
    { key: 'action', letter: 'A', label: 'Action' },
];

export const STANCE_ORDER: Stance[] = ['Bullish', 'Cautiously Bullish', 'Neutral', 'Cautiously Bearish', 'Bearish'];

export function stanceTone(stance: Stance | null | undefined): 'pos' | 'pos-soft' | 'neutral' | 'neg-soft' | 'neg' | 'none' {
    switch (stance) {
        case 'Bullish': return 'pos';
        case 'Cautiously Bullish': return 'pos-soft';
        case 'Neutral': return 'neutral';
        case 'Cautiously Bearish': return 'neg-soft';
        case 'Bearish': return 'neg';
        default: return 'none';
    }
}

export const DIRECTION_LABEL: Record<Exclude<PillarDirection, null>, string> = {
    positive: 'Supportive',
    mixed: 'Mixed',
    negative: 'Weak',
};

export function directionSymbol(d: PillarDirection): string {
    return d === 'positive' ? '+' : d === 'negative' ? '−' : d === 'mixed' ? '±' : '·';
}

export function relativeTime(iso: string | null | undefined): string {
    if (!iso) return '—';
    const diffMs = Date.now() - new Date(iso).getTime();
    if (Number.isNaN(diffMs)) return '—';
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    if (days < 60) return `${days}d ago`;
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function shortDate(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function dateTime(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function duration(ms: number | null | undefined): string {
    if (ms == null) return '—';
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function money(usd: number | null | undefined): string {
    if (usd == null) return '—';
    if (usd === 0) return '$0';
    if (usd < 0.01) return '<$0.01';
    return `$${usd.toFixed(usd < 10 ? 2 : 0)}`;
}

export function compactUsd(usd: number): string {
    const a = Math.abs(usd);
    const sign = usd < 0 ? '−' : '+';
    if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(1)}B`;
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(0)}K`;
    return `${sign}$${a.toFixed(0)}`;
}

export function runTitle(task: AnalysisTask): string {
    return task.title || TASK_LABELS[task.task_type] || task.task_type;
}

export function tabForTask(task: Pick<AnalysisTask, 'agent_type'>): 'Secondary Research' | 'Quantitative Research' {
    return task.agent_type === 'quant' ? 'Quantitative Research' : 'Secondary Research';
}

export function dateGroupFor(iso: string | null): string {
    if (!iso) return 'Undated';
    const d = new Date(iso);
    const now = new Date();
    const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diffDays = Math.round((startOf(now) - startOf(d)) / 86400000);
    if (diffDays <= 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return 'This week';
    if (diffDays < 31) return 'This month';
    return 'Earlier';
}

export function attentionKey(item: AttentionItem): string {
    return `${item.portfolio_id ?? ''}:${item.kind}:${item.ticker ?? ''}:${'task_id' in item.action ? item.action.task_id : ''}`;
}

export const ERROR_HINTS: Record<string, string> = {
    timeout: 'The run took too long. Retrying usually works; for ALPHA, try fewer tickers.',
    provider: 'The model provider refused the request (key, quota or rate limit). Check Settings → AI providers, then retry.',
    no_documents: 'No filings or documents matched. Ingest filings for these companies, then retry.',
    cancelled: 'This run was cancelled.',
    other: 'Something went wrong during the run. Retrying may help.',
};
