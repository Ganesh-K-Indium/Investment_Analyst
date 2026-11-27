// AI providers API (backend: app/api/providers.py). Keys are write-only: the server returns only whether one is set and its last four characters.
import { apiRequest } from './http';

export interface ProviderField {
    name: string;
    label: string;
    secret: boolean;
    kind: 'key' | 'url' | 'text';
    help: string;
    set: boolean;
    source: 'yours' | 'workspace' | 'server' | null;
    preview: string;
}

export interface Provider {
    id: string;
    label: string;
    cloud: boolean;
    hint: string;
    caveat: string;
    installed: boolean;
    configured: boolean;
    source: 'yours' | 'workspace' | 'server' | null;
    fields: ProviderField[];
    models: string[];
    defaults: { strong: string; light: string } | null;
    vision: boolean;
    presets: { label: string; url: string }[];
}

export interface ProvidersResponse {
    providers: Provider[];
    embeddings: { provider: string; model: string; dimensions: number; note: string };
    local_only: boolean;
    locked: boolean;
    encryption_ok: boolean;
}

export interface TestResult {
    ok: boolean;
    message: string;
    latency_ms?: number;
    model?: string;
}

export const providersApi = {
    list: () => apiRequest<ProvidersResponse>('/providers'),
    /** Set a value (string) or remove it (null). Only the fields you include change. */
    save: (id: string, values: Record<string, string | null>, scope: 'user' | 'workspace' = 'user') =>
        apiRequest<Provider>(`/providers/${id}?scope=${scope}`, { method: 'PUT', body: JSON.stringify({ values }) }),
    remove: (id: string, scope: 'user' | 'workspace' = 'user') => apiRequest<Provider>(`/providers/${id}?scope=${scope}`, { method: 'DELETE' }),
    /** Try a request with values you have not saved yet (they win over saved ones). */
    test: (id: string, model: string, values: Record<string, string> = {}) =>
        apiRequest<TestResult>(`/providers/${id}/test`, { method: 'POST', body: JSON.stringify({ model, values }) }),
    models: (id: string) => apiRequest<{ models: string[] }>(`/providers/${id}/models`),
};

/** "anthropic:claude-sonnet-5-5" -> { provider: 'anthropic', model: 'claude-sonnet-5-5' }. A bare name is OpenAI; only known provider names count as a prefix. */
export function splitSpec(spec: string, known: string[]): { provider: string; model: string } {
    const i = spec.indexOf(':');
    if (i > 0 && known.includes(spec.slice(0, i).toLowerCase())) return { provider: spec.slice(0, i).toLowerCase(), model: spec.slice(i + 1) };
    return { provider: 'openai', model: spec };
}

export const joinSpec = (provider: string, model: string): string => (provider === 'openai' ? model : `${provider}:${model}`);
