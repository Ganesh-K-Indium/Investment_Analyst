// Market-data providers the quant agent can query with your own key (backend: app/api/data_sources.py). Keys are write-only.
import { apiRequest } from './http';

export interface DataProvider { id: string; label: string; hint: string; needs_url: boolean; operations: { name: string; args: string[]; help: string }[] }
export interface RestSource {
    id: string; provider: string; provider_label: string; name: string; base_url: string | null; description: string | null;
    enabled: boolean; has_key: boolean; last_error: string | null; operations: string[];
}
export interface RestList { providers: DataProvider[]; sources: RestSource[]; max: number }

export const dataSourcesApi = {
    list: () => apiRequest<RestList>('/data-sources'),
    add: (b: { provider: string; name: string; api_key?: string; base_url?: string; auth_name?: string; description?: string }) =>
        apiRequest<RestSource>('/data-sources', { method: 'POST', body: JSON.stringify(b) }),
    patch: (id: string, b: Partial<{ name: string; description: string; enabled: boolean; api_key: string; auth_name: string }>) =>
        apiRequest<RestSource>(`/data-sources/${id}`, { method: 'PATCH', body: JSON.stringify(b) }),
    remove: (id: string) => apiRequest<void>(`/data-sources/${id}`, { method: 'DELETE' }),
    test: (id: string) => apiRequest<RestSource>(`/data-sources/${id}/test`, { method: 'POST' }),
};
