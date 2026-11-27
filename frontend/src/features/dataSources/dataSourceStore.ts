// ─── Data Source Store ─────────────────────────────────────────────────────────
// localStorage-backed store for managing external data sources.
// No backend APIs are used; all data is persisted locally.

export interface DataSource {
    id: string;
    name: string;
    provider: string;
    baseUrl: string;
    apiKey: string;
    description: string;
    status: 'active' | 'inactive';
    isDefault: boolean;
    credentials?: Record<string, string>;
    authName?: string;      // Custom API: the header the key is sent in
    hasKey?: boolean;
    lastError?: string | null;
}

interface DataSourceState {
    dataSources: DataSource[];
    selectedSources: string[]; // IDs of selected sources
}

const STORAGE_KEY = 'investmentAnalyst_dataSources';

const DEFAULT_DATA_SOURCE: DataSource = {
    id: 'default-yahoo-finance',
    name: 'Yahoo Finance',
    provider: 'Yahoo',
    baseUrl: 'https://finance.yahoo.com',
    apiKey: '',
    description: 'Free financial data provider with real-time quotes, portfolio management, and market news.',
    status: 'active',
    isDefault: true,
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function generateId(): string {
    return `ds-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
}

function loadState(): DataSourceState {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
            const parsed: DataSourceState = JSON.parse(raw);
            // Ensure default source always exists
            const hasDefault = parsed.dataSources.some(ds => ds.isDefault);
            if (!hasDefault) {
                parsed.dataSources.unshift(DEFAULT_DATA_SOURCE);
            }
            // Ensure at least one source is selected
            if (parsed.selectedSources.length === 0) {
                const defaultDs = parsed.dataSources.find(ds => ds.isDefault);
                if (defaultDs) {
                    parsed.selectedSources = [defaultDs.id];
                }
            }
            return parsed;
        }
    } catch (e) {
        console.error('Failed to load data source state:', e);
    }
    // First-time init
    return {
        dataSources: [DEFAULT_DATA_SOURCE],
        selectedSources: [DEFAULT_DATA_SOURCE.id],
    };
}

function saveState(state: DataSourceState): void {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
        console.error('Failed to save data source state:', e);
    }
}

// ── Public API ───────────────────────────────────────────────────────────────

export function getDataSources(): DataSource[] {
    return loadState().dataSources;
}

export function getSelectedSources(): string[] {
    return loadState().selectedSources;
}

export function addDataSource(ds: Omit<DataSource, 'id' | 'isDefault'>): DataSource {
    const state = loadState();

    // Prevent duplicate names
    if (state.dataSources.some(existing => existing.name.toLowerCase() === ds.name.toLowerCase())) {
        throw new Error(`A data source named "${ds.name}" already exists.`);
    }

    const newDs: DataSource = { ...ds, id: generateId(), isDefault: false };
    state.dataSources.push(newDs);
    saveState(state);
    return newDs;
}

export function updateDataSource(id: string, updates: Partial<Omit<DataSource, 'id' | 'isDefault'>>): DataSource {
    const state = loadState();
    const idx = state.dataSources.findIndex(ds => ds.id === id);
    if (idx === -1) throw new Error('Data source not found.');

    // Prevent duplicate names (excluding self)
    if (updates.name) {
        const duplicate = state.dataSources.some(
            existing => existing.id !== id && existing.name.toLowerCase() === updates.name!.toLowerCase()
        );
        if (duplicate) throw new Error(`A data source named "${updates.name}" already exists.`);
    }

    state.dataSources[idx] = { ...state.dataSources[idx], ...updates };
    saveState(state);
    return state.dataSources[idx];
}

export function deleteDataSource(id: string): void {
    const state = loadState();
    const ds = state.dataSources.find(d => d.id === id);
    if (!ds) throw new Error('Data source not found.');
    if (ds.isDefault) throw new Error('Cannot delete the default data source.');

    state.dataSources = state.dataSources.filter(d => d.id !== id);

    // If the deleted source was selected, remove it and fallback
    if (state.selectedSources.includes(id)) {
        state.selectedSources = state.selectedSources.filter(sid => sid !== id);
        // Ensure at least one source remains selected
        if (state.selectedSources.length === 0) {
            const defaultDs = state.dataSources.find(d => d.isDefault);
            if (defaultDs) state.selectedSources = [defaultDs.id];
        }
    }

    saveState(state);
}

export function selectSource(id: string): void {
    const state = loadState();
    if (!state.selectedSources.includes(id)) {
        state.selectedSources.push(id);
        saveState(state);
    }
}

export function deselectSource(id: string): void {
    const state = loadState();
    if (state.selectedSources.length <= 1) {
        throw new Error('At least one data source must remain selected.');
    }
    state.selectedSources = state.selectedSources.filter(sid => sid !== id);
    saveState(state);
}

// ── Validation helpers ───────────────────────────────────────────────────────

export function validateBaseUrl(url: string): boolean {
    if (!url) return true; // optional field
    try {
        new URL(url);
        return true;
    } catch {
        return false;
    }
}
