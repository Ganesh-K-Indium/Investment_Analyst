// Reading engines API (backend: app/api/reading_engines.py)
import { apiRequest } from './http';

export interface EngineModels {
    state: 'bundled' | 'downloaded' | 'missing' | 'none';
    files?: number;
    bytes?: number;
    approx_mb?: number;
}

export interface LocalEngine {
    id: string;
    label: string;
    summary: string;
    ready: boolean;
    cross_checked: boolean;
    models: EngineModels;
    status: 'ready' | 'not_installed';
    hint: string;
    prepare: { state: 'running' | 'done' | 'failed'; error: string } | null;
}

export interface Strategy { id: string; label: string; mode: string; summary: string }

export interface EnginesResponse {
    local_only: boolean;
    locked: boolean;
    models_dir: string;
    local_engines: LocalEngine[];
    strategies: Strategy[];
    selected_engine: string;
    plan: { strategy: string; mode: string; local_engine: string | null; models: string[]; uses_models: boolean; notes: string[] };
    can_prepare: boolean;
}

export interface EngineTest { ok: boolean; error?: string; seconds?: number; text?: string; confidence?: number | null; score?: number; tokens?: number }

export const readingApi = {
    engines: () => apiRequest<EnginesResponse>('/reading/engines'),
    prepare: (id: string) => apiRequest<{ state: string }>(`/reading/engines/${id}/prepare`, { method: 'POST' }),
    test: (kind: 'local' | 'model', id: string) => apiRequest<EngineTest>('/reading/engines/test', { method: 'POST', body: JSON.stringify({ kind, id }) }),
};
