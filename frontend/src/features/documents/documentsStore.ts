import { create } from 'zustand';

// Remembers the library's current result list so the Inspector can offer
// previous/next document inside the same filtered view, and a Back link that
// restores the exact filters (`search` is the library's raw query string).
interface DocumentsListState {
    ids: string[];
    search: string;
    setList: (ids: string[], search: string) => void;
}

export const useDocumentsStore = create<DocumentsListState>((set) => ({
    ids: [],
    search: '',
    setList: (ids, search) => set({ ids, search }),
}));

const LAST_FILTERS_KEY = 'documents.lastFilters';
export const loadLastFilters = () => localStorage.getItem(LAST_FILTERS_KEY) || '';
export const saveLastFilters = (search: string) => localStorage.setItem(LAST_FILTERS_KEY, search);
