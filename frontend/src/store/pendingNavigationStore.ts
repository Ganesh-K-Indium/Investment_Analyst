import { create } from 'zustand';

// Lets a notification click (Header, rendered on every page) hand off "open
// this portfolio/tab/session" to Home.tsx without prop-drilling or a route
// param — Home isn't route-scoped per portfolio today, it manages the
// selected project as local state. Header sets this and navigates to
// /home; Home's own effect picks it up once its portfolio list has loaded
// and clears it.
export interface PendingNavigation {
    portfolioId: string;
    tab: 'Secondary Research' | 'Quantitative Research';
    sessionId?: string;
}

interface PendingNavigationState {
    pending: PendingNavigation | null;
    setPending: (nav: PendingNavigation) => void;
    clearPending: () => void;
}

export const usePendingNavigationStore = create<PendingNavigationState>((set) => ({
    pending: null,
    setPending: (nav) => set({ pending: nav }),
    clearPending: () => set({ pending: null }),
}));
