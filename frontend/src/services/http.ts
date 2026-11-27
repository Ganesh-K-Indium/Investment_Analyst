// One place for the API base URL and the request/error handling every newer service shares.
import { getAuthHeader } from './authHeader';

export const API_BASE: string = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

/** FastAPI returns `detail` as a string, or a list of {loc, msg} for 422 validation errors. */
export function errorMessage(body: unknown, status: number): string {
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

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    // A FormData body needs the browser to set its own multipart Content-Type (with the boundary).
    if (!(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    for (const [key, value] of Object.entries(getAuthHeader())) headers.set(key, value);
    const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(errorMessage(body, response.status));
    }
    return response.status === 204 ? (undefined as T) : response.json();
}

/** Server-sent events over POST (EventSource cannot send a body or a bearer token). Calls onEvent for every `data:` line. */
export async function streamPost<E>(path: string, body: unknown, onEvent: (event: E) => void, signal?: AbortSignal): Promise<void> {
    const response = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
        body: JSON.stringify(body),
        signal,
    });
    if (!response.ok || !response.body) {
        const err = await response.json().catch(() => null);
        throw new Error(errorMessage(err, response.status));
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut = buffer.indexOf('\n\n');
        while (cut !== -1) {
            const frame = buffer.slice(0, cut);
            buffer = buffer.slice(cut + 2);
            const line = frame.split('\n').find(l => l.startsWith('data:'));
            if (line) {
                try { onEvent(JSON.parse(line.slice(5).trim()) as E); } catch { /* a malformed frame is skipped, not fatal */ }
            }
            cut = buffer.indexOf('\n\n');
        }
    }
}

/** A binary download (original file, PDF view) with the bearer token. */
export async function fetchBlob(path: string): Promise<Blob> {
    const response = await fetch(`${API_BASE}${path}`, { headers: { ...getAuthHeader() } });
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(errorMessage(body, response.status));
    }
    return response.blob();
}
