import { getAuthHeader } from './authHeader';
import { API_BASE } from './http';

const BASE = `${API_BASE}/reports`;

// ── Types ───────────────────────────────────────────────────────────────────

export interface DraftItem {
    id: number;
    user_id: string;
    portfolio_id: number | null;
    item_type: 'text' | 'image' | 'summary';
    content: string | null;
    html: string | null;
    image_url: string | null;
    source: 'rag' | 'quant' | 'summary' | null;
    session_id: string | null;
    label: string | null;
    sort_order: number;
    created_at: string;
}

export interface AddDraftItemPayload {
    user_id: string;
    portfolio_id?: number;
    item_type: 'text' | 'image' | 'summary';
    content?: string;
    html?: string;
    image_url?: string;
    source?: 'rag' | 'quant' | 'summary';
    session_id?: string;
    label?: string;
    sort_order?: number;
}

export interface ReportResponse {
    id: number;
    user_id: string;
    author_name: string;
    company_name: string;
    ticker: string | null;
    content_markdown: string | null;
    content_html: string | null;
    image_urls: string[];
    source_session_ids: string[];
    portfolio_id: number | null;
    status: 'draft' | 'published';
    created_at: string;
    updated_at: string;
}

export interface ReportListResponse {
    total: number;
    page: number;
    page_size: number;
    items: ReportResponse[];
}

export interface CreateReportFromDraftPayload {
    company_name: string;
    ticker?: string;
    portfolio_id?: number;
    source_session_ids?: string[];
    clear_draft?: boolean;
}

export interface RecentReportItem {
    id: number;
    company_name: string;
    ticker: string | null;
    author: string;
    created_at: string;
    updated_at: string | null;
}

export interface RepositoryStats {
    total_published: number;
    top_companies: { company: string; count: number }[];
    top_analysts: { analyst: string; count: number }[];
    recent_reports: RecentReportItem[];
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function extractDetail(detail: unknown): string {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail
            .map((e: { loc?: string[]; msg?: string }) => {
                const field = e.loc ? e.loc.filter(l => l !== 'body').join(' → ') : '';
                return field ? `${field}: ${e.msg}` : (e.msg ?? 'Invalid input');
            })
            .join('; ');
    }
    return 'Request failed';
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const res = await fetch(`${BASE}${path}`, {
        headers: { 'Content-Type': 'application/json', ...getAuthHeader(), ...options.headers },
        ...options,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(extractDetail(err.detail) || res.statusText || 'Request failed');
    }
    return res.json();
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
        if (v != null && v !== '') p.append(k, String(v));
    }
    const s = p.toString();
    return s ? `?${s}` : '';
}

// ── Draft clipboard ───────────────────────────────────────────────────────────

