/**
 * Integration form validation and credential building utilities.
 * Keeps all validation logic out of the UI components.
 */

import type { IntegrationVendor } from './integrationApi';

// ─── Types ───────────────────────────────────────────────────────────────────

export type GoogleDriveAuthMode = 'service_account' | 'oauth2';

export interface IntegrationFormData {
    vendor: string;
    url: string;
    userId: string;
    clientId: string;
    clientSecret: string;
    folder: string;
    tenantId: string;
    // Google Drive
    gdAuthMode: GoogleDriveAuthMode;
    serviceAccountJson: string;
    refreshToken: string;
    // Confluence
    username: string;
    apiToken: string;
}

export type ValidationErrors = Partial<Record<keyof IntegrationFormData | 'form', string>>;

export const INITIAL_FORM_DATA: IntegrationFormData = {
    vendor: '',
    url: '',
    userId: '',
    clientId: '',
    clientSecret: '',
    folder: '',
    tenantId: '',
    gdAuthMode: 'service_account',
    serviceAccountJson: '',
    refreshToken: '',
    username: '',
    apiToken: '',
};

// ─── Validators ──────────────────────────────────────────────────────────────

export function validateGoogleDrive(data: IntegrationFormData): ValidationErrors {
    const errors: ValidationErrors = {};

    if (data.gdAuthMode === 'service_account') {
        if (!data.serviceAccountJson.trim()) {
            errors.serviceAccountJson = 'Service Account JSON is required.';
        } else {
            try {
                const parsed = JSON.parse(data.serviceAccountJson);
                if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
                    errors.serviceAccountJson = 'Must be a valid JSON object.';
                }
            } catch {
                errors.serviceAccountJson = 'Invalid JSON. Check for syntax errors.';
            }
        }
    } else {
        // OAuth2
        if (!data.clientId.trim()) errors.clientId = 'Client ID is required for OAuth2.';
        if (!data.clientSecret.trim()) errors.clientSecret = 'Client Secret is required for OAuth2.';
        if (!data.refreshToken.trim()) errors.refreshToken = 'Refresh Token is required for OAuth2.';
    }

    return errors;
}

export function validateConfluence(data: IntegrationFormData): ValidationErrors {
    const errors: ValidationErrors = {};

    if (!data.url.trim()) {
        errors.url = 'Confluence URL is required.';
    } else {
        const url = data.url.trim().toLowerCase();
        if (!url.startsWith('https://')) {
            errors.url = 'URL must start with https://';
        } else if (!url.includes('atlassian.net')) {
            errors.url = 'URL must contain atlassian.net';
        } else if (!url.includes('/wiki')) {
            errors.url = 'URL must include /wiki path.';
        }
    }

    if (!data.username.trim()) {
        errors.username = 'Atlassian Account Email is required.';
    }

    if (!data.apiToken.trim()) {
        errors.apiToken = 'API Token is required.';
    }

    return errors;
}

export function validateGenericVendor(data: IntegrationFormData): ValidationErrors {
    const errors: ValidationErrors = {};
    if (!data.clientId.trim()) errors.clientId = 'Client ID is required.';
    if (!data.clientSecret.trim()) errors.clientSecret = 'Client Secret is required.';
    return errors;
}

// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * Validate form data for the selected vendor.
 * Returns an empty object when everything is valid.
 */
export function validateIntegrationForm(
    _vendor: string,
    _data: IntegrationFormData,
): ValidationErrors {
    // All validations removed per request — form is never blocked
    return {};
}

/**
 * Quick boolean check — is the form currently valid?
 */
export function isFormValid(vendor: string, data: IntegrationFormData): boolean {
    return Object.keys(validateIntegrationForm(vendor, data)).length === 0;
}

/**
 * Build the credentials payload for the backend.
 * When `isUpdate` is true, masked values (********) are omitted.
 */
export function buildCredentials(
    vendor: string,
    backendVendor: IntegrationVendor,
    data: IntegrationFormData,
    defaultUserId: string,
    isUpdate: boolean = false,
): Record<string, string> {
    void vendor;
    const credentials: Record<string, string> = {
        user_id: data.userId || defaultUserId,
        folder_path: data.folder,
    };

    if (backendVendor === 'google_drive') {
        if (data.gdAuthMode === 'service_account') {
            if (data.serviceAccountJson) {
                credentials.service_account_json = data.serviceAccountJson;
            }
        } else {
            if (data.clientId) credentials.client_id = data.clientId;
            if (!isUpdate || data.clientSecret !== '********') {
                if (data.clientSecret) credentials.client_secret = data.clientSecret;
            }
            if (data.refreshToken) credentials.refresh_token = data.refreshToken;
        }
    } else if (backendVendor === 'confluence') {
        credentials.username = data.username;
        if (!isUpdate || data.apiToken !== '********') {
            credentials.api_token = data.apiToken;
        }
    } else {
        credentials.client_id = data.clientId;
        if (!isUpdate || data.clientSecret !== '********') {
            credentials.client_secret = data.clientSecret;
        }
        credentials.tenant_id = data.tenantId;
    }

    return credentials;
}

/**
 * Determine whether any secret field has been changed from its masked value
 * (used to decide whether credentials should be sent on update).
 */
export function hasSecretsChanged(
    backendVendor: IntegrationVendor,
    data: IntegrationFormData,
): boolean {
    if (backendVendor === 'confluence') {
        return data.apiToken !== '********';
    }
    if (backendVendor === 'google_drive' && data.gdAuthMode === 'service_account') {
        return data.serviceAccountJson !== '********' && data.serviceAccountJson.trim() !== '';
    }
    return data.clientSecret !== '********';
}
