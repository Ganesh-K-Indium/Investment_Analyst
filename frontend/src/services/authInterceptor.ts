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
let refreshInFlight: Promise<boolean> | null = null;

function hasAuthHeader(init: RequestInit | undefined): boolean {
    const headers = init?.headers;
    if (!headers) return false;
    return headers instanceof Headers
        ? headers.has('Authorization')
        : Object.keys(headers as Record<string, string>).some(k => k.toLowerCase() === 'authorization');
}

function attemptRefresh(originalFetch: typeof fetch): Promise<boolean> {
    if (!refreshInFlight) {
        refreshInFlight = originalFetch(`${AUTH_BASE}/refresh`, {
            method: 'POST',
            credentials: 'include',
        })
            .then(async res => {
                if (!res.ok) return false;
                const tokens = await res.json();
                setAccessToken(tokens.access_token);
                window.dispatchEvent(new CustomEvent('auth:token-refreshed', { detail: tokens.access_token }));
                return true;
            })
            .catch(() => false)
            .finally(() => { refreshInFlight = null; });
    }
    return refreshInFlight;
}

export function installAuthInterceptor(): void {
    if (installed) return;
    installed = true;

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args: Parameters<typeof fetch>) => {
        const response = await originalFetch(...args);
        if (response.status === 401) {
            const [input, init] = args;
            const urlStr = typeof input === 'string' ? input : input instanceof Request ? input.url : '';
            
            // Do not intercept or retry 401s on /auth endpoints (login/refresh/signup)
            if (urlStr.includes('/auth/')) {
                return response;
            }

            if (hasAuthHeader(init) && getAccessToken()) {
                const refreshed = await attemptRefresh(originalFetch);
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
                } else {
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
