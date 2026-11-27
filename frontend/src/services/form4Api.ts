// Form 4 Insider Trading API Service

import { getAuthHeader } from './authHeader';
import { API_BASE } from './http';

const API_BASE_URL = API_BASE;

export interface Form4IngestRequest {
    ticker: string;
    start_date?: string; // YYYY-MM-DD format
}

export interface Form4IngestResponse {
    ticker: string;
    total_url_fetched: number;
    forms_with_common_stock: number;
    forms_with_0_common_stock: number;
    transactions_saved_total: number;
    skipped_already_in_db: number;
    failed: number;
    date_range: { start: string; end: string };
    message?: string;
}

export interface Form4BatchIngestRequest {
    tickers: string[];
    start_date?: string; // YYYY-MM-DD format, applied to every ticker
}

export interface Form4BatchIngestResponse {
    tickers: string[]; // normalized, de-duplicated tickers actually ingested
    results: Form4IngestResponse[]; // per-ticker breakdown, same order as `tickers`
    totals: {
        total_url_fetched: number;
        forms_with_common_stock: number;
        forms_with_0_common_stock: number;
        transactions_saved_total: number;
        skipped_already_in_db: number;
        failed: number;
    };
    date_range: { start: string; end: string };
}

export interface Form4StreamRequest {
    tickers: string[];
    start_date?: string; // YYYY-MM-DD
    end_date?: string; // YYYY-MM-DD
}

/** Events emitted by POST /form4/ingest/stream (see the backend's /form4/ingest/stream docs). */
export type Form4StreamEvent =
    | { event: 'start'; data: { tickers: string[]; total: number } }
    | { event: 'ticker_start'; data: { ticker: string; index: number; count: number } }
    | { event: 'fetched'; data: { ticker: string; total: number } }
    | { event: 'progress'; data: { ticker: string; processed: number; total: number } }
    | { event: 'log'; data: { message: string } }
    | { event: 'ticker_completed'; data: { result: Form4IngestResponse } }
    | { event: 'completed'; data: Form4BatchIngestResponse }
    | { event: 'error'; data: { detail: string } };

export const form4Api = {
    /**
     * Ingest Form 4 data for one or more tickers, streamed as SSE (live progress + logs).
     */
    async *ingestStream(
        request: Form4StreamRequest,
        signal?: AbortSignal,
    ): AsyncGenerator<Form4StreamEvent, void, unknown> {
        const response = await fetch(`${API_BASE_URL}/form4/ingest/stream`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(request),
            signal,
        });

        if (!response.ok) {
            const errorBody = await response.json().catch(() => null);
            throw new Error(errorBody?.detail || `Request failed: ${response.status}`);
        }
        if (!response.body) {
            throw new Error('ReadableStream not supported by browser');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const messages = buffer.split('\n\n');
                buffer = messages.pop() || '';

                for (const message of messages) {
                    if (!message.startsWith('data: ')) continue;
                    try {
                        yield JSON.parse(message.slice(6)) as Form4StreamEvent;
                    } catch (e) {
                        console.error('Failed to parse Form 4 SSE event', e);
                    }
                }
            }
        } finally {
            reader.releaseLock();
        }
    },

    /**
     * Ingest Form 4 insider trading data from SEC EDGAR for a single ticker.
     */
    async ingest(request: Form4IngestRequest): Promise<Form4IngestResponse> {
        const response = await fetch(`${API_BASE_URL}/form4/ingest`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(request),
        });

        if (!response.ok) {
            const errorBody = await response.json().catch(() => null);
            const detail = errorBody?.detail || `Request failed: ${response.status}`;
            throw new Error(detail);
        }

        return response.json();
    },

    /**
     * Ingest Form 4 insider trading data from SEC EDGAR for multiple tickers in one call.
     */
    async ingestBatch(request: Form4BatchIngestRequest): Promise<Form4BatchIngestResponse> {
        const response = await fetch(`${API_BASE_URL}/form4/ingest/batch`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(request),
        });

        if (!response.ok) {
            const errorBody = await response.json().catch(() => null);
            const detail = errorBody?.detail || `Request failed: ${response.status}`;
            throw new Error(detail);
        }

        return response.json();
    },
};

export default form4Api;
