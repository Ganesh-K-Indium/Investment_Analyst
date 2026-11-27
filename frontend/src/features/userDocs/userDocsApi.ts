// User documents API (backend: app/api/user_documents.py)
import { apiRequest, fetchBlob } from '../../services/http';

export type DocStatus = 'queued' | 'processing' | 'awaiting_approval' | 'needs_review' | 'ready' | 'failed';
export type Visibility = 'private' | 'workspace';
export type Reading = 'economy' | 'balanced' | 'accuracy';

export interface UserDoc {
    id: string;
    filename: string;
    ext: string;
    size_bytes: number;
    doc_type: string | null;
    doc_type_label: string | null;
    label: string | null;
    entity: string | null;
    period: string | null;
    tags: string[];
    visibility: Visibility;
    status: DocStatus;
    stage: string | null;
    progress: { done?: number; total?: number };
    error: string | null;
    pages: number;
    pages_indexed: number;
    review_pending: number;
    approved: boolean;
    index_policy: string;
    stats: Record<string, number>;
    reading: Record<string, unknown>;
    has_pdf: boolean;
    owner: boolean;
    created_at: string | null;
    updated_at: string | null;
}

export interface UploadResult {
    filename: string;
    ok: boolean;
    error?: string;
    duplicate_of?: string;
    document?: UserDoc;
}

export interface DocTypes {
    types: { id: string; label: string }[];
    formats: string[];
    native_formats: string[];
}

export interface ReviewItem {
    id: string;
    kind: 'line' | 'field' | 'cell';
    page: number;
    label?: string;
    value: string | null;
    alternatives: string[];
    reason: string | null;
    resolved: boolean;
    corrected_value: string | null;
}

export interface ReviewState {
    status: DocStatus;
    policy?: string;
    pending?: number;
    items: ReviewItem[];
    pages: { page: number; pending: number; indexed: boolean; skipped: boolean; unreadable: boolean; doubtful_lines: number | null; lines: number | null }[];
}

export interface ContentBlock {
    type: string;
    text?: string;
    items?: unknown[];
    columns?: string[];
    rows?: string[][];
    title?: string | null;
    range?: string;
}

export interface DocContent {
    id: string;
    filename: string;
    pages: { page: number; source: string | null; label: string | null; text: string; blocks: ContentBlock[] }[];
}

export interface Filters {
    status?: string;
    doc_type?: string;
    entity?: string;
    q?: string;
    mine?: boolean;
}

export const userDocsApi = {
    types: () => apiRequest<DocTypes>('/user-documents/types'),
    list: (f: Filters = {}) => {
        const p = new URLSearchParams();
        Object.entries(f).forEach(([k, v]) => { if (v !== undefined && v !== '' && v !== false) p.set(k, String(v)); });
        const qs = p.toString();
        return apiRequest<{ documents: UserDoc[]; needs_review: number }>(`/user-documents${qs ? `?${qs}` : ''}`);
    },
    get: (id: string) => apiRequest<UserDoc>(`/user-documents/${id}`),
    upload: (files: File[], opts: { doc_type?: string; entity?: string; period?: string; tags?: string; visibility?: Visibility; reading?: Reading }) => {
        const body = new FormData();
        files.forEach(f => body.append('files', f));
        Object.entries(opts).forEach(([k, v]) => { if (v) body.append(k, v); });
        return apiRequest<{ results: UploadResult[] }>('/user-documents', { method: 'POST', body });
    },
    importFromSource: (integrationId: number, filePaths: string[], entity?: string) =>
        apiRequest<{ results: UploadResult[] }>('/user-documents/import', { method: 'POST', body: JSON.stringify({ integration_id: integrationId, file_paths: filePaths, entity: entity || null }) }),
    patch: (id: string, changes: Partial<Pick<UserDoc, 'doc_type' | 'entity' | 'period' | 'tags' | 'visibility' | 'label'>>) =>
        apiRequest<UserDoc>(`/user-documents/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
    remove: (id: string) => apiRequest<void>(`/user-documents/${id}`, { method: 'DELETE' }),
    reread: (id: string, reading?: Reading, better_pages: number[] = []) =>
        apiRequest<UserDoc>(`/user-documents/${id}/reread`, { method: 'POST', body: JSON.stringify({ reading, better_pages }) }),
    approve: (id: string) => apiRequest<UserDoc>(`/user-documents/${id}/approve`, { method: 'POST' }),
    review: (id: string) => apiRequest<ReviewState>(`/user-documents/${id}/review`),
    applyReview: (id: string, body: { edits?: Record<string, string>; confirm?: string[]; confirm_pages?: number[]; skip_pages?: number[]; unskip_pages?: number[] }) =>
        apiRequest<UserDoc>(`/user-documents/${id}/review`, { method: 'POST', body: JSON.stringify(body) }),
    content: (id: string, page?: number) => apiRequest<DocContent>(`/user-documents/${id}/content${page ? `?page=${page}` : ''}`),
    pdf: (id: string) => fetchBlob(`/user-documents/${id}/pdf`),
    file: (id: string) => fetchBlob(`/user-documents/${id}/file`),
};

export const STATUS_LABEL: Record<DocStatus, string> = {
    queued: 'Waiting', processing: 'Reading', awaiting_approval: 'Awaiting your approval', needs_review: 'Needs review', ready: 'Searchable', failed: 'Failed',
};

export function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
