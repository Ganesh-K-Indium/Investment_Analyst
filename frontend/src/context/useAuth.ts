/** The signed-in user and auth actions. The provider (state and effects) is in AuthContext.tsx; this file holds what
 * components import, so the provider file exports only a component and keeps hot reload working. */
import { createContext, useContext } from 'react';
import type { UserProfile } from '../services/authApi';

export interface AuthState {
    user: UserProfile | null;
    userId: string;          // string version of user.id — used everywhere as user_id
    accessToken: string | null;
    isAuthenticated: boolean;
    isLoading: boolean;
}

export interface AuthContextType extends AuthState {
    login: (email: string, password: string) => Promise<void>;
    signup: (email: string, username: string, password: string, fullName: string, role: string) => Promise<void>;
    logout: () => void;
    /** Returns the Bearer header value for API calls that need auth */
    authHeader: () => Record<string, string>;
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = (): AuthContextType => {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
    return ctx;
};
