import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout } from '../components/layout/PageLayout';
import { PageHeader } from '../components/layout/PageHeader';
import { Button } from '../components/ui/Button';
import { Modal } from '../components/ui/Modal';
import { DataSourceModal } from '../features/dataSources/DataSourceModal';
import {
    getDataSources,
    type DataSource,
} from '../features/dataSources/dataSourceStore';
import { useAuth } from '../context/useAuth';
import { integrationApi } from '../services/integrationApi';
import { dataSourcesApi, type DataProvider } from '../services/dataSourcesApi';
import { FileSourcesSection } from '../features/dataSources/FileSourcesSection';
import { SecIngestSection } from '../components/project/secondary-research/SecIngestSection';
import { AnalystNotesSection } from '../features/notes/AnalystNotesSection';
import './DataSources.css';

export const DataSources: React.FC = () => {
    const { user } = useAuth();
    const navigate = useNavigate();
    const builtin = useCallback(() => getDataSources().filter(d => d.isDefault), []);       // Yahoo Finance is built in; every other provider lives on the server
    const [dataSources, setDataSources] = useState<DataSource[]>(builtin);
    const [providers, setProviders] = useState<DataProvider[]>([]);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [kind, setKind] = useState<'market' | 'database'>('market');
    const [chooserOpen, setChooserOpen] = useState(false);
    const [addFileSignal, setAddFileSignal] = useState(0);
    const [editingSource, setEditingSource] = useState<DataSource | null>(null);
    const [toastMessage, setToastMessage] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(false);

    const showToast = useCallback((message: string) => {
        setToastMessage(message);
        setTimeout(() => setToastMessage(null), 3000);
    }, []);

    const loadAllDataSources = useCallback(async () => {
        try {
            setIsLoading(true);
            const localSources = builtin();
            
            let remoteSources: DataSource[] = [];
            if (user) {
                const integrations = await integrationApi.getUserIntegrations(user.id.toString());
                const postgresIntegrations = integrations.filter(i => i.vendor === 'postgresql');
                remoteSources = postgresIntegrations.map(i => ({
                    id: i.id.toString(),
                    name: i.name,
                    provider: 'PostgreSQL',
                    baseUrl: i.url || '',
                    apiKey: '', // Hidden by API
                    description: i.description || '',
                    status: i.status === 'active' ? 'active' : 'inactive',
                    isDefault: false,
                    credentials: i.credentials_summary || {},
                }));
            }
            
            const rest = await dataSourcesApi.list();
            setProviders(rest.providers);
            const restSources: DataSource[] = rest.sources.map(s => ({
                id: s.id, name: s.name, provider: s.provider_label, baseUrl: s.base_url || '', apiKey: '', description: s.description || '',
                status: s.enabled && !s.last_error ? 'active' : 'inactive', isDefault: false, hasKey: s.has_key, lastError: s.last_error,
            }));
            setDataSources([...localSources, ...restSources, ...remoteSources]);
        } catch (err) {
            console.error('Failed to load remote data sources', err);
            setDataSources(builtin());
        } finally {
            setIsLoading(false);
        }
    }, [user, builtin]);

    React.useEffect(() => {
        loadAllDataSources();
    }, [loadAllDataSources]);

    const choose = (what: 'files' | 'market' | 'database') => {
        setChooserOpen(false);
        if (what === 'files') { setAddFileSignal(n => n + 1); return; }
        setKind(what);
        setEditingSource(null);
        setIsModalOpen(true);
    };

    const openEditModal = (ds: DataSource) => {
        setEditingSource(ds);
        setIsModalOpen(true);
    };

    const handleSave = async (data: Omit<DataSource, 'id' | 'isDefault'>) => {
        try {
            if (data.provider === 'PostgreSQL') {
                if (!user) throw new Error('You must be logged in to save remote data sources');
                if (editingSource && editingSource.provider === 'PostgreSQL') {
                    await integrationApi.updateIntegration(parseInt(editingSource.id), {
                        name: data.name,
                        description: data.description,
                        status: data.status === 'active' ? 'active' : 'disconnected',
                        credentials: data.credentials,
                    });
                    showToast('✓ PostgreSQL connection updated');
                } else {
                    await integrationApi.createIntegration({
                        user_id: user.id.toString(),
                        vendor: 'postgresql',
                        name: data.name,
                        description: data.description,
                        credentials: data.credentials || {},
                    });
                    showToast('✓ PostgreSQL connection added');
                }
            } else {
                const pid = providers.find(p => p.label === data.provider)?.id ?? '';
                if (editingSource) {
                    await dataSourcesApi.patch(editingSource.id, {
                        name: data.name, description: data.description, enabled: data.status === 'active',
                        ...(data.apiKey ? { api_key: data.apiKey, auth_name: data.authName } : {}),
                    });
                    showToast('✓ Data source updated');
                } else {
                    if (!pid) throw new Error('Choose a provider.');
                    await dataSourcesApi.add({ provider: pid, name: data.name, api_key: data.apiKey || undefined, base_url: data.baseUrl || undefined, auth_name: data.authName, description: data.description || undefined });
                    showToast('✓ Connected. The quant agent can use it now.');
                }
            }
            await loadAllDataSources();
            setIsModalOpen(false);
            setEditingSource(null);
        } catch (err) {
            showToast(`✗ ${err instanceof Error ? err.message : 'Operation failed'}`);
        }
    };

    const handleDelete = async (ds: DataSource) => {
        if (ds.isDefault) return;
        if (!confirm(`Are you sure you want to delete "${ds.name}"?`)) return;
        try {
            if (ds.provider === 'PostgreSQL') {
                await integrationApi.deleteIntegration(parseInt(ds.id));
                showToast('✓ PostgreSQL connection deleted');
            } else {
                await dataSourcesApi.remove(ds.id);
                showToast('✓ Data source deleted');
            }
            await loadAllDataSources();
        } catch (err) {
            showToast(`✗ ${err instanceof Error ? err.message : 'Delete failed'}`);
        }
    };

    const marketSources = dataSources.filter(d => d.provider !== 'PostgreSQL');
    const databases = dataSources.filter(d => d.provider === 'PostgreSQL');

    const renderTable = (list: DataSource[], emptyText: string) => list.length === 0 ? (
        <p className="ds-empty">{emptyText}</p>
    ) : (
        <div className="data-sources-table">
            <table>
                <thead>
                    <tr><th>Name</th><th>Details</th><th>Status</th><th><span className="ds-sr-only">Actions</span></th></tr>
                </thead>
                <tbody>
                    {list.map(ds => (
                        <tr key={ds.id}>
                            <td>
                                <div className="data-sources-table__name">
                                    <span className="data-sources-table__name-text">{ds.name}</span>
                                    {ds.isDefault && <span className="data-sources-table__default-badge">Built in</span>}
                                </div>
                                <div className="data-sources-table__provider">{ds.provider}</div>
                            </td>
                            <td><span className="data-sources-table__description" title={ds.description}>{ds.description || '—'}</span></td>
                            <td>
                                <span className={`data-sources-table__status data-sources-table__status--${ds.status === 'active' ? 'active' : ds.lastError ? 'attention' : 'inactive'}`} title={ds.lastError || undefined}>
                                    {ds.status === 'active' ? '● Active' : ds.lastError ? '! Needs attention' : '○ Inactive'}
                                </span>
                            </td>
                            <td>
                                <div className="data-sources-table__actions">
                                    <button className="data-sources-table__action-btn" onClick={() => openEditModal(ds)} title="Edit" aria-label={`Edit ${ds.name}`}>
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                        </svg>
                                    </button>
                                    <button className="data-sources-table__action-btn data-sources-table__action-btn--danger" onClick={() => handleDelete(ds)} disabled={ds.isDefault}
                                        title={ds.isDefault ? 'Built-in source cannot be deleted' : 'Delete'} aria-label={`Delete ${ds.name}`}>
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                            <polyline points="3 6 5 6 21 6" />
                                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                        </svg>
                                    </button>
                                </div>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );

    return (
        <>
        <PageLayout className="data-sources-page">
            <PageHeader
                title="Data Sources"
                description="Connect the files, market data and databases your research uses."
                actions={
                    <Button variant="primary" onClick={() => setChooserOpen(true)}
                        icon={<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>}>
                        Add data source
                    </Button>
                }
            />

                <section className="ds-section" aria-labelledby="ds-files">
                    <header className="ds-section__head">
                        <h2 id="ds-files">Files and documents</h2>
                        <p>SharePoint, Google Drive, OneDrive, Confluence, Azure Blob, S3 and SFTP. Import their files from My documents.</p>
                    </header>
                    <FileSourcesSection addSignal={addFileSignal} />
                </section>

                <section className="ds-section" aria-labelledby="ds-market">
                    <header className="ds-section__head">
                        <h2 id="ds-market">Market data</h2>
                        <p>Price, fundamentals and news providers the quant agent can query.</p>
                    </header>
                    {isLoading ? <p className="ds-empty">Loading…</p> : renderTable(marketSources, 'No market data providers connected.')}
                </section>

                <section className="ds-section" aria-labelledby="ds-db">
                    <header className="ds-section__head">
                        <h2 id="ds-db">Databases</h2>
                        <p>PostgreSQL databases the quant agent can run read-only queries against.</p>
                    </header>
                    {isLoading ? <p className="ds-empty">Loading…</p> : renderTable(databases, 'No databases connected.')}
                </section>

                {/* Content you add yourself: handwritten or typed notes, and SEC filings */}
                <AnalystNotesSection mode="add" onShowToast={showToast} onViewNotes={() => navigate('/notes')} onViewDocuments={() => navigate('/documents/mine')} />

                {user?.role === 'fund_manager' && (
                    <SecIngestSection
                        onImported={(files) => console.log('Imported SEC filings:', files)}
                        onShowToast={showToast}
                    />
                )}
        </PageLayout>

            <Modal isOpen={chooserOpen} onClose={() => setChooserOpen(false)} title="What do you want to connect?">
                <div className="ds-chooser">
                    <button type="button" className="ds-chooser__option" onClick={() => choose('files')}>
                        <strong>Files and documents</strong>
                        <span>SharePoint, Drive, OneDrive, Confluence, Azure Blob, S3, SFTP</span>
                    </button>
                    <button type="button" className="ds-chooser__option" onClick={() => choose('market')}>
                        <strong>Market data</strong>
                        <span>Alpha Vantage, Polygon.io, Finnhub, Twelve Data, Financial Modeling Prep, your own API</span>
                    </button>
                    <button type="button" className="ds-chooser__option" onClick={() => choose('database')}>
                        <strong>A database</strong>
                        <span>PostgreSQL</span>
                    </button>
                </div>
            </Modal>

            <DataSourceModal
                isOpen={isModalOpen}
                onClose={() => { setIsModalOpen(false); setEditingSource(null); }}
                onSave={handleSave}
                editingSource={editingSource}
                kind={kind}
                existingNames={dataSources.map(ds => ds.name)}
            />

            {toastMessage && (
                <div className="data-sources-page__toast">{toastMessage}</div>
            )}
        </>
    );
};

export default DataSources;
