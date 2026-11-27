import { API_BASE } from '../../services/http';
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Modal } from '../../components/ui/Modal';
import './FileSources.css';
import { integrationApi, type IntegrationResponse, type IntegrationVendor, type IntegrationStatus } from '../../services/integrationApi';
import {
    INITIAL_FORM_DATA,
    validateIntegrationForm,
    isFormValid,
    buildCredentials,
    hasSecretsChanged,
    type IntegrationFormData,
    type ValidationErrors,
} from '../../services/integrationValidation';
import { VendorSpecificFields } from '../../components/integrations/VendorSpecificFields';
import { useAuth } from '../../context/useAuth';

const VENDOR_MAP: Record<string, IntegrationVendor> = {
    'SharePoint': 'sharepoint',
    'Azure Blob': 'azure_blob',
    'S3': 'aws_s3',
    'SFTP': 'sftp',
    'Google Drive': 'google_drive',
    'OneDrive': 'onedrive',
    'Confluence': 'confluence'
};

const REVERSE_VENDOR_MAP: Record<string, string> = Object.entries(VENDOR_MAP).reduce((acc, [k, v]) => ({ ...acc, [v]: k }), {});

// Status classes mapping
const statusClassMap: Record<IntegrationStatus, string> = {
    'active': 'connected',
    'disconnected': 'disconnected',
    'error': 'error'
};

const VENDOR_NAME: Record<string, string> = {
    sharepoint: 'SharePoint', google_drive: 'Google Drive', onedrive: 'OneDrive', confluence: 'Confluence',
    azure_blob: 'Azure Blob Storage', aws_s3: 'Amazon S3', sftp: 'SFTP',
};

const vendorOptions = ['SharePoint', 'Google Drive', 'OneDrive', 'Confluence', 'Azure Blob', 'S3', 'SFTP'];

interface Props {
    /** Each time this number goes up, the "connect a file source" form opens. */
    addSignal: number;
}

