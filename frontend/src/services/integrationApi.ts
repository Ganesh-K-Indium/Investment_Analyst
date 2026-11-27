// Integrations API Service

import { getAuthHeader } from './authHeader';
import { API_BASE } from './http';

const INTEGRATIONS_BASE_URL = `${API_BASE}/integrations`;

export type IntegrationVendor = 'sharepoint' | 'google_drive' | 'onedrive' | 'confluence' | 'azure_blob' | 'aws_s3' | 'sftp' | 'postgresql';
export type IntegrationStatus = 'active' | 'disconnected' | 'error';

export interface IntegrationCredentials {
    client_id?: string;
    client_secret?: string;
    user_id?: string;
    folder_path?: string;
    [key: string]: string | undefined;
}

export interface IntegrationCreate {
    user_id: string;
    vendor: IntegrationVendor;
    name: string;
    url?: string;
    credentials: Record<string, string>;
    description?: string;
}

export interface IntegrationUpdate {
    name?: string;
    url?: string;
    credentials?: Record<string, string>;
    description?: string;
    status?: IntegrationStatus;
}

export interface IntegrationResponse {
    id: number;
    user_id: string;
    vendor: IntegrationVendor;
    name: string;
    url: string | null;
    status: IntegrationStatus;
    last_sync: string | null;
    description: string | null;
    created_at: string;
    updated_at: string;
    credentials_summary: Record<string, string>;
}

export interface RemoteFile {
    name: string;
    path: string;
    size: number | null;
    last_modified: string | null;
    mime_type: string | null;
    is_directory: boolean;
}

export interface BrowseFilesResponse {
    integration_id: number;
    vendor: string;
    path: string;
    files: RemoteFile[];
    total_count: number;
    available_tickers: string[] | null;
    portfolio_id: number | null;
    portfolio_name: string | null;
}

export interface ConnectionTestResponse {
    success: boolean;
    message: string;
    vendor: string;
    files_found: number | null;
    error?: string;
}

export const integrationApi = {
    /**
     * Get all integrations for a user
     */
    async getUserIntegrations(userId: string): Promise<IntegrationResponse[]> {
        const response = await fetch(`${INTEGRATIONS_BASE_URL}/user/${userId}`, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) throw new Error('Failed to fetch integrations');
        return response.json();
    },

    /**
     * Create a new integration
     */
    async createIntegration(data: IntegrationCreate): Promise<IntegrationResponse> {
        const response = await fetch(`${INTEGRATIONS_BASE_URL}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify(data),
        });
        if (!response.ok) throw new Error('Failed to create integration');
        return response.json();
    },

    /**
     * Update an integration
     */
    async updateIntegration(id: number, data: IntegrationUpdate): Promise<IntegrationResponse> {
        const response = await fetch(`${INTEGRATIONS_BASE_URL}/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify(data),
        });
        if (!response.ok) throw new Error('Failed to update integration');
        return response.json();
    },

    /**
     * Delete an integration
     */
    async deleteIntegration(id: number): Promise<void> {
        const response = await fetch(`${INTEGRATIONS_BASE_URL}/${id}`, {
            method: 'DELETE',
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) throw new Error('Failed to delete integration');
    },

    /**
     * Test connection for an integration
     */
    async testConnection(id: number): Promise<ConnectionTestResponse> {
        const response = await fetch(`${INTEGRATIONS_BASE_URL}/${id}/test`, {
            method: 'POST',
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) throw new Error('Connection test failed');
        return response.json();
    },

    /**
     * Browse files in an integration
     */
    async browseFiles(integrationId: number, path?: string | null, searchQuery?: string, portfolioId?: number, userId?: string): Promise<BrowseFilesResponse> {
        const response = await fetch(`${INTEGRATIONS_BASE_URL}/browse`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({
                integration_id: integrationId,
                path,
                search_query: searchQuery,
                portfolio_id: portfolioId,
                user_id: userId
            }),
        });
        if (!response.ok) throw new Error('Failed to browse files');
        return response.json();
    }
};

export default integrationApi;
