import React, { useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { authApi, type UserProfile, type TokenResponse } from '../services/authApi';
import { useClipboardStore } from '../store/clipboardStore';
import { useAnalysisRunsStore } from '../store/analysisRunsStore';
import { getAccessToken, setAccessToken as setStoredAccessToken } from '../services/authHeader';
import { installAuthInterceptor, onSessionExpired, onTokenRefreshed, refreshSession } from '../services/authInterceptor';
import { AuthContext } from './useAuth';

export const SESSION_EXPIRED_KEY = 'ia_session_expired';

/** Refresh this long before the access token runs out, so no request starts with a token about to lapse. */
const REFRESH_AHEAD_MS = 60_000;
/** Tabs of this app tell each other about sign-in and sign-out. */
const CHANNEL = 'ia-auth';

/** Milliseconds since the epoch when a JWT expires, read from its payload (no verification: the server does that). */
function expiresAt(token: string): number | null {
    try {
        const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        const exp = (JSON.parse(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), '='))) as { exp?: number }).exp;
        return typeof exp === 'number' ? exp * 1000 : null;
    } catch {
        return null;
    }
}

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [user, setUser]               = useState<UserProfile | null>(null);
    const [accessToken, setAccessToken]  = useState<string | null>(null);
    const [isLoading, setIsLoading]      = useState(true);
    const channel = useRef<BroadcastChannel | null>(null);
    const userIdRef = useRef<number | null>(null);
    useEffect(() => { userIdRef.current = user?.id ?? null; }, [user]);

    // The access token is kept only in memory (React state + the module-level
    // store in authHeader.ts, for plain non-hook service files) — never in
    // localStorage. The refresh token lives entirely in the httpOnly cookie
    // the backend sets; the frontend never sees its value at all.
    const applyTokens = useCallback((tokens: TokenResponse) => {
        setStoredAccessToken(tokens.access_token);
        setAccessToken(tokens.access_token);
    }, []);

    const clearSession = useCallback(() => {
        setStoredAccessToken(null);
        setAccessToken(null);
        setUser(null);
        useAnalysisRunsStore.getState().clearAll();
    }, []);

    const clipboardInit = useClipboardStore(s => s.init);

    const startSession = useCallback(async (tokens: TokenResponse) => {
        applyTokens(tokens);
        const profile = await authApi.me(tokens.access_token);
        setUser(profile);
        clipboardInit(String(profile.id));
        channel.current?.postMessage({ type: 'login', userId: profile.id });
    }, [applyTokens, clipboardInit]);

    /** Pick up a sign-in from the refresh cookie (page load, or another tab signed in). */
    const resume = useCallback(async () => {
        if ((await refreshSession()) !== true) return false;
        const token = getAccessToken();
        if (!token) return false;
        const profile = await authApi.me(token);
        setAccessToken(token);
        setUser(profile);
        clipboardInit(String(profile.id));
        return true;
    }, [clipboardInit]);

    const logout = useCallback(() => {
        authApi.logout().catch(() => {});
        clearSession();
        channel.current?.postMessage({ type: 'logout' });
    }, [clearSession]);

    // Any API call anywhere in the app that comes back 401 (token expired,
    // e.g. the laptop was closed overnight and the access/refresh tokens both
    // lapsed) clears the session so the route guard bounces to /login instead
    // of the screen quietly failing with raw backend 401s.
    useEffect(() => {
        installAuthInterceptor();
        const offExpired = onSessionExpired(() => {
            sessionStorage.setItem(SESSION_EXPIRED_KEY, '1');
            clearSession();
        });
        // The interceptor updates the module-level token store directly (so
        // plain service files see it immediately); this keeps React's own
        // accessToken state in sync with that same silent refresh.
        const offRefreshed = onTokenRefreshed((token) => setAccessToken(token));
        return () => { offExpired(); offRefreshed(); };
    }, [clearSession]);

    // Signing out in one tab signs out the others; signing in in one tab signs in the others.
    useEffect(() => {
        if (typeof BroadcastChannel === 'undefined') return;
        const ch = new BroadcastChannel(CHANNEL);
        channel.current = ch;
        ch.onmessage = (e: MessageEvent<{ type?: string; userId?: number }>) => {
            if (e.data?.type === 'logout') clearSession();
            if (e.data?.type === 'login') {
                if (userIdRef.current === null) void resume().catch(() => undefined);
                // Someone else signed in in another tab: this tab's screens hold the previous user's data, so start clean.
                else if (userIdRef.current !== e.data.userId) window.location.reload();
            }
        };
        return () => { ch.close(); channel.current = null; };
    }, [clearSession, resume]);

    // Renew the access token shortly before it expires, and straight away when the tab comes back after a sleep.
    useEffect(() => {
        if (!accessToken) return;
        const exp = expiresAt(accessToken);
        if (!exp) return;
        let timer = 0;
        const renew = () => {
            void refreshSession().then(ok => {
                if (ok === false) { sessionStorage.setItem(SESSION_EXPIRED_KEY, '1'); clearSession(); }
                else if (ok === null) timer = window.setTimeout(renew, 15_000);    // server unreachable: keep the session, try again
            });
        };
        timer = window.setTimeout(renew, Math.max(5_000, exp - Date.now() - REFRESH_AHEAD_MS));
        const onVisible = () => {
            if (document.visibilityState === 'visible' && exp - Date.now() < REFRESH_AHEAD_MS) renew();
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', onVisible); };
    }, [accessToken, clearSession]);

    // On mount there's no access token in memory yet (a reload wipes it) —
    // silently exchange the httpOnly refresh cookie (if any) for a fresh
    // access token instead of reading anything from localStorage.
    useEffect(() => {
        resume()
            .catch(() => { /* no valid session — stay logged out */ })
            .finally(() => setIsLoading(false));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const login = useCallback(async (email: string, password: string) => {
        useAnalysisRunsStore.getState().clearAll();
        await startSession(await authApi.login(email, password));
    }, [startSession]);

    const signup = useCallback(async (
        email: string, username: string, password: string, fullName: string, role: string
    ) => {
        useAnalysisRunsStore.getState().clearAll();
        await startSession(await authApi.signup(email, username, password, fullName, role));
    }, [startSession]);

    const authHeader = useCallback((): Record<string, string> => {
        return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
    }, [accessToken]);

    return (
        <AuthContext.Provider value={{
            user,
            userId: user ? String(user.id) : '',
            accessToken,
            isAuthenticated: !!user,
            isLoading,
            login,
            signup,
            logout,
            authHeader,
        }}>
            {children}
        </AuthContext.Provider>
    );
};
