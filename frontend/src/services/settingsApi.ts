// Settings API (backend: app/api/settings.py). The UI renders its forms from /settings/schema.
import { apiRequest } from './http';

export type Settings = Record<string, Record<string, unknown>>;

export type FieldType = 'bool' | 'int' | 'float' | 'enum' | 'text' | 'list' | 'model' | 'json';

export interface FieldSpec {
    key: string;
    type: FieldType;
    default: unknown;
    label: string;
    help: string;
    min?: number;
    max?: number;
    options?: string[];
}

export interface SectionSpec {
    id: string;
    label: string;
    help: string;
    fields: FieldSpec[];
}

export interface SettingsSchema {
    sections: SectionSpec[];
    defaults: Settings;
    model_roles: Record<string, { label: string; default: string }>;
}

export interface SettingsResponse {
    saved: Settings;
    effective: Settings;
}

export interface Usage {
    month_start: string;
    tokens: number;
    estimated_cost_usd: number;
    budget: number;
    over_budget: boolean;
    by_role: Record<string, { tokens: number; cost_usd: number }>;
    /** Tokens of models with no known price (counted, not costed). Set prices in config: model_prices. */
    unpriced_tokens?: number;
    prices_as_of?: string;
    by_model?: { model: string; priced: boolean; tokens: number; cost_usd: number }[];
}

export interface EditablePrompt {
    name: string;
    label: string;
    help: string;
    default: string;
}

export interface Framework {
    id: string;
    name: string;
    description: string;
    triggers: string[];
    sections: string[];
    sources: string[];
    prompt: string;
    enabled: boolean;
}

const q = (portfolioId?: number | null) => (portfolioId ? `?portfolio_id=${portfolioId}` : '');

export const settingsApi = {
    schema: () => apiRequest<SettingsSchema>('/settings/schema'),
    get: (portfolioId?: number | null) => apiRequest<SettingsResponse>(`/settings${q(portfolioId)}`),
    save: (patch: Settings) => apiRequest<SettingsResponse>('/settings', { method: 'PUT', body: JSON.stringify(patch) }),
    reset: (section?: string) => apiRequest<SettingsResponse>(`/settings${section ? `?section=${encodeURIComponent(section)}` : ''}`, { method: 'DELETE' }),
    getPortfolio: (id: number) => apiRequest<SettingsResponse>(`/settings/portfolio/${id}`),
    savePortfolio: (id: number, patch: Settings) => apiRequest<SettingsResponse>(`/settings/portfolio/${id}`, { method: 'PUT', body: JSON.stringify(patch) }),
    resetPortfolio: (id: number, section?: string) =>
        apiRequest<SettingsResponse>(`/settings/portfolio/${id}${section ? `?section=${encodeURIComponent(section)}` : ''}`, { method: 'DELETE' }),
    usage: () => apiRequest<Usage>('/settings/usage'),
    prompts: () => apiRequest<{ prompts: EditablePrompt[] }>('/settings/prompts'),
    frameworkTemplate: () => apiRequest<Framework>('/settings/frameworks/template'),
    quantHealth: () => apiRequest<{ status: string; servers_ready: Record<string, boolean>; agents_ready: boolean; timestamp: string }>('/quant/health'),
};
