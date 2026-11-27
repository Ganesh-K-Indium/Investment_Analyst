// Shared in-memory access-token store for plain service modules (not React
// components/hooks, so useAuth()'s authHeader() isn't reachable here).
//
// The access token is intentionally NOT persisted to localStorage/
// sessionStorage — it only lives as a JS variable for the life of the tab.
// Any XSS that runs on the page can still read it while the tab is open
// (that risk is unavoidable for a bearer token used in JS-driven fetches),
// but it no longer survives a reload, a closed tab, or disk inspection the
// way a localStorage value would. On reload, AuthContext re-derives it by
// calling /auth/refresh, which relies on the httpOnly refresh cookie instead.
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
    accessToken = token;
}

export function getAccessToken(): string | null {
    return accessToken;
}

export function getAuthHeader(): Record<string, string> {
    return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}
