import { create } from 'zustand';
import { providersApi, type Provider, type ProvidersResponse } from '../services/providersApi';

interface State {
    data: ProvidersResponse | null;
    loading: boolean;
    error: string | null;
    load: (force?: boolean) => Promise<void>;
    replace: (p: Provider) => void;
}

/** The providers and which of them this user can use right now. Shared by the model pickers and the AI providers tab. */
export const useProvidersStore = create<State>((set, get) => ({
    data: null,
    loading: false,
    error: null,
    load: async (force = false) => {
        if (get().loading || (get().data && !force)) return;
        set({ loading: true });
        try {
            set({ data: await providersApi.list(), error: null, loading: false });
        } catch (e) {
            set({ error: e instanceof Error ? e.message : 'Could not load the providers', loading: false });
        }
    },
    replace: p => set(s => (s.data ? { data: { ...s.data, providers: s.data.providers.map(x => (x.id === p.id ? p : x)) } } : s)),
}));
