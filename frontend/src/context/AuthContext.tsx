import React, { useState, useEffect, useCallback, type ReactNode } from 'react';
import { authApi, type UserProfile, type TokenResponse } from '../services/authApi';
import { useClipboardStore } from '../store/clipboardStore';
import { useAnalysisRunsStore } from '../store/analysisRunsStore';
import { setAccessToken as setStoredAccessToken } from '../services/authHeader';
import { installAuthInterceptor, onSessionExpired, onTokenRefreshed } from '../services/authInterceptor';
import { AuthContext } from './useAuth';

export const SESSION_EXPIRED_KEY = 'ia_session_expired';


export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [user, setUser]               = useState<UserProfile | null>(null);
    const [accessToken, setAccessToken]  = useState<string | null>(null);
    const [isLoading, setIsLoading]      = useState(true);

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

    const logout = useCallback(() => {
        if (accessToken) {
            authApi.logout(accessToken).catch(() => {});
        }
        clearSession();
    }, [accessToken, clearSession]);

    const clipboardInit = useClipboardStore(s => s.init);

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

    // On mount there's no access token in memory yet (a reload wipes it) —
    // silently exchange the httpOnly refresh cookie (if any) for a fresh
    // access token instead of reading anything from localStorage.
    useEffect(() => {
        authApi.refresh()
            .then(async tokens => {
                applyTokens(tokens);
                const profile = await authApi.me(tokens.access_token);
                setUser(profile);
                clipboardInit(String(profile.id));
            })
            .catch(() => { /* no valid session — stay logged out */ })
            .finally(() => setIsLoading(false));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const login = useCallback(async (email: string, password: string) => {
        useAnalysisRunsStore.getState().clearAll();
        const tokens = await authApi.login(email, password);
        applyTokens(tokens);
        const profile = await authApi.me(tokens.access_token);
        setUser(profile);
        clipboardInit(String(profile.id));
    }, [applyTokens, clipboardInit]);

    const signup = useCallback(async (
        email: string, username: string, password: string, fullName: string, role: string
    ) => {
        useAnalysisRunsStore.getState().clearAll();
        const tokens = await authApi.signup(email, username, password, fullName, role);
        applyTokens(tokens);
        const profile = await authApi.me(tokens.access_token);
        setUser(profile);
        clipboardInit(String(profile.id));
    }, [applyTokens, clipboardInit]);

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
