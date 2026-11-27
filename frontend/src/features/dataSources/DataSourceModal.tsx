import React, { useState } from 'react';
import { Modal } from '../../components/ui/Modal';
import { Button } from '../../components/ui/Button';
import { type DataSource, validateBaseUrl } from './dataSourceStore';
import './DataSourceModal.css';

// Every provider here is runnable from the quant agent once connected. Bloomberg and Refinitiv have no open internet API: reach them
// through your own gateway with "Custom API".
const MARKET_PROVIDERS = ['Alpha Vantage', 'Polygon.io', 'Finnhub', 'Twelve Data', 'Financial Modeling Prep', 'Custom API'];
const DATABASE_PROVIDERS = ['PostgreSQL'];
const HINTS: Record<string, string> = {
    'Alpha Vantage': 'Free key at alphavantage.co. Quotes, daily prices, company overview, statements and news.',
    'Polygon.io': 'Key from polygon.io. End-of-day bars, company details, reported financials and news.',
    'Finnhub': 'Free key at finnhub.io. Quotes, company profile, key metrics and news.',
    'Twelve Data': 'Key from twelvedata.com. Quotes, daily prices, profile and statements.',
    'Financial Modeling Prep': 'Key from financialmodelingprep.com. Quotes, prices, profile, statements and news.',
    'Custom API': 'Your own REST API, or an enterprise gateway in front of Bloomberg or Refinitiv. Read-only requests. The key is checked when you connect it.',
};

interface DataSourceModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSave: (data: Omit<DataSource, 'id' | 'isDefault'>) => void;
    editingSource?: DataSource | null;
    /** What is being added: market data providers or databases. Editing ignores it. */
    kind?: 'market' | 'database';
    existingNames: string[];
}

interface FormErrors {
    name?: string;
    provider?: string;
    baseUrl?: string;
    pgHost?: string;
    pgDatabase?: string;
    pgUser?: string;
    apiKey?: string;
    form?: string;
}

