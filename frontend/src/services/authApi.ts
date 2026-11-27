import { API_BASE } from './http';
const BASE = `${API_BASE}/auth`;

export interface TokenResponse {
    access_token: string;
    token_type: string;
    user_id: number;
    email: string;
    username: string;
    role: string;
}

export interface UserProfile {
    id: number;
    email: string;
    username: string;
    full_name: string | null;
    role: string;
    is_active: boolean;
    created_at: string;
}

function extractDetail(detail: unknown): string {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        // FastAPI 422 validation errors: [{ loc, msg, type }]
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
        headers: { 'Content-Type': 'application/json', ...options.headers },
        // The refresh token now lives only in an httpOnly cookie the backend
        // sets on /login, /signup and /refresh — 'include' is what makes the
        // browser send/accept that cookie on these (cross-origin) requests.
        credentials: 'include',
        ...options,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(extractDetail(err.detail) || res.statusText || 'Request failed');
    }
    return res.json();
}

export const authApi = {
    login: (email: string, password: string) =>
        request<TokenResponse>('/login', {
            method: 'POST',
            body: JSON.stringify({ email, password }),
        }),

    signup: (email: string, username: string, password: string, full_name: string, role: string) =>
        request<TokenResponse>('/signup', {
            method: 'POST',
            body: JSON.stringify({ email, username, password, full_name, role }),
        }),

    // No argument: the refresh token travels as an httpOnly cookie the
    // browser attaches automatically, never touched by JS.
    refresh: () =>
        request<TokenResponse>('/refresh', { method: 'POST' }),

    me: (token: string) =>
        request<UserProfile>('/me', {
            headers: { Authorization: `Bearer ${token}` },
        }),

    // Ends this browser's sign-in using the refresh cookie alone, so it works even after the access token has expired.
    logout: () =>
        request<{ message: string }>('/logout', { method: 'POST' }),
};
