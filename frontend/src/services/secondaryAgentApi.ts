// Secondary Research Agent API Service

import { getAuthHeader } from './authHeader';
import { analysisTasksApi, type AnalysisTask } from './analysisTasksApi';
import { streamPost } from './http';
import { API_BASE } from './http';

const SECONDARY_AGENT_BASE_URL = API_BASE;

export interface SecondaryAskRequest {
    query: string;
    thread_id: string; // Now required for portfolio context
    /** One of the user's own analysis frameworks (Settings > Analysis modules). */
    framework?: string;
    /** Search only these uploaded documents (default: all the user may read). */
    document_ids?: string[];
    /** The user's answer to a clarification question the previous call returned. */
    clarification?: string;
    /** Per-question settings that win over the saved ones, same shape as /settings. */
    settings_override?: Record<string, Record<string, unknown>>;
}

export interface Verification {
    status: 'verified' | 'issues' | 'nothing_to_check';
    checked: number;
    supported: number;
    issues: { text: string; kind: string; detail: string }[];
    mode?: string;
}

export interface UsageInfo {
    total_tokens: number;
    cost?: { estimated_cost_usd: number };
}

/** Events of POST /ask/stream: a finished step, answer text as it is written, then the complete response. */
export type AskStreamEvent =
    | { type: 'step'; node: string }
    | { type: 'token'; text: string }
    | { type: 'done'; result: SecondaryAskResponse }
    | { type: 'error'; status: number; detail: string };

export interface DocumentMetadata {
    source?: string;
    source_file?: string;
    page_num?: number;
    company?: string;
    content_type?: string;
    filing_type?: string;
    period_end_date?: string;
    year?: number;
    [key: string]: unknown;
}

export interface Document {
    content: string;
    metadata: DocumentMetadata;
}

export interface CitationInfo {
    source_type: string;
    document_id: string;
    relevance_score: number;
    key_information: string;
}

export interface Message {
    type: string;
    content: string;
}

export interface SecondaryAskResponse {
    answer: string;
    thread_id: string;
    portfolio_id: number;
    portfolio_name: string;
    company_filter: string[];
    ticker: string | null;
    chart_url?: string | null;
    chart_filename?: string | null;
    messages: Message[];
    intermediate_message: string;
    documents: Document[];
    vectorstore_searched: boolean;
    web_searched: boolean;
    vectorstore_quality: string;
    needs_web_fallback: boolean;
    retry_count: number;
    tool_calls: string[];
    document_sources: Record<string, string[]>;
    citation_info: CitationInfo[];
    summary_strategy: string;
    sub_query_analysis: Record<string, unknown>;
    sub_query_results: Record<string, unknown>;
    /** Set when the pipeline paused to ask which company/period you mean; answer it by sending `clarification`. */
    clarification_needed?: boolean;
    question?: string;
    verification?: Verification;
    usage?: UsageInfo;
}

export interface CompareRequest {
    company1: string;
    company2: string;
    company3?: string;  // Optional third company
    user_id: string;    // Required for comparison persistence
    thread_id?: string;
    year?: number;  // Year for comparison
}

export interface CompareResponse {
    answer: string;
    thread_id: string;
    company1: string;
    company2: string;
    company3?: string;
    company_filter: string[];
    chart_url: string | null;
    chart_filename: string | null;
    messages: Message[];
    intermediate_message: string;
    documents: Document[];
    vectorstore_searched: boolean;
    web_searched: boolean;
    vectorstore_quality: string;
    needs_web_fallback: boolean;
    retry_count: number;
    tool_calls: string[];
    document_sources: Record<string, string[]>;
    citation_info: CitationInfo[];
    summary_strategy: string;
    sub_query_analysis: Record<string, unknown>;
    sub_query_results: Record<string, unknown>;
}

export interface AlphaRequest {
    tickers: string[];  // Ticker symbols only — no free-text query
    user_id: string;
    thread_id: string;
}

export interface AlphaResult {
    ticker: string;
    report: string;
}

export interface AlphaResponse {
    thread_id: string;
    portfolio_id: number;
    portfolio_name: string;
    tickers: string[];
    results: AlphaResult[];
}

// API timeout in milliseconds (5 minutes)
const API_TIMEOUT_MS = 5 * 60 * 1000;

function unwrapResponse<T>(task: AnalysisTask): T {
    if (task.status === 'FAILED' || task.status === 'CANCELLED') {
        throw new Error(task.progress_message || 'Analysis run failed');
    }
    return (task.result_metadata as { response: T }).response;
}