export const DataSourceModal: React.FC<DataSourceModalProps> = ({
    isOpen,
    onClose,
    onSave,
    editingSource,
    kind = 'market',
    existingNames,
}) => {
    const [name, setName] = useState('');
    const [provider, setProvider] = useState('');
    const [baseUrl, setBaseUrl] = useState('');
    const [apiKey, setApiKey] = useState('');
    const [authName, setAuthName] = useState('Authorization');
    const [pgHost, setPgHost] = useState('');
    const [pgPort, setPgPort] = useState('5432');
    const [pgDatabase, setPgDatabase] = useState('');
    const [pgUser, setPgUser] = useState('');
    const [pgPassword, setPgPassword] = useState('');
    const [description, setDescription] = useState('');
    const [status, setStatus] = useState<'active' | 'inactive'>('active');
    const [errors, setErrors] = useState<FormErrors>({});
    const [hasAttemptedSubmit, setHasAttemptedSubmit] = useState(false);

    // Reset the form when the modal opens or what it edits changes. Done while rendering (React's pattern for
    // resetting state on a prop change) rather than in an effect, which would render the stale form once first.
    const [resetFor, setResetFor] = useState<{ isOpen: boolean; editingSource?: DataSource | null; kind: typeof kind } | null>(null);
    if (!resetFor || resetFor.isOpen !== isOpen || resetFor.editingSource !== editingSource || resetFor.kind !== kind) {
        setResetFor({ isOpen, editingSource, kind });
        if (isOpen) {
            if (editingSource) {
                setName(editingSource.name);
                setProvider(editingSource.provider);
                setBaseUrl(editingSource.baseUrl);
                setApiKey('');
                setAuthName(editingSource.authName || 'Authorization');
                setDescription(editingSource.description);
                setStatus(editingSource.status);
                if (editingSource.provider === 'PostgreSQL' && editingSource.credentials) {
                    setPgHost(editingSource.credentials.host || '');
                    setPgPort(editingSource.credentials.port || '5432');
                    setPgDatabase(editingSource.credentials.database || '');
                    setPgUser(editingSource.credentials.user || '');
                    setPgPassword(editingSource.credentials.password || '');
                }
            } else {
                setName('');
                setProvider(kind === 'database' ? 'PostgreSQL' : '');
                setBaseUrl('');
                setApiKey('');
                setAuthName('Authorization');
                setDescription('');
                setStatus('active');
                setPgHost('');
                setPgPort('5432');
                setPgDatabase('');
                setPgUser('');
                setPgPassword('');
            }
            setErrors({});
            setHasAttemptedSubmit(false);
        }
    }

    const validate = (): FormErrors => {
        const errs: FormErrors = {};
        if (!name.trim()) errs.name = 'Name is required.';
        else {
            const isDuplicate = existingNames.some(
                n => n.toLowerCase() === name.trim().toLowerCase() &&
                    (!editingSource || editingSource.name.toLowerCase() !== name.trim().toLowerCase())
            );
            if (isDuplicate) errs.name = 'A data source with this name already exists.';
        }
        if (!provider) errs.provider = 'Provider is required.';
        
        if (provider === 'PostgreSQL') {
            if (!pgHost.trim()) errs.pgHost = 'Host is required.';
            if (!pgDatabase.trim()) errs.pgDatabase = 'Database name is required.';
            if (!pgUser.trim()) errs.pgUser = 'Username is required.';
        } else {
            if (provider === 'Custom API' && (!baseUrl.trim() || !validateBaseUrl(baseUrl))) errs.baseUrl = 'Enter the address of your API (https://...).';
            if (!editingSource && !apiKey.trim() && provider !== 'Custom API') errs.apiKey = 'An API key is required.';
        }
        
        return errs;
    };

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setHasAttemptedSubmit(true);
        const errs = validate();
        setErrors(errs);
        if (Object.keys(errs).length > 0) return;

        const baseData = {
            name: name.trim(),
            provider,
            baseUrl: baseUrl.trim(),
            apiKey: apiKey.trim(),
            authName: authName.trim(),
            description: description.trim(),
            status,
        };

        if (provider === 'PostgreSQL') {
            onSave({
                ...baseData,
                credentials: {
                    host: pgHost.trim(),
                    port: pgPort.trim(),
                    database: pgDatabase.trim(),
                    user: pgUser.trim(),
                    password: pgPassword,
                }
            });
        } else {
            onSave(baseData);
        }
    };

    const isEditMode = !!editingSource;
    const providerOptions = editingSource ? [...MARKET_PROVIDERS, ...DATABASE_PROVIDERS] : kind === 'database' ? DATABASE_PROVIDERS : MARKET_PROVIDERS;
    const title = isEditMode ? 'Edit data source' : kind === 'database' ? 'Add a database' : 'Add market data';

    return (
        <Modal isOpen={isOpen} onClose={onClose} title={title}>
            <form className="data-source-modal__form" onSubmit={handleSubmit} noValidate>
                {errors.form && (
                    <div className="data-source-modal__form-error" role="alert">
                        {errors.form}
                    </div>
                )}

                {/* Name + Provider row */}
                <div className="data-source-modal__row">
                    <div className="data-source-modal__field">
                        <label className="data-source-modal__label" htmlFor="ds-name">Name *</label>
                        <input
                            id="ds-name"
                            type="text"
                            className={`data-source-modal__input ${hasAttemptedSubmit && errors.name ? 'data-source-modal__input--error' : ''}`}
                            placeholder="e.g. My Alpha Vantage"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            autoComplete="off"
                        />
                        {hasAttemptedSubmit && errors.name && (
                            <span className="data-source-modal__error">{errors.name}</span>
                        )}
                    </div>
                    <div className="data-source-modal__field">
                        <label className="data-source-modal__label" htmlFor="ds-provider">Provider *</label>
                        <select
                            id="ds-provider"
                            className={`data-source-modal__select ${hasAttemptedSubmit && errors.provider ? 'data-source-modal__select--error' : ''}`}
                            value={provider}
                            onChange={(e) => setProvider(e.target.value)}
                            disabled={!!editingSource}
                        >
                            <option value="">Select provider</option>
                            {providerOptions.map(opt => (
                                <option key={opt} value={opt}>{opt}</option>
                            ))}
                        </select>
                        {hasAttemptedSubmit && errors.provider && (
                            <span className="data-source-modal__error">{errors.provider}</span>
                        )}
                    </div>
                </div>

                {provider === 'PostgreSQL' ? (
                    <>
                        <div className="data-source-modal__row">
                            <div className="data-source-modal__field">
                                <label className="data-source-modal__label">Host *</label>
                                <input
                                    type="text"
                                    className={`data-source-modal__input ${hasAttemptedSubmit && errors.pgHost ? 'data-source-modal__input--error' : ''}`}
                                    placeholder="localhost or db.example.com"
                                    value={pgHost}
                                    onChange={(e) => setPgHost(e.target.value)}
                                />
                                {hasAttemptedSubmit && errors.pgHost && <span className="data-source-modal__error">{errors.pgHost}</span>}
                            </div>
                            <div className="data-source-modal__field">
                                <label className="data-source-modal__label">Port</label>
                                <input
                                    type="text"
                                    className="data-source-modal__input"
                                    placeholder="5432"
                                    value={pgPort}
                                    onChange={(e) => setPgPort(e.target.value)}
                                />
                            </div>
                        </div>
                        <div className="data-source-modal__row">
                            <div className="data-source-modal__field">
                                <label className="data-source-modal__label">Database Name *</label>
                                <input
                                    type="text"
                                    className={`data-source-modal__input ${hasAttemptedSubmit && errors.pgDatabase ? 'data-source-modal__input--error' : ''}`}
                                    placeholder="postgres"
                                    value={pgDatabase}
                                    onChange={(e) => setPgDatabase(e.target.value)}
                                />
                                {hasAttemptedSubmit && errors.pgDatabase && <span className="data-source-modal__error">{errors.pgDatabase}</span>}
                            </div>
                        </div>
                        <div className="data-source-modal__row">
                            <div className="data-source-modal__field">
                                <label className="data-source-modal__label">Username *</label>
                                <input
                                    type="text"
                                    className={`data-source-modal__input ${hasAttemptedSubmit && errors.pgUser ? 'data-source-modal__input--error' : ''}`}
                                    placeholder="db_user"
                                    value={pgUser}
                                    onChange={(e) => setPgUser(e.target.value)}
                                />
                                {hasAttemptedSubmit && errors.pgUser && <span className="data-source-modal__error">{errors.pgUser}</span>}
                            </div>
                            <div className="data-source-modal__field">
                                <label className="data-source-modal__label">Password</label>
                                <input
                                    type="password"
                                    className="data-source-modal__input"
                                    placeholder="Enter password"
                                    value={pgPassword}
                                    onChange={(e) => setPgPassword(e.target.value)}
                                />
                            </div>
                        </div>
                    </>
                ) : (
                    <>
                    {provider && HINTS[provider] && <p className="data-source-modal__hint">{HINTS[provider]}</p>}
                    <div className="data-source-modal__row">
                        {provider === 'Custom API' && (
                        <div className="data-source-modal__field">
                            <label className="data-source-modal__label" htmlFor="ds-url">Address *</label>
                            <input
                                id="ds-url"
                                type="text"
                                className={`data-source-modal__input ${hasAttemptedSubmit && errors.baseUrl ? 'data-source-modal__input--error' : ''}`}
                                placeholder="https://api.example.com"
                                value={baseUrl}
                                onChange={(e) => setBaseUrl(e.target.value)}
                                autoComplete="off"
                            />
                            {hasAttemptedSubmit && errors.baseUrl && (
                                <span className="data-source-modal__error">{errors.baseUrl}</span>
                            )}
                        </div>
                        )}
                        {provider === 'Custom API' && (
                        <div className="data-source-modal__field">
                            <label className="data-source-modal__label" htmlFor="ds-authname">Key header</label>
                            <input id="ds-authname" type="text" className="data-source-modal__input" value={authName} onChange={(e) => setAuthName(e.target.value)} autoComplete="off" />
                        </div>
                        )}
                        <div className="data-source-modal__field">
                            <label className="data-source-modal__label" htmlFor="ds-apikey">API key{provider === 'Custom API' ? '' : ' *'}</label>
                            <input
                                id="ds-apikey"
                                type="password"
                                className="data-source-modal__input"
                                placeholder={editingSource ? 'Leave blank to keep the saved key' : 'Enter API key'}
                                value={apiKey}
                                onChange={(e) => setApiKey(e.target.value)}
                                autoComplete="off"
                            />
                            {hasAttemptedSubmit && errors.apiKey && <span className="data-source-modal__error">{errors.apiKey}</span>}
                        </div>
                    </div>
                    </>
                )}

                {/* Description */}
                <div className="data-source-modal__field">
                    <label className="data-source-modal__label" htmlFor="ds-description">Description</label>
                    <textarea
                        id="ds-description"
                        className="data-source-modal__textarea"
                        placeholder="Brief description of this data source..."
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        rows={2}
                    />
                </div>

                {/* Status Toggle */}
                <div className="data-source-modal__field">
                    <label className="data-source-modal__label">Status</label>
                    <div className="data-source-modal__status-toggle">
                        <button
                            type="button"
                            className={`data-source-modal__toggle ${status === 'active' ? 'data-source-modal__toggle--active' : ''}`}
                            onClick={() => setStatus(status === 'active' ? 'inactive' : 'active')}
                            aria-label={`Status: ${status}`}
                        />
                        <span className="data-source-modal__toggle-label">
                            {status === 'active' ? 'Active' : 'Inactive'}
                        </span>
                    </div>
                </div>

                {/* Actions */}
                <div className="data-source-modal__actions">
                    <Button type="button" variant="outline" onClick={onClose}>
                        Cancel
                    </Button>
                    <Button type="submit" variant="primary">
                        {isEditMode ? 'Update' : 'Connect and test'}
                    </Button>
                </div>
            </form>
        </Modal>
    );
};

export default DataSourceModal;
