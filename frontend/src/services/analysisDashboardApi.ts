// Dashboard reads (portfolio pulse, cross-portfolio overview, a ticker's thesis timeline) and the opt-in re-run schedules.
// Backend: app/api/analysis_dashboard.py, app/services/portfolio_pulse.py.
import { apiRequest } from './http';
import type { PillarDirection, Stance } from './analysisTasksApi';

export interface TickerSignal {
    task_id: string;
    ticker: string;
    stance: Stance | null;
    stance_score: number | null;
    pillars: Partial<Record<PillarKey, PillarDirection>>;
    fact_counts: { pos?: number; neg?: number; neutral?: number };
    key_risk: string | null;
    portfolio_id: number | null;
    created_at: string | null;
    /** Per-pillar verdicts of runs made with the structured ALPHA flow (empty for older runs). */
    detail?: SignalDetail;
}

export interface PillarDetail {
    status: 'ok' | 'failed' | 'no_data' | null;
    score: number | null;
    recommendation: string | null;
    source?: 'model' | 'market_data' | null;
    issues?: number;
    revised?: boolean;
}

export interface SignalDetail {
    pillars?: Partial<Record<PillarKey, PillarDetail>>;
    partial?: boolean;
    missing?: PillarKey[];
    mean_score?: number | null;
    fact_issues?: number;
    company?: string;
}

export type PillarKey = 'alignment' | 'liquidity' | 'performance' | 'horizon' | 'action';

export interface PulseTickerRow {
    ticker: string;
    latest: TickerSignal | null;
    previous: TickerSignal | null;
    runs: number;
    last_analysed_at: string | null;
    alpha_age_days: number | null;
    stale: boolean;
    filings: number;
    latest_filing: { form_type: string | null; filing_date: string | null; ingested_at: string | null } | null;
    insider: { net_usd: number; trades: number; since: string };
}

export type AttentionAction =
    | { type: 'run_alpha'; ticker: string }
    | { type: 'ingest'; ticker: string }
    | { type: 'retry'; task_id: string }
    | { type: 'open_run'; task_id: string }
    | { type: 'compare'; ticker: string; a: string; b: string };

export interface AttentionItem {
    kind: 'no_filings' | 'never_analysed' | 'stance_downgrade' | 'filing_after_analysis' | 'stale_analysis' | 'insider_spike' | 'failed_run';
    ticker: string | null;
    severity: 'high' | 'medium' | 'low';
    message: string;
    action: AttentionAction;
    portfolio_id?: number;
    portfolio_name?: string;
}

export interface PulseKpis {
    holdings: number;
    covered: number;
    coverage_pct: number;
    last_alpha_at: string | null;
    stance_mix: Partial<Record<Stance, number>>;
    spend_month_usd: number;
    tokens_month: number;
    running: number;
    unreviewed: number;
    failed_7d: number;
    runs_total: number;
}

export interface PortfolioPulse {
    portfolio: { id: number; name: string; tickers: string[]; description: string | null };
    stale_days: number;
    tickers: PulseTickerRow[];
    attention: AttentionItem[];
    kpis: PulseKpis;
}

export interface OverviewCard extends PulseKpis {
    id: number;
    name: string;
    tickers: string[];
    description: string | null;
    last_activity_at: string | null;
    attention: number;
    high_alerts: number;
}

export interface Mover { ticker: string; from: Stance; to: Stance; change: number; a: string; b: string; at: string }

export interface DashboardOverview {
    portfolios: OverviewCard[];
    conviction: Partial<Record<Stance, number>>;
    movers: Mover[];
    alerts: AttentionItem[];
    inbox: number;
    totals: { running: number; unreviewed: number; failed_7d: number; spend_month_usd: number; tokens_month: number; holdings: number; covered: number };
}

export interface Timeline {
    ticker: string;
    signals: TickerSignal[];
    filings: { form_type: string | null; filing_date: string | null; fiscal_year: number | null; fiscal_quarter: number | null }[];
    insider_monthly: { month: string; net_usd: number }[];
}

export type ScheduleTrigger = 'daily' | 'weekly' | 'monthly' | 'new_filing';

export interface Schedule {
    id: number;
    portfolio_id: number;
    task_type: string;
    tickers: string[] | null;
    trigger: ScheduleTrigger;
    hour_utc: number;
    weekday: number | null;
    enabled: boolean;
    next_run_at: string | null;
    last_run_at: string | null;
    last_task_id: string | null;
    last_skip_reason: string | null;
    created_at: string | null;
}

export interface DashboardConfig { schedules_allowed: boolean; stale_days: number; insider_alert_usd: number }

export const analysisDashboardApi = {
    config: (): Promise<DashboardConfig> => apiRequest('/analysis/config'),
    pulse: (portfolioId: number): Promise<PortfolioPulse> => apiRequest(`/analysis/portfolios/${portfolioId}/pulse`),
    overview: (): Promise<DashboardOverview> => apiRequest('/analysis/overview'),
    timeline: (ticker: string, portfolioId?: number): Promise<Timeline> =>
        apiRequest(`/analysis/timeline/${encodeURIComponent(ticker)}${portfolioId != null ? `?portfolio_id=${portfolioId}` : ''}`),
    schedules: (portfolioId: number): Promise<Schedule[]> => apiRequest(`/analysis/portfolios/${portfolioId}/schedules`),
    createSchedule: (portfolioId: number, body: Partial<Schedule>): Promise<Schedule> =>
        apiRequest(`/analysis/portfolios/${portfolioId}/schedules`, { method: 'POST', body: JSON.stringify(body) }),
    updateSchedule: (id: number, body: Partial<Schedule>): Promise<Schedule> =>
        apiRequest(`/analysis/schedules/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    deleteSchedule: (id: number): Promise<void> => apiRequest(`/analysis/schedules/${id}`, { method: 'DELETE' }),
};

export default analysisDashboardApi;
