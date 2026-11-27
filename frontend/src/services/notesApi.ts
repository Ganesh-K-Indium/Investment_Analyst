// Analyst Notes API Service (backend: app/api/notes.py)

import { getAuthHeader } from './authHeader';
import { API_BASE } from './http';

const NOTES_BASE_URL = `${API_BASE}/notes`;

export type NoteVisibility = 'private' | 'shared';
export type NoteInputMethod = 'typed' | 'handwritten';

export interface AnalystNote {
    note_id: string;
    ticker: string;
    title: string;
    note_date: string; // YYYY-MM-DD
    visibility: NoteVisibility;
    input_method: NoteInputMethod;
    author: string;
    content: string;
    chunk_count: number;
    created_at: string;
    updated_at: string;
    is_owner: boolean; // false = shared by someone else (read-only)
    active: boolean; // the owner's pause flag: false = 'hide for now' (stored, but out of everyone's analysis)
    included: boolean; // someone else's shared note: has the current user opted in to using it?
    used_in_analysis: boolean; // will this note inform the current user's analysis right now?
}

export interface NoteInput {
    ticker: string;
    title: string;
    content: string;
    note_date?: string;
    visibility: NoteVisibility;
    input_method?: NoteInputMethod; // 'handwritten' only after the user reviewed a transcription draft
}

export type NoteUpdate = Partial<Omit<NoteInput, 'input_method'>> & { active?: boolean };

export interface TranscriptionLine {
    text: string;
    status: 'agreed' | 'review';
    alternatives: string[];
    reason: string | null;
}

export interface TranscriptionPage {
    page: number;
    source: 'ocr' | 'text-layer';
    lines: TranscriptionLine[];
}

export interface Transcription {
    text: string;
    engines: string[];
    verified: boolean; // false = only one reader was available, so numbers were not cross-checked
    needs_review: number;
    pages: TranscriptionPage[];
}

/** FastAPI returns `detail` as a string, or a list of {loc, msg} for 422 validation errors. */
function errorMessage(body: unknown, status: number): string {
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length) {
        return detail
            .map((d: { loc?: unknown[]; msg?: string }) => {
                const field = Array.isArray(d.loc) ? String(d.loc[d.loc.length - 1]) : '';
                return field && field !== 'body' ? `${field}: ${d.msg}` : d.msg;
            })
            .join('; ');
    }
    return `Request failed (${status})`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    // A FormData body needs the browser to set its own multipart Content-Type (with the boundary).
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    for (const [key, value] of Object.entries(getAuthHeader())) headers.set(key, value);
    const response = await fetch(`${NOTES_BASE_URL}${path}`, { ...init, headers });
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(errorMessage(body, response.status));
    }
    return response.status === 204 ? (undefined as T) : response.json();
}

export const notesApi = {
    list: (ticker?: string) => request<AnalystNote[]>(ticker ? `?ticker=${encodeURIComponent(ticker)}` : ''),
    get: (noteId: string) => request<AnalystNote>(`/${encodeURIComponent(noteId)}`),
    /** Draft text for a handwritten/scanned note. Saves nothing; review it, then call create(). */
    transcribe: (file: File) => {
        const body = new FormData();
        body.append('file', file);
        return request<Transcription>('/transcribe', { method: 'POST', body });
    },
    create: (note: NoteInput) => request<AnalystNote>('', { method: 'POST', body: JSON.stringify(note) }),
    update: (noteId: string, changes: NoteUpdate) =>
        request<AnalystNote>(`/${encodeURIComponent(noteId)}`, { method: 'PATCH', body: JSON.stringify(changes) }),
    /** Opt in/out of using another analyst's shared note in MY analysis. */
    setInclusion: (noteId: string, included: boolean) =>
        request<AnalystNote>(`/${encodeURIComponent(noteId)}/inclusion`, { method: 'PUT', body: JSON.stringify({ included }) }),
    remove: (noteId: string) => request<void>(`/${encodeURIComponent(noteId)}`, { method: 'DELETE' }),
};