export const secondaryAgentApi = {
    /**
     * Send a query to the Secondary Research Agent
     */
    async ask(request: SecondaryAskRequest): Promise<SecondaryAskResponse> {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

        try {
            const response = await fetch(`${SECONDARY_AGENT_BASE_URL}/ask`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify(request),
                signal: controller.signal,
            });

            if (!response.ok) {
                throw new Error(`API request failed: ${response.status} ${response.statusText}`);
            }

            return response.json();
        } catch (error) {
            if (error instanceof Error && error.name === 'AbortError') {
                throw new Error('Request timed out after 5 minutes');
            }
            throw error;
        } finally {
            clearTimeout(timeoutId);
        }
    },

    /**
     * Compare two companies using the Secondary Research Agent
     */
    async compare(request: CompareRequest): Promise<CompareResponse> {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

        try {
            const response = await fetch(`${SECONDARY_AGENT_BASE_URL}/compare`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify(request),
                signal: controller.signal,
            });

            if (!response.ok) {
                throw new Error(`Compare API request failed: ${response.status} ${response.statusText}`);
            }

            return response.json();
        } catch (error) {
            if (error instanceof Error && error.name === 'AbortError') {
                throw new Error('Comparison request timed out after 5 minutes');
            }
            throw error;
        } finally {
            clearTimeout(timeoutId);
        }
    },

    /**
     * Queue-backed equivalent of ask() — same request/response shape, but
     * runs through the background job system (interactive lane) instead of
     * blocking the request directly. Every call shows up in the portfolio's
     * analysis-run history, not just the chat transcript.
     */
    /**
     * Answer a question directly and stream its progress (steps and text) instead of going through the background queue.
     * Resolves with the complete response; a server error rejects with its message.
     */
    async askStream(request: SecondaryAskRequest, onEvent: (e: AskStreamEvent) => void, signal?: AbortSignal): Promise<SecondaryAskResponse> {
        let result: SecondaryAskResponse | null = null;
        let failure: string | null = null;
        await streamPost<AskStreamEvent>('/ask/stream', request, e => {
            if (e.type === 'done') result = e.result;
            else if (e.type === 'error') failure = e.detail;
            onEvent(e);
        }, signal);
        if (failure) throw new Error(failure);
        if (!result) throw new Error('The answer stream ended without a result');
        return result;
    },

    async askViaQueue(request: SecondaryAskRequest): Promise<SecondaryAskResponse> {
        const task = await analysisTasksApi.enqueueAsk(request);
        const done = await analysisTasksApi.pollUntilDone(task.id);
        return unwrapResponse<SecondaryAskResponse>(done);
    },

    /**
     * Queue-backed equivalent of compare().
     */
    async compareViaQueue(request: CompareRequest, onProgress?: (task: AnalysisTask) => void): Promise<CompareResponse> {
        const task = await analysisTasksApi.enqueueCompare(request);
        const done = await analysisTasksApi.pollUntilDone(task.id, { onProgress });
        return unwrapResponse<CompareResponse>(done);
    },

    /**
     * Queue-backed equivalent of alpha() — runs on the batch lane (this is
     * exactly the long multi-ticker job the background system was built
     * for). onProgress fires with messages like "3/8 tickers done (MSFT)"
     * as each ticker completes, for the caller to show inline.
     */
    async alphaViaQueue(
        request: AlphaRequest,
        onProgress?: (message: string, task: AnalysisTask) => void
    ): Promise<AlphaResponse> {
        const task = await analysisTasksApi.enqueueAlpha(request);
        const done = await analysisTasksApi.pollUntilDone(task.id, {
            timeoutMs: 30 * 60 * 1000, // ALPHA runs multiple full agent passes per ticker
            onProgress: (t) => onProgress?.(t.progress_message ?? '', t),
        });
        return unwrapResponse<AlphaResponse>(done);
    },

    /**
     * Run the ALPHA framework for a list of ticker symbols (no free-text query)
     */
    async alpha(request: AlphaRequest): Promise<AlphaResponse> {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

        try {
            const response = await fetch(`${SECONDARY_AGENT_BASE_URL}/alpha`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify(request),
                signal: controller.signal,
            });

            if (!response.ok) {
                throw new Error(`ALPHA API request failed: ${response.status} ${response.statusText}`);
            }

            return response.json();
        } catch (error) {
            if (error instanceof Error && error.name === 'AbortError') {
                throw new Error('ALPHA request timed out after 5 minutes');
            }
            throw error;
        } finally {
            clearTimeout(timeoutId);
        }
    }
};

export default secondaryAgentApi;
