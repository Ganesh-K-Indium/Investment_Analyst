import { create } from 'zustand';
import { reportApi, type DraftItem, type AddDraftItemPayload } from '../services/reportApi';

interface ClipboardState {
    items: DraftItem[];
    isLoading: boolean;
    userId: string | null;
    portfolioId: number | null;

    // Derived counts used by tab badges
    secondaryCount: () => number;
    quantCount: () => number;
    totalCount: () => number;

    // Actions
    init: (userId: string, portfolioId?: number) => Promise<void>;
    addItem: (payload: AddDraftItemPayload) => Promise<void>;
    removeItem: (itemId: number) => Promise<void>;
    reorder: (orderedIds: number[]) => Promise<void>;
    clearAll: () => Promise<void>;
    refresh: () => Promise<void>;
}

export const useClipboardStore = create<ClipboardState>((set, get) => ({
    items: [],
    isLoading: false,
    userId: null,
    portfolioId: null,

    secondaryCount: () => get().items.filter(i => i.source === 'rag' || i.source === 'summary').length,
    quantCount: () => get().items.filter(i => i.source === 'quant').length,
    totalCount: () => get().items.length,

    init: async (userId: string, portfolioId?: number) => {
        const state = get();
        // Re-fetch if user or portfolio changed
        if (state.userId === userId && state.portfolioId === (portfolioId ?? null) && state.items.length > 0) return;
        set({ userId, portfolioId: portfolioId ?? null, isLoading: true });
        try {
            const items = await reportApi.getDraftItems(userId, portfolioId);
            set({ items, isLoading: false });
        } catch {
            set({ isLoading: false });
        }
    },

    refresh: async () => {
        const { userId, portfolioId } = get();
        if (!userId) return;
        try {
            const items = await reportApi.getDraftItems(userId, portfolioId ?? undefined);
            set({ items });
        } catch {
            // Keep showing the items already loaded if a refresh fails; the next action retries.
        }
    },

    addItem: async (payload: AddDraftItemPayload) => {
        try {
            const item = await reportApi.addDraftItem(payload);
            set(state => ({ items: [...state.items, item] }));
        } catch (err) {
            console.error('Failed to clip item:', err);
            throw err;
        }
    },

    removeItem: async (itemId: number) => {
        const userId = get().userId;
        if (!userId) return;
        try {
            await reportApi.deleteDraftItem(itemId, userId);
            set(state => ({ items: state.items.filter(i => i.id !== itemId) }));
        } catch (err) {
            console.error('Failed to remove item:', err);
            throw err;
        }
    },

    reorder: async (orderedIds: number[]) => {
        const { userId, portfolioId } = get();
        if (!userId) return;
        try {
            const items = await reportApi.reorderDraftItems(userId, orderedIds, portfolioId ?? undefined);
            set({ items });
        } catch (err) {
            console.error('Failed to reorder:', err);
        }
    },

    clearAll: async () => {
        const { userId, portfolioId } = get();
        if (!userId) return;
        try {
            await reportApi.clearDraftItems(userId, portfolioId ?? undefined);
            set({ items: [] });
        } catch (err) {
            console.error('Failed to clear clipboard:', err);
            throw err;
        }
    },
}));