/** The connected file sources (SharePoint, Drive, OneDrive, Confluence, Azure Blob, S3, SFTP): cards, details, test, connect/disconnect, delete. */
export const FileSourcesSection: React.FC<Props> = ({ addSignal }) => {
    const { userId: USER_ID } = useAuth();
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [modalMode, setModalMode] = useState<'create' | 'view'>('create');
    const [integrations, setIntegrations] = useState<IntegrationResponse[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [testStatus, setTestStatus] = useState<{ id: number; loading: boolean; success?: boolean; message?: string } | null>(null);
    const [activeMenuId, setActiveMenuId] = useState<number | null>(null);
    const [editingIntegration, setEditingIntegration] = useState<IntegrationResponse | null>(null);
    const menuRef = useRef<HTMLDivElement>(null);

    // Consolidated form state
    const [formData, setFormData] = useState<IntegrationFormData>({ ...INITIAL_FORM_DATA });
    const [validationErrors, setValidationErrors] = useState<ValidationErrors>({});
    const [hasAttemptedSubmit, setHasAttemptedSubmit] = useState(false);

    const loadIntegrations = useCallback(async () => {
        setIsLoading(true);
        try {
            const data = await integrationApi.getUserIntegrations(USER_ID);
            setIntegrations(data.filter(i => i.vendor !== 'postgresql'));   // databases are listed under Databases
        } catch (error) {
            console.error('Failed to load integrations:', error);
        } finally {
            setIsLoading(false);
        }
    }, [USER_ID]);

    // Load integrations on mount (and again if the signed-in user changes)
    useEffect(() => {
        loadIntegrations();
    }, [loadIntegrations]);

    useEffect(() => {
        if (addSignal > 0) openCreateModal();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [addSignal]);

    // Close menu when clicking outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
                setActiveMenuId(null);
            }
        };

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    // ── Form helpers ─────────────────────────────────────────────────────

    const updateField = useCallback(<K extends keyof IntegrationFormData>(
        field: K,
        value: IntegrationFormData[K],
    ) => {
        setFormData(prev => {
            const next = { ...prev, [field]: value };
            // Live-validate once user has attempted submit
            if (hasAttemptedSubmit) {
                setValidationErrors(validateIntegrationForm(
                    field === 'vendor' ? value as string : prev.vendor,
                    next,
                ));
            }
            return next;
        });
    }, [hasAttemptedSubmit]);

    const resetForm = useCallback(() => {
        setFormData({ ...INITIAL_FORM_DATA });
        setValidationErrors({});
        setHasAttemptedSubmit(false);
        setEditingIntegration(null);
    }, []);

    const openCreateModal = () => {
        resetForm();
        setModalMode('create');
        setIsModalOpen(true);
    };

    const openViewModal = (integration: IntegrationResponse) => {
        setEditingIntegration(integration);
        const vendorLabel = REVERSE_VENDOR_MAP[integration.vendor] || '';
        setFormData({
            vendor: vendorLabel,
            url: integration.url || '',
            userId: integration.credentials_summary.user_id || '',
            clientId: integration.credentials_summary.client_id || '',
            clientSecret: '********',
            folder: integration.credentials_summary.folder_path || '',
            tenantId: integration.credentials_summary.tenant_id || '',
            gdAuthMode: integration.credentials_summary.service_account_json ? 'service_account' : 'oauth2',
            serviceAccountJson: integration.credentials_summary.service_account_json || '',
            refreshToken: integration.credentials_summary.refresh_token || '',
            username: integration.credentials_summary.username || '',
            apiToken: integration.credentials_summary.api_token ? '********' : '',
        });
        setValidationErrors({});
        setHasAttemptedSubmit(false);
        setModalMode('view');
        setIsModalOpen(true);
        setActiveMenuId(null);
        setTestStatus(null);
    };

    // ── CRUD handlers ────────────────────────────────────────────────────

    const handleCreateIntegration = async () => {
        setHasAttemptedSubmit(true);
        const errors = validateIntegrationForm(formData.vendor, formData);
        setValidationErrors(errors);
        if (Object.keys(errors).length > 0) return;

        const backendVendor = VENDOR_MAP[formData.vendor];
        if (!backendVendor) return;

        setIsSubmitting(true);
        try {
            const credentials = buildCredentials(formData.vendor, backendVendor, formData, USER_ID, false);

            await integrationApi.createIntegration({
                user_id: USER_ID,
                vendor: backendVendor,
                name: `${formData.vendor} Integration`,
                url: formData.url,
                credentials
            });

            loadIntegrations();
            resetForm();
            setIsModalOpen(false);
        } catch (error) {
            console.error('Failed to create integration:', error);
            setValidationErrors({ form: 'Failed to create integration. Please try again.' });
        } finally {
            setIsSubmitting(false);
        }
    };

    const handleUpdateIntegration = async () => {
        if (!editingIntegration) return;

        setHasAttemptedSubmit(true);
        const errors = validateIntegrationForm(formData.vendor, formData);
        setValidationErrors(errors);
        if (Object.keys(errors).length > 0) return;

        const backendVendor = VENDOR_MAP[formData.vendor];
        if (!backendVendor) return;

        setIsSubmitting(true);
        try {
            const secretsChanged = hasSecretsChanged(backendVendor, formData);
            const credentials = secretsChanged
                ? buildCredentials(formData.vendor, backendVendor, formData, USER_ID, true)
                : undefined;

            await integrationApi.updateIntegration(editingIntegration.id, {
                name: `${formData.vendor} Integration`,
                url: formData.url,
                credentials
            });

            loadIntegrations();
            resetForm();
            setIsModalOpen(false);
        } catch (error) {
            console.error('Failed to update integration:', error);
            setValidationErrors({ form: 'Failed to update integration. Please try again.' });
        } finally {
            setIsSubmitting(false);
        }
    };

    const handleToggleConnection = async (id: number) => {
        const integration = integrations.find(i => i.id === id);
        if (!integration) return;

        try {
            // If connected, disconnect
            if (integration.status === 'active') {
                await fetch(`${API_BASE}/integrations/${id}/disconnect`, { method: 'POST' });
            } else {
                // For now just toggle status if disconnected
                await integrationApi.updateIntegration(id, { status: 'active' });
            }
            loadIntegrations();
        } catch (error) {
            console.error('Failed to toggle connection:', error);
        }
        setActiveMenuId(null);
    };

    const handleDeleteIntegration = async (id: number) => {
        if (!confirm('Are you sure you want to delete this integration?')) return;
        try {
            await integrationApi.deleteIntegration(id);
            loadIntegrations();
        } catch (error) {
            console.error('Failed to delete integration:', error);
        }
        setActiveMenuId(null);
    };

    const handleTestConnection = async (id: number) => {
        setTestStatus({ id, loading: true });
        try {
            const result = await integrationApi.testConnection(id);
            setTestStatus({
                id,
                loading: false,
                success: result.success,
                message: result.message
            });
        } catch {
            setTestStatus({
                id,
                loading: false,
                success: false,
                message: 'Connection test failed unexpectedly.'
            });
        }
    };

    const getVendorIcon = (vendorKey: string): string => {
        const icons: Record<string, string> = {
            'sharepoint': '📁',
            'azure_blob': '☁️',
            'aws_s3': '🪣',
            'sftp': '🔒',
            'google_drive': '📂',
            'onedrive': '💾',
            'confluence': '📝'
        };
        return icons[vendorKey] || '🔗';
    };

    // ── Derived state ────────────────────────────────────────────────────

    const formValid = formData.vendor ? isFormValid(formData.vendor, formData) : false;
    const isCreateDisabled = !formValid || isSubmitting;

    // ── URL label / placeholder per vendor ────────────────────────────────

    const getUrlLabel = (): string => {
        switch (formData.vendor) {
            case 'SharePoint': return 'Site URL';
            case 'Google Drive': return 'Shared Drive ID (Optional)';
            default: return 'URL';
        }
    };

    const getUrlPlaceholder = (): string => {
        switch (formData.vendor) {
            case 'SharePoint': return 'https://company.sharepoint.com/sites/mysite';
            case 'Google Drive': return 'Optional shared drive ID';
            default: return 'Enter URL';
        }
    };

    // ── Render ────────────────────────────────────────────────────────────

    return (
        <>
                <div className={`integrations-page__grid ${isLoading ? 'integrations-page__grid--loading' : ''}`}>
                    {!isLoading && integrations.length === 0 && (
                        <p className="file-sources__empty">No file sources connected yet. Choose Add data source, then Files and documents.</p>
                    )}
                    {integrations.map(integration => (
                        <Card key={integration.id} hoverable className="integration-card">
                            <div className="integration-card__icon">{getVendorIcon(integration.vendor)}</div>
                            <div className="integration-card__content">
                                <h3 className="integration-card__title">{integration.name}</h3>
                                <p className="integration-card__vendor">{VENDOR_NAME[integration.vendor] ?? integration.vendor}</p>
                                {integration.url && <p className="integration-card__url" title={integration.url}>{integration.url}</p>}
                            </div>
                            <div className="integration-card__footer">
                                <span className={`integration-card__status integration-card__status--${statusClassMap[integration.status] || 'disconnected'}`}>
                                    {integration.status === 'active' ? '● Connected' : integration.status === 'disconnected' ? '○ Disconnected' : '! Needs attention'}
                                </span>
                                <div className="integration-card__menu-wrapper" ref={activeMenuId === integration.id ? menuRef : null}>
                                    <button
                                        className="integration-card__menu"
                                        onClick={() => setActiveMenuId(activeMenuId === integration.id ? null : integration.id)}
                                    >
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                            <circle cx="12" cy="12" r="1" />
                                            <circle cx="19" cy="12" r="1" />
                                            <circle cx="5" cy="12" r="1" />
                                        </svg>
                                    </button>
                                    {activeMenuId === integration.id && (
                                        <div className="integration-card__dropdown">
                                            <button
                                                className="integration-card__dropdown-item"
                                                onClick={() => openViewModal(integration)}
                                            >
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                                                    <circle cx="12" cy="12" r="3" />
                                                </svg>
                                                View Details
                                            </button>
                                            <button
                                                className="integration-card__dropdown-item"
                                                onClick={() => handleToggleConnection(integration.id)}
                                            >
                                                {integration.status === 'active' ? (
                                                    <>
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                            <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
                                                            <line x1="12" y1="2" x2="12" y2="12" />
                                                        </svg>
                                                        Disconnect
                                                    </>
                                                ) : (
                                                    <>
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                            <path d="M5 12.55a11 11 0 0 1 14.08 0" />
                                                            <path d="M1.42 9a16 16 0 0 1 21.16 0" />
                                                            <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
                                                            <circle cx="12" cy="20" r="1" />
                                                        </svg>
                                                        Connect
                                                    </>
                                                )}
                                            </button>
                                            <button
                                                className="integration-card__dropdown-item integration-card__dropdown-item--danger"
                                                onClick={() => handleDeleteIntegration(integration.id)}
                                            >
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                    <polyline points="3 6 5 6 21 6" />
                                                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                                </svg>
                                                Delete source
                                            </button>
                                        </div>
                                    )}
                                </div>
                            </div>
                        </Card>
                    ))}
                </div>
            {/* Create/View file source */}
            <Modal
                isOpen={isModalOpen}
                onClose={() => { resetForm(); setIsModalOpen(false); }}
                title={modalMode === 'create' ? 'Connect a file source' : 'File source details'}
            >
                <form
                    className="modal-form"
                    onSubmit={(e) => {
                        e.preventDefault();
                        if (isSubmitting) return;
                        if (modalMode === 'create') handleCreateIntegration();
                        else handleUpdateIntegration();
                    }}
                    noValidate
                >
                    {/* Form-level error */}
                    {validationErrors.form && (
                        <div style={{
                            padding: '8px 12px',
                            borderRadius: '6px',
                            fontSize: '13px',
                            backgroundColor: 'var(--color-error-surface)',
                            color: 'var(--color-error-dark)',
                            marginBottom: '12px',
                        }} role="alert">
                            {validationErrors.form}
                        </div>
                    )}

                    {/* Vendor Selector */}
                    <div className="modal-form__field">
                        <label className="modal-form__label" htmlFor="vendor-select">Vendor</label>
                        <select
                            id="vendor-select"
                            className="modal-form__select"
                            value={formData.vendor}
                            onChange={(e) => {
                                updateField('vendor', e.target.value);
                                // Clear errors when switching vendor
                                setValidationErrors({});
                                setHasAttemptedSubmit(false);
                            }}
                            aria-label="Select integration vendor"
                        >
                            <option value="">Select</option>
                            {vendorOptions.map(opt => (
                                <option key={opt} value={opt}>{opt}</option>
                            ))}
                        </select>
                    </div>

                    {/* URL + User ID row (skip for Confluence — it has its own URL field) */}
                    {formData.vendor && formData.vendor !== 'Confluence' && (
                        <div className="modal-form__row">
                            <div className="modal-form__field">
                                <label className="modal-form__label" htmlFor="integration-url">
                                    {getUrlLabel()}
                                </label>
                                <input
                                    id="integration-url"
                                    type="text"
                                    className="input-field"
                                    placeholder={getUrlPlaceholder()}
                                    value={formData.url}
                                    onChange={(e) => updateField('url', e.target.value)}
                                    autoComplete="off"
                                />
                            </div>
                            <div className="modal-form__field">
                                <label className="modal-form__label" htmlFor="integration-user-id">User Id</label>
                                <input
                                    id="integration-user-id"
                                    type="text"
                                    className="input-field"
                                    placeholder={USER_ID}
                                    value={formData.userId}
                                    onChange={(e) => updateField('userId', e.target.value)}
                                    autoComplete="off"
                                />
                            </div>
                        </div>
                    )}

                    {/* Confluence gets a User ID field without the URL (handled inside VendorSpecificFields) */}
                    {formData.vendor === 'Confluence' && (
                        <div className="modal-form__field">
                            <label className="modal-form__label" htmlFor="integration-user-id">User Id</label>
                            <input
                                id="integration-user-id"
                                type="text"
                                className="input-field"
                                placeholder={USER_ID}
                                value={formData.userId}
                                onChange={(e) => updateField('userId', e.target.value)}
                                autoComplete="off"
                            />
                        </div>
                    )}

                    {/* Vendor-Specific Fields */}
                    {formData.vendor && (
                        <VendorSpecificFields
                            vendor={formData.vendor}
                            formData={formData}
                            errors={validationErrors}
                            onChange={updateField}
                            isViewMode={modalMode === 'view'}
                        />
                    )}

                    {/* Test Connection (view mode only) */}
                    {modalMode === 'view' && editingIntegration && (
                        <div className="modal-form__test-section" style={{ marginTop: '20px', borderTop: '1px solid var(--color-border-light)', paddingTop: '15px' }}>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => handleTestConnection(editingIntegration.id)}
                                disabled={testStatus?.loading}
                            >
                                {testStatus?.loading ? 'Testing...' : 'Test Connection'}
                            </Button>
                            {testStatus && !testStatus.loading && (
                                <div style={{
                                    marginTop: '8px',
                                    padding: '8px',
                                    borderRadius: '4px',
                                    fontSize: '13px',
                                    backgroundColor: testStatus.success ? 'var(--color-success-surface)' : 'var(--color-error-surface)',
                                    color: testStatus.success ? 'var(--color-success-dark)' : 'var(--color-error-dark)'
                                }}>
                                    {testStatus.success ? '✓ ' : '✗ '}
                                    {testStatus.message}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Actions */}
                    <div className="modal-form__actions">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => { resetForm(); setIsModalOpen(false); }}
                            disabled={isSubmitting}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="submit"
                            variant="primary"
                            disabled={modalMode === 'create' ? isCreateDisabled : isSubmitting}
                            aria-busy={isSubmitting}
                        >
                            {isSubmitting
                                ? (modalMode === 'create' ? 'Creating...' : 'Updating...')
                                : (modalMode === 'create' ? 'Create' : 'Update')
                            }
                        </Button>
                    </div>
                </form>
            </Modal>
        </>
    );
};

export default FileSourcesSection;
