import React from 'react';
import type {
    IntegrationFormData,
    GoogleDriveAuthMode,
    ValidationErrors,
} from '../../services/integrationValidation';
import './VendorSpecificFields.css';

// ─── Props ───────────────────────────────────────────────────────────────────

interface VendorSpecificFieldsProps {
    vendor: string;
    formData: IntegrationFormData;
    errors: ValidationErrors;
    onChange: <K extends keyof IntegrationFormData>(
        field: K,
        value: IntegrationFormData[K],
    ) => void;
    isViewMode: boolean;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const FieldError: React.FC<{ message?: string; id?: string }> = ({ message, id }) => {
    if (!message) return null;
    return (
        <span className="field-error" id={id} role="alert">
            <svg className="field-error__icon" width="12" height="12" viewBox="0 0 24 24"
                fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            {message}
        </span>
    );
};

const FieldHelper: React.FC<{ text: string }> = ({ text }) => (
    <span className="field-helper">{text}</span>
);

const RequiredMark: React.FC = () => <span className="field-required" aria-hidden="true">*</span>;

// ─── Component ───────────────────────────────────────────────────────────────

export const VendorSpecificFields: React.FC<VendorSpecificFieldsProps> = ({
    vendor,
    formData,
    errors,
    onChange,
    isViewMode,
}) => {
    void isViewMode;
    const inputClass = (field: keyof IntegrationFormData) =>
        `input-field${errors[field] ? ' field-error-input' : ''}`;

    // ── Google Drive ─────────────────────────────────────────────────────

    if (vendor === 'Google Drive') {
        return (
            <>
                {/* Auth Mode Selector */}
                <div className="auth-mode-selector" role="radiogroup" aria-label="Authentication method">
                    <button
                        type="button"
                        role="radio"
                        aria-checked={formData.gdAuthMode === 'service_account'}
                        className={`auth-mode-selector__option${formData.gdAuthMode === 'service_account' ? ' auth-mode-selector__option--active' : ''}`}
                        onClick={() => onChange('gdAuthMode', 'service_account' as GoogleDriveAuthMode)}
                    >
                        <input
                            type="radio"
                            className="auth-mode-selector__radio"
                            checked={formData.gdAuthMode === 'service_account'}
                            onChange={() => onChange('gdAuthMode', 'service_account' as GoogleDriveAuthMode)}
                            tabIndex={-1}
                            aria-hidden="true"
                        />
                        Service Account
                    </button>
                    <button
                        type="button"
                        role="radio"
                        aria-checked={formData.gdAuthMode === 'oauth2'}
                        className={`auth-mode-selector__option${formData.gdAuthMode === 'oauth2' ? ' auth-mode-selector__option--active' : ''}`}
                        onClick={() => onChange('gdAuthMode', 'oauth2' as GoogleDriveAuthMode)}
                    >
                        <input
                            type="radio"
                            className="auth-mode-selector__radio"
                            checked={formData.gdAuthMode === 'oauth2'}
                            onChange={() => onChange('gdAuthMode', 'oauth2' as GoogleDriveAuthMode)}
                            tabIndex={-1}
                            aria-hidden="true"
                        />
                        OAuth2
                    </button>
                </div>

                {formData.gdAuthMode === 'service_account' ? (
                    /* ── Service Account Mode ───────────────────────── */
                    <div className="modal-form__field">
                        <label className="modal-form__label" htmlFor="gd-sa-json">
                            Service Account JSON<RequiredMark />
                        </label>
                        <textarea
                            id="gd-sa-json"
                            className={`${inputClass('serviceAccountJson')} vendor-fields__json-textarea`}
                            placeholder='{"type":"service_account","project_id":"...","private_key":"..."}'
                            value={formData.serviceAccountJson}
                            onChange={(e) => onChange('serviceAccountJson', e.target.value)}
                            rows={5}
                            autoComplete="off"
                            spellCheck={false}
                            aria-invalid={!!errors.serviceAccountJson}
                            aria-describedby={errors.serviceAccountJson ? 'gd-sa-json-error' : undefined}
                        />
                        <FieldError message={errors.serviceAccountJson} id="gd-sa-json-error" />
                    </div>
                ) : (
                    /* ── OAuth2 Mode ────────────────────────────────── */
                    <>
                        <div className="modal-form__row">
                            <div className="modal-form__field">
                                <label className="modal-form__label" htmlFor="gd-client-id">
                                    Client ID<RequiredMark />
                                </label>
                                <input
                                    id="gd-client-id"
                                    type="text"
                                    className={inputClass('clientId')}
                                    placeholder="OAuth2 Client ID"
                                    value={formData.clientId}
                                    onChange={(e) => onChange('clientId', e.target.value)}
                                    autoComplete="off"
                                    aria-invalid={!!errors.clientId}
                                    aria-describedby={errors.clientId ? 'gd-client-id-error' : undefined}
                                />
                                <FieldError message={errors.clientId} id="gd-client-id-error" />
                            </div>
                            <div className="modal-form__field">
                                <label className="modal-form__label" htmlFor="gd-client-secret">
                                    Client Secret<RequiredMark />
                                </label>
                                <input
                                    id="gd-client-secret"
                                    type="password"
                                    className={inputClass('clientSecret')}
                                    placeholder="••••••••"
                                    value={formData.clientSecret}
                                    onChange={(e) => onChange('clientSecret', e.target.value)}
                                    autoComplete="new-password"
                                    aria-invalid={!!errors.clientSecret}
                                    aria-describedby={errors.clientSecret ? 'gd-client-secret-error' : undefined}
                                />
                                <FieldError message={errors.clientSecret} id="gd-client-secret-error" />
                            </div>
                        </div>
                        <div className="modal-form__field">
                            <label className="modal-form__label" htmlFor="gd-refresh-token">
                                Refresh Token<RequiredMark />
                            </label>
                            <input
                                id="gd-refresh-token"
                                type="password"
                                className={inputClass('refreshToken')}
                                placeholder="••••••••"
                                value={formData.refreshToken}
                                onChange={(e) => onChange('refreshToken', e.target.value)}
                                autoComplete="new-password"
                                aria-invalid={!!errors.refreshToken}
                                aria-describedby={errors.refreshToken ? 'gd-refresh-token-error' : undefined}
                            />
                            <FieldError message={errors.refreshToken} id="gd-refresh-token-error" />
                        </div>
                    </>
                )}

                {/* Starting Folder ID */}
                <div className="modal-form__field">
                    <label className="modal-form__label" htmlFor="gd-folder">
                        Starting Folder ID (Optional)
                    </label>
                    <input
                        id="gd-folder"
                        type="text"
                        className="input-field"
                        placeholder="e.g. 1A2B3c4D5e6F7g8H"
                        value={formData.folder}
                        onChange={(e) => onChange('folder', e.target.value)}
                        autoComplete="off"
                        aria-describedby="gd-folder-help"
                    />
                    <FieldHelper text="Paste the folder ID from the Google Drive URL (after /folders/). Leave blank to start at root." />
                </div>
            </>
        );
    }

    // ── Confluence ───────────────────────────────────────────────────────

    if (vendor === 'Confluence') {
        return (
            <>
                {/* Confluence URL */}
                <div className="modal-form__field">
                    <label className="modal-form__label" htmlFor="cf-url">
                        Confluence URL<RequiredMark />
                    </label>
                    <input
                        id="cf-url"
                        type="url"
                        className={inputClass('url')}
                        placeholder="https://company.atlassian.net/wiki"
                        value={formData.url}
                        onChange={(e) => onChange('url', e.target.value)}
                        autoComplete="off"
                        aria-invalid={!!errors.url}
                        aria-describedby={errors.url ? 'cf-url-error' : 'cf-url-help'}
                    />
                    <FieldError message={errors.url} id="cf-url-error" />
                    {!errors.url && (
                        <FieldHelper text="Must include /wiki (e.g., https://company.atlassian.net/wiki)" />
                    )}
                </div>

                {/* Email + API Token */}
                <div className="modal-form__row">
                    <div className="modal-form__field">
                        <label className="modal-form__label" htmlFor="cf-email">
                            Atlassian Account Email<RequiredMark />
                        </label>
                        <input
                            id="cf-email"
                            type="email"
                            className={inputClass('username')}
                            placeholder="your-email@company.com"
                            value={formData.username}
                            onChange={(e) => onChange('username', e.target.value)}
                            autoComplete="off"
                            aria-invalid={!!errors.username}
                            aria-describedby={errors.username ? 'cf-email-error' : undefined}
                        />
                        <FieldError message={errors.username} id="cf-email-error" />
                    </div>
                    <div className="modal-form__field">
                        <label className="modal-form__label" htmlFor="cf-api-token">
                            API Token<RequiredMark />
                        </label>
                        <input
                            id="cf-api-token"
                            type="password"
                            className={inputClass('apiToken')}
                            placeholder="••••••••"
                            value={formData.apiToken}
                            onChange={(e) => onChange('apiToken', e.target.value)}
                            autoComplete="new-password"
                            aria-invalid={!!errors.apiToken}
                            aria-describedby={errors.apiToken ? 'cf-api-token-error' : undefined}
                        />
                        <FieldError message={errors.apiToken} id="cf-api-token-error" />
                    </div>
                </div>

                {/* Space Key */}
                <div className="modal-form__field">
                    <label className="modal-form__label" htmlFor="cf-space">
                        Space Key (Optional)
                    </label>
                    <input
                        id="cf-space"
                        type="text"
                        className="input-field"
                        placeholder="e.g. DEV, TEAM"
                        value={formData.folder}
                        onChange={(e) => onChange('folder', e.target.value)}
                        autoComplete="off"
                        aria-describedby="cf-space-help"
                    />
                    <FieldHelper text="Optional. Enter space key (e.g., DEV, TEAM). Leave blank to browse all spaces." />
                </div>
            </>
        );
    }

    // ── SharePoint / OneDrive ────────────────────────────────────────────

    if (vendor === 'SharePoint' || vendor === 'OneDrive') {
        return (
            <>
                <div className="modal-form__field">
                    <label className="modal-form__label" htmlFor="sp-tenant">
                        Tenant ID
                    </label>
                    <input
                        id="sp-tenant"
                        type="text"
                        className="input-field"
                        placeholder="Enter Tenant ID"
                        value={formData.tenantId}
                        onChange={(e) => onChange('tenantId', e.target.value)}
                        autoComplete="off"
                    />
                </div>
                <div className="modal-form__row">
                    <div className="modal-form__field">
                        <label className="modal-form__label" htmlFor="sp-client-id">
                            Client ID<RequiredMark />
                        </label>
                        <input
                            id="sp-client-id"
                            type="text"
                            className={inputClass('clientId')}
                            placeholder="Enter Client ID"
                            value={formData.clientId}
                            onChange={(e) => onChange('clientId', e.target.value)}
                            autoComplete="off"
                            aria-invalid={!!errors.clientId}
                            aria-describedby={errors.clientId ? 'sp-client-id-error' : undefined}
                        />
                        <FieldError message={errors.clientId} id="sp-client-id-error" />
                    </div>
                    <div className="modal-form__field">
                        <label className="modal-form__label" htmlFor="sp-client-secret">
                            Client Secret<RequiredMark />
                        </label>
                        <input
                            id="sp-client-secret"
                            type="password"
                            className={inputClass('clientSecret')}
                            placeholder="••••••••"
                            value={formData.clientSecret}
                            onChange={(e) => onChange('clientSecret', e.target.value)}
                            autoComplete="new-password"
                            aria-invalid={!!errors.clientSecret}
                            aria-describedby={errors.clientSecret ? 'sp-client-secret-error' : undefined}
                        />
                        <FieldError message={errors.clientSecret} id="sp-client-secret-error" />
                    </div>
                </div>
                <div className="modal-form__field">
                    <label className="modal-form__label" htmlFor="sp-folder">
                        Folder Path (Optional)
                    </label>
                    <input
                        id="sp-folder"
                        type="text"
                        className="input-field"
                        placeholder="Enter folder path"
                        value={formData.folder}
                        onChange={(e) => onChange('folder', e.target.value)}
                        autoComplete="off"
                    />
                </div>
            </>
        );
    }

    // ── Generic (Azure Blob, S3, SFTP, etc.) ─────────────────────────────

    return (
        <>
            <div className="modal-form__row">
                <div className="modal-form__field">
                    <label className="modal-form__label" htmlFor="gen-client-id">
                        Client ID<RequiredMark />
                    </label>
                    <input
                        id="gen-client-id"
                        type="text"
                        className={inputClass('clientId')}
                        placeholder="Enter Client ID"
                        value={formData.clientId}
                        onChange={(e) => onChange('clientId', e.target.value)}
                        autoComplete="off"
                        aria-invalid={!!errors.clientId}
                        aria-describedby={errors.clientId ? 'gen-client-id-error' : undefined}
                    />
                    <FieldError message={errors.clientId} id="gen-client-id-error" />
                </div>
                <div className="modal-form__field">
                    <label className="modal-form__label" htmlFor="gen-client-secret">
                        Client Secret<RequiredMark />
                    </label>
                    <input
                        id="gen-client-secret"
                        type="password"
                        className={inputClass('clientSecret')}
                        placeholder="••••••••"
                        value={formData.clientSecret}
                        onChange={(e) => onChange('clientSecret', e.target.value)}
                        autoComplete="new-password"
                        aria-invalid={!!errors.clientSecret}
                        aria-describedby={errors.clientSecret ? 'gen-client-secret-error' : undefined}
                    />
                    <FieldError message={errors.clientSecret} id="gen-client-secret-error" />
                </div>
            </div>
            <div className="modal-form__field">
                <label className="modal-form__label" htmlFor="gen-folder">
                    Folder Path (Optional)
                </label>
                <input
                    id="gen-folder"
                    type="text"
                    className="input-field"
                    placeholder="Enter folder path"
                    value={formData.folder}
                    onChange={(e) => onChange('folder', e.target.value)}
                    autoComplete="off"
                />
            </div>
        </>
    );
};

export default VendorSpecificFields;
