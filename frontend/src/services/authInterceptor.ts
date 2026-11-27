import { setAccessToken, getAccessToken } from './authHeader';
import { API_BASE } from './http';

// Installs a one-time global `fetch` patch so an expired/invalid session is
// caught wherever it happens (any service module's fetch call), not just on
// app mount. Without this, a token that expires while the tab stays open
// (e.g. the 30-minute access token lapsing mid-session, or the laptop closed
// overnight) surfaces as raw 401s in the console with no user-facing recovery.
//
// On a 401 from an authenticated request, this first tries one silent
// refresh (via the httpOnly refresh cookie) before giving up — otherwise
// every user would get logged out every 30 minutes of active use, which is
// worse than the original bug. Only if the refresh itself fails does it
// dispatch the session-expired event that AuthContext listens for.
const UNAUTHORIZED_EVENT = 'auth:unauthorized';

const AUTH_BASE = `${API_BASE}/auth`;

let installed = false;
/** true: refreshed. false: the server ended the session. null: could not reach the server (offline, restarting): try later. */
export type RefreshResult = boolean | null;
let refreshInFlight: Promise<RefreshResult> | null = null;
let baseFetch: typeof fetch = (...args) => window.fetch(...args);

function hasAuthHeader(init: RequestInit | undefined): boolean {
    const headers = init?.headers;
    if (!headers) return false;
    return headers instanceof Headers
        ? headers.has('Authorization')
        : Object.keys(headers as Record<string, string>).some(k => k.toLowerCase() === 'authorization');
}

async function doRefresh(): Promise<RefreshResult> {
    try {
        const res = await baseFetch(`${AUTH_BASE}/refresh`, { method: 'POST', credentials: 'include' });
        if (res.status === 401 || res.status === 403) return false;
        if (!res.ok) return null;
        const tokens = await res.json();
        setAccessToken(tokens.access_token);
        window.dispatchEvent(new CustomEvent('auth:token-refreshed', { detail: tokens.access_token }));
        return true;
    } catch {
        return null;
    }
}

/**
 * Exchange the refresh cookie for a new access token. One refresh at a time in this tab, and (where the browser has Web
 * Locks) across tabs too: each refresh token works once, so two tabs must not spend the same cookie at the same moment.
 */
export function refreshSession(): Promise<RefreshResult> {
    if (!refreshInFlight) {
        const locks = (navigator as Navigator & { locks?: LockManager }).locks;
        const run = locks ? locks.request('ia-auth-refresh', doRefresh) : doRefresh();
        refreshInFlight = Promise.resolve(run).finally(() => { refreshInFlight = null; });
    }
    return refreshInFlight;
}

export function installAuthInterceptor(): void {
    if (installed) return;
    installed = true;

    const originalFetch = window.fetch.bind(window);
    baseFetch = originalFetch;
    window.fetch = async (...args: Parameters<typeof fetch>) => {
        const response = await originalFetch(...args);
        if (response.status === 401) {
            const [input, init] = args;
            const urlStr = typeof input === 'string' ? input : input instanceof Request ? input.url : '';
            
            // Never retry the auth endpoints themselves (login, refresh, signup, logout).
            if (urlStr.startsWith(`${AUTH_BASE}/`)) {
                return response;
            }

            if (hasAuthHeader(init) && getAccessToken()) {
                const refreshed = await refreshSession();
                if (refreshed) {
                    const newToken = getAccessToken();
                    if (input instanceof Request) {
                        const newHeaders = new Headers(input.headers);
                        if (newToken) {
                            newHeaders.set('Authorization', `Bearer ${newToken}`);
                        }
                        return originalFetch(new Request(input, { headers: newHeaders }));
                    } else {
                        const newHeaders = new Headers(init?.headers || {});
                        if (newToken) {
                            newHeaders.set('Authorization', `Bearer ${newToken}`);
                        }
                        return originalFetch(input, { ...init, headers: newHeaders });
                    }
                } else if (refreshed === false) {
                    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
                }
            }
        }
        return response;
    };
}

export function onSessionExpired(handler: () => void): () => void {
    window.addEventListener(UNAUTHORIZED_EVENT, handler);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler);
}

export function onTokenRefreshed(handler: (accessToken: string) => void): () => void {
    const listener = (e: Event) => handler((e as CustomEvent<string>).detail);
    window.addEventListener('auth:token-refreshed', listener);
    return () => window.removeEventListener('auth:token-refreshed', listener);
}