export const reportApi = {
    // Clipboard
    addDraftItem: (payload: AddDraftItemPayload) =>
        request<DraftItem>('/draft/items', { method: 'POST', body: JSON.stringify(payload) }),

    getDraftItems: (userId: string, portfolioId?: number) =>
        request<DraftItem[]>(`/draft/items/${encodeURIComponent(userId)}${qs({ portfolio_id: portfolioId })}`),

    updateDraftItem: (itemId: number, userId: string, patch: { label?: string; content?: string; sort_order?: number }) =>
        request<DraftItem>(`/draft/items/${itemId}${qs({ user_id: userId })}`, {
            method: 'PUT', body: JSON.stringify(patch),
        }),

    reorderDraftItems: (userId: string, orderedIds: number[], portfolioId?: number) =>
        request<DraftItem[]>(`/draft/items/reorder${qs({ user_id: userId, portfolio_id: portfolioId })}`, {
            method: 'POST', body: JSON.stringify({ ordered_ids: orderedIds }),
        }),

    deleteDraftItem: (itemId: number, userId: string) =>
        request<{ message: string; item_id: number }>(`/draft/items/${itemId}${qs({ user_id: userId })}`, {
            method: 'DELETE',
        }),

    clearDraftItems: (userId: string, portfolioId?: number) =>
        request<{ message: string; deleted: number }>(`/draft/items/user/${encodeURIComponent(userId)}${qs({ portfolio_id: portfolioId })}`, {
            method: 'DELETE',
        }),

    // Create report directly with assembled content (used by Creation tab)
    createReport: (payload: {
        user_id: string;
        company_name: string;
        ticker?: string;
        content_markdown?: string;
        content_html?: string;
        image_urls?: string[];
        source_session_ids?: string[];
        portfolio_id?: number;
    }) => request<ReportResponse>('', { method: 'POST', body: JSON.stringify(payload) }),

    // Update an existing report (used when resuming a draft)
    updateReport: (reportId: number, userId: string, patch: {
        company_name?: string;
        ticker?: string;
        content_markdown?: string;
        content_html?: string;
        image_urls?: string[];
        source_session_ids?: string[];
    }) => request<ReportResponse>(`/${reportId}${qs({ user_id: userId })}`, {
        method: 'PUT', body: JSON.stringify(patch),
    }),

    // Create report from staged clipboard items
    createFromDraft: (userId: string, payload: CreateReportFromDraftPayload) =>
        request<ReportResponse>(`/from-draft/${encodeURIComponent(userId)}`, {
            method: 'POST', body: JSON.stringify(payload),
        }),

    // Report CRUD
    getReport: (reportId: number) =>
        request<ReportResponse>(`/${reportId}`),

    getUserReports: (userId: string, status?: string, portfolioId?: string | number) =>
        request<ReportListResponse>(`/user/${encodeURIComponent(userId)}${qs({ status, portfolio_id: portfolioId })}`),

    publishReport: (reportId: number, userId: string) =>
        request<ReportResponse>(`/${reportId}/publish${qs({ user_id: userId })}`, { method: 'POST' }),

    unpublishReport: (reportId: number, userId: string) =>
        request<ReportResponse>(`/${reportId}/unpublish${qs({ user_id: userId })}`, { method: 'POST' }),

    deleteReport: (reportId: number, userId: string) =>
        request<{ message: string }>(`/${reportId}${qs({ user_id: userId })}`, { method: 'DELETE' }),

    // Repository (Fund Manager)
    getRepositoryStats: () =>
        request<RepositoryStats>('/repository/stats'),

    listPublished: (params: {
        company?: string; ticker?: string;
        author?: string; from_date?: string; to_date?: string;
        page?: number; page_size?: number;
    } = {}) =>
        request<ReportListResponse>(`${qs(params)}`),

    searchReports: (q: string, params: {
        company?: string; ticker?: string;
        author?: string; from_date?: string; to_date?: string;
        page?: number; page_size?: number;
    } = {}) =>
        request<ReportListResponse>(`/search${qs({ q, ...params })}`),

    // PDF export/view. This endpoint requires a Bearer token like every other
    // route, but a plain URL string handed to <a href>/<iframe src> can't
    // carry a custom header — the browser navigates directly with no auth,
    // so it would 401. Instead, fetch the PDF ourselves (with the header)
    // and hand back a blob: object URL, which downloads/renders identically
    // but doesn't need the browser to attach anything.
    async fetchPdfBlobUrl(reportId: number, inline: boolean): Promise<string> {
        const path = inline ? `/${reportId}/export/pdf?inline=true` : `/${reportId}/export/pdf?t=${Date.now()}`;
        const res = await fetch(`${BASE}${path}`, {
            headers: { ...getAuthHeader() },
        });
        if (!res.ok) {
            throw new Error(`Failed to load PDF: ${res.status} ${res.statusText}`);
        }
        const blob = await res.blob();
        return URL.createObjectURL(blob);
    },

    // Triggers a browser download of the report PDF. Caller does not need to
    // manage the resulting blob URL — it's revoked automatically once the
    // download has been handed off to the browser.
    async downloadPdf(reportId: number, filename?: string): Promise<void> {
        const url = await reportApi.fetchPdfBlobUrl(reportId, false);
        try {
            const a = document.createElement('a');
            a.href = url;
            a.download = filename || `report-${reportId}.pdf`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);
        }
    },
};
