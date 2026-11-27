// Document Inspector API. Types mirror the backend contract in
// docs/DOCUMENT_INSPECTOR_PLAN.md (§5.3, §6) one for one.

import { getAuthHeader } from '../../services/authHeader';

import { API_BASE } from '../../services/http';
export { API_BASE };      // the one definition lives in services/http.ts
const BASE = `${API_BASE}/documents`;

export type PdfStatus = 'local' | 'missing' | 'downloading' | 'failed' | 'unsupported';
export type SortKey = 'filing_date' | 'ticker' | 'ingested_at' | 'pages';
export type BBox = [number, number, number, number]; // PDF points, top-left origin

export interface DocumentSummary {
    doc_id: string;
    ticker: string | null;
    company: string | null;
    form_type: string | null;
    fiscal_year: number | null;
    fiscal_quarter: number | null;
    period_end_date: string | null;
    filing_date: string | null;
    accession: string | null;
    source_file: string;
    collection: string;
    page_count: number;
    text_pages: number;
    image_chunks: number;
    pdf_status: PdfStatus;
    ingested_at: string | null;
    synced_at: string;
    aligned: boolean | null;
}

export interface FacetCount { value: string | number; count: number }
export interface Facets { tickers: FacetCount[]; forms: FacetCount[]; years: FacetCount[] }

export interface DocumentListResponse {
    items: DocumentSummary[];
    total: number;
    next_cursor: string | null;
    facets: Facets;
}

export interface PdfStatusResponse { status: PdfStatus; error: string | null; aligned: boolean | null }

export interface PageSummary { page: number; chars: number; has_tables: boolean; has_images: boolean }
export interface PageIndexResponse { doc_id: string; page_count: number; pages: PageSummary[] }

export interface PageTable {
    markdown: string;
    headers: string[] | null; // structured cells (set when rebuilt from the PDF)
    rows: string[][] | null;
    bbox: BBox | null;
    verified: { numbers: number; matched: number } | null; // numbers printed in the table vs found in the extraction
    source: 'pdf' | 'index';
}
export interface PageImage {
    image_id: string | null;
    type: string | null;
    summary: string | null;
    analysis: string;
    bbox: BBox | null;
}
export interface PageBlock { id: string; bbox: BBox; text: string }
export interface PageResponse {
    doc_id: string;
    page: number;
    page_count: number;
    size: { width: number; height: number } | null;
    text: string;
    tables: PageTable[];
    images: PageImage[];
    blocks: PageBlock[];
    chunks: { point_id: string; content_type: string | null }[];
    meta: {
        ticker: string | null;
        form_type: string | null;
        fiscal_year: number | null;
        ingested_at: string | null;
        pdf_available: boolean;
        aligned: boolean | null;
    };
}

export interface LocateResponse {
    method: 'exact' | 'fuzzy' | 'no_text_layer' | 'not_found' | 'no_pdf';
    rects: BBox[];
    score: number;
}

export interface ListQuery {
    ticker?: string[];
    form?: string[];
    year_from?: number;
    year_to?: number;
    quarter?: number;
    q?: string;
    pdf_status?: PdfStatus;
    has_images?: boolean;
    sort?: SortKey;
    order?: 'asc' | 'desc';
    cursor?: string;
    limit?: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${BASE}${path}`, {
        ...init,
        headers: { ...getAuthHeader(), ...(init?.headers || {}) },
    });
    if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try {
            const body = await res.json();
            detail = typeof body.detail === 'string' ? body.detail : body.detail?.message || detail;
        } catch { /* non-JSON error body */ }
        throw new Error(detail);
    }
    return res.json() as Promise<T>;
}

export function toSearchParams(q: ListQuery): URLSearchParams {
    const p = new URLSearchParams();
    q.ticker?.forEach(v => p.append('ticker', v));
    q.form?.forEach(v => p.append('form', v));
    (['year_from', 'year_to', 'quarter', 'q', 'pdf_status', 'has_images', 'sort', 'order', 'cursor', 'limit'] as const)
        .forEach(k => { if (q[k] !== undefined && q[k] !== '') p.set(k, String(q[k])); });
    return p;
}

// Small promise caches so hovering a citation can warm what the preview needs before it is clicked.
const docCache = new Map<string, Promise<DocumentSummary>>();
const locateCache = new Map<string, Promise<LocateResponse>>();
const cached = <T>(cache: Map<string, Promise<T>>, key: string, load: () => Promise<T>) => {
    let p = cache.get(key);
    if (!p) {
        p = load();
        cache.set(key, p);
        p.catch(() => cache.delete(key)); // never cache a failure
    }
    return p;
};

export const documentsApi = {
    list: (q: ListQuery, signal?: AbortSignal) =>
        request<DocumentListResponse>(`?${toSearchParams(q)}`, { signal }),
    sync: () => request<{ documents: number; added: number; removed: number }>('/sync', { method: 'POST' }),
    get: (id: string) => cached(docCache, id, () => request<DocumentSummary>(`/${encodeURIComponent(id)}`)),
    /** Drop the cached summary (after a PDF download changes its status). */
    forget: (id: string) => { docCache.delete(id); },
    ensurePdf: (id: string) => request<PdfStatusResponse>(`/${encodeURIComponent(id)}/ensure-pdf`, { method: 'POST' }),
    pdfStatus: (id: string) => request<PdfStatusResponse>(`/${encodeURIComponent(id)}/pdf-status`),
    pageIndex: (id: string) => request<PageIndexResponse>(`/${encodeURIComponent(id)}/pages`),
    page: (id: string, n: number, signal?: AbortSignal) =>
        request<PageResponse>(`/${encodeURIComponent(id)}/pages/${n}`, { signal }),
    locate: (id: string, n: number, q: string) => cached(locateCache, `${id}:${n}:${q}`, () =>
        request<LocateResponse>(`/${encodeURIComponent(id)}/pages/${n}/locate?${new URLSearchParams({ q })}`)),
    pdfUrl: (id: string) => `${BASE}/${encodeURIComponent(id)}/pdf`,
};
