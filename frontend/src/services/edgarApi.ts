import { getAuthHeader } from './authHeader';

import { API_BASE } from './http';

/** One filing's outcome in POST /edgar/ingest (backend app/services/sec_edgar.py). */
export interface EdgarFilingResult {
    form?: string;
    accession?: string;
    success?: boolean;
    pdf_path?: string;
    message?: string;
    [key: string]: unknown;
}

/** The server-sent events of POST /edgar/ingest, in the order they arrive. */
export type EdgarIngestEvent =
    | { event: 'start'; data: { total: number; ticker: string } }
    | { event: 'progress'; data: { file: string; accession: string; status: string; message?: string } }
    | { event: 'file_completed'; data: EdgarFilingResult }
    | { event: 'log'; data: { message: string } }
    | { event: 'error'; data: { detail?: string } }
    | { event: 'completed'; data: { results?: EdgarFilingResult[]; summary?: unknown } };


const EDGAR_BASE_URL = `${API_BASE}/edgar`;

export interface EdgarFiling {
    form: string;
    filing_date: string;
    period_end_date: string | null;
    url: string;
    pdf_path: string;
    accession: string;
}

export interface EdgarListResponse {
    filings: EdgarFiling[];
}

export const edgarApi = {
    /**
     * Fetch the list of available SEC filings for a ticker.
     */
    async listFilings(
        ticker: string,
        formTypes: string[],
        startDate?: string,
        endDate?: string
    ): Promise<EdgarListResponse> {
        const response = await fetch(`${EDGAR_BASE_URL}/list`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({
                ticker,
                form_types: formTypes.length > 0 ? formTypes : undefined,
                start_date: startDate || undefined,
                end_date: endDate || undefined,
            }),
        });

        if (!response.ok) {
            const err = await response.text();
            throw new Error(err || 'Failed to list SEC filings');
        }

        return response.json();
    },

    /**
     * Ingest SEC filings and return an SSE stream.
     */
    async *ingestFilingsStream(
        ticker: string,
        formTypes: string[],
        accessions: string[],
        startDate?: string,
        endDate?: string
    ): AsyncGenerator<EdgarIngestEvent, void, unknown> {
        const response = await fetch(`${EDGAR_BASE_URL}/ingest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({
                ticker,
                form_types: formTypes.length > 0 ? formTypes : undefined,
                accessions: accessions.length > 0 ? accessions : undefined,
                start_date: startDate || undefined,
                end_date: endDate || undefined,
            }),
        });

        if (!response.ok) {
            const err = await response.text();
            throw new Error(err || 'Failed to ingest SEC filings');
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
                const lines = buffer.split('\n\n');
                
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const event = JSON.parse(line.slice(6)) as EdgarIngestEvent;
                            yield event;
                        } catch (e) {
                            console.error('Failed to parse SSE event', e);
                        }
                    }
                }
            }
        } finally {
            reader.releaseLock();
        }
    }
};

export default edgarApi;
