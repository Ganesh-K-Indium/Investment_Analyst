import React, { useState, useEffect, useRef } from 'react';
import { Button } from '../../ui/Button';
import { Input } from '../../ui/Input';
import { Card } from '../../ui/Card';
import { edgarApi, type EdgarFiling } from '../../../services/edgarApi';
import { Form4IngestPanel } from './Form4IngestPanel';
import './ImportFileModal.css'; 

// 20-F / 6-K are the foreign private issuer counterparts of 10-K / (10-Q + 8-K). 20-F is on by
// default (low volume, and a domestic ticker simply has none); 6-K is opt-in because a foreign
// issuer files hundreds of them. Mirrors the backend's DEFAULT_FORM_TYPES.
const FORM_TYPE_OPTIONS = ['10-K', '10-Q', '8-K', '20-F', '6-K'];
const DEFAULT_FORM_TYPES = ['10-K', '10-Q', '8-K', '20-F'];
const ANNUAL_FORMS = ['10-K', '20-F'];

interface SecIngestSectionProps {
    onImported: (newFiles: string[]) => void;
    onShowToast: (msg: string) => void;
}

export const SecIngestSection: React.FC<SecIngestSectionProps> = ({
    onImported,
    onShowToast
}) => {
    // Step Management
    const [step, setStep] = useState<0 | 1 | 2 | 3>(0);
    // Which source was picked on step 0: the filing flow (steps 1-3) or the Form 4 insider-trading flow.
    const [source, setSource] = useState<'filings' | 'form4'>('filings');

    // Step 1 State: Configuration
    const [ticker, setTicker] = useState('');
    const [year, setYear] = useState('');
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');
    const [formTypes, setFormTypes] = useState<string[]>(DEFAULT_FORM_TYPES);
    
    // Step 2 State: Selection
    const [availableFilings, setAvailableFilings] = useState<EdgarFiling[]>([]);
    const [selectedAccessions, setSelectedAccessions] = useState<Set<string>>(new Set());
    
    // Global & Step 3 State: Processing
    const [isLoading, setIsLoading] = useState(false);
    const [isIngesting, setIsIngesting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [importProgress, setImportProgress] = useState<{ total: number; completed: number; currentStatus: string; currentFileProgress?: number } | null>(null);
    const [successfulResults, setSuccessfulResults] = useState<string[]>([]);
    const [ingestLogs, setIngestLogs] = useState<string[]>([]);
    const logContainerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (logContainerRef.current) {
            logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
        }
    }, [ingestLogs]);

    const resetState = () => {
        setStep(0);
        setSource('filings');
        setTicker('');
        setYear('');
        setStartDate('');
        setEndDate('');
        setFormTypes(DEFAULT_FORM_TYPES);
        setAvailableFilings([]);
        setSelectedAccessions(new Set());
        setError(null);
        setImportProgress(null);
        setIsLoading(false);
        setIsIngesting(false);
        setSuccessfulResults([]);
        setIngestLogs([]);
    };

    const toggleFormType = (type: string) => {
        setFormTypes(prev => 
            prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
        );
    };

    const toggleAccession = (accession: string) => {
        const newSet = new Set(selectedAccessions);
        if (newSet.has(accession)) {
            newSet.delete(accession);
        } else {
            newSet.add(accession);
        }
        setSelectedAccessions(newSet);
    };

    const fetchFilings = async () => {
        if (!ticker.trim()) {
            setError("Ticker is required.");
            return;
        }
        if (formTypes.length === 0) {
            setError("Select at least one form type.");
            return;
        }

        setIsLoading(true);
        setError(null);

        let effectiveStartDate = startDate;
        let effectiveEndDate = endDate;

        if (year && year.length === 4) {
            if (!effectiveStartDate) effectiveStartDate = `${year}-01-01`;
            if (!effectiveEndDate) effectiveEndDate = `${year}-12-31`;
        }

        try {
            const data = await edgarApi.listFilings(ticker.trim(), formTypes, effectiveStartDate, effectiveEndDate);
            setAvailableFilings(data.filings);
            // Auto-select all by default
            setSelectedAccessions(new Set(data.filings.map(f => f.accession)));
            setStep(2);
        } catch (err) {
            console.error(err);
            setError((err instanceof Error && err.message) || 'Failed to fetch SEC filings.');
        } finally {
            setIsLoading(false);
        }
    };

    const startIngestion = async () => {
        if (selectedAccessions.size === 0) {
            setError("Select at least one filing to ingest.");
            return;
        }

        setIsIngesting(true);
        setError(null);
        setStep(3);
        setImportProgress(null);
        setSuccessfulResults([]);
        setIngestLogs([]);

        let effectiveStartDate = startDate;
        let effectiveEndDate = endDate;

        if (year && year.length === 4) {
            if (!effectiveStartDate) effectiveStartDate = `${year}-01-01`;
            if (!effectiveEndDate) effectiveEndDate = `${year}-12-31`;
        }

        let successPaths: string[] = [];
        try {
            const stream = edgarApi.ingestFilingsStream(
                ticker.trim(),
                formTypes,
                Array.from(selectedAccessions),
                effectiveStartDate,
                effectiveEndDate
            );

            let totalFiles = selectedAccessions.size;
            let completedCount = 0;

            for await (const event of stream) {
                if (event.event === 'start') {
                    totalFiles = event.data.total;
                    setImportProgress({ total: totalFiles, completed: completedCount, currentStatus: 'Starting SEC Edgar fetch...', currentFileProgress: 0 });
                } else if (event.event === 'progress') {
                    let progress = 0.1;
                    const statusStr = (event.data.status || '').toLowerCase();
                    if (statusStr.includes('download')) progress = 0.3;
                    if (statusStr.includes('render')) progress = 0.4;
                    if (statusStr.includes('process') || statusStr.includes('ingest')) progress = 0.6;
                    setImportProgress({ total: totalFiles, completed: completedCount, currentStatus: `Processing: ${event.data.file} (${event.data.accession}) - ${event.data.message || event.data.status}`, currentFileProgress: progress });
                } else if (event.event === 'file_completed') {
                    completedCount++;
                    setImportProgress({ total: totalFiles, completed: completedCount, currentStatus: `Finished: ${event.data.form} (${event.data.accession})`, currentFileProgress: 0 });
                    if (event.data.success && event.data.pdf_path) {
                        successPaths.push(event.data.pdf_path);
                    }
                } else if (event.event === 'error') {
                    setError(event.data.detail || 'An error occurred during ingestion.');
                    break;
                } else if (event.event === 'completed') {
                    const results = event.data.results || [];
                    successPaths = results.filter(r => r.success && r.pdf_path).map(r => r.pdf_path as string);
                    break;
                } else if (event.event === 'log') {
                    setIngestLogs(prev => [...prev, event.data.message]);
                    setImportProgress(prev => {
                        if (!prev) return prev;
                        let newProgress = prev.currentFileProgress || 0.1;
                        const msg = (event.data.message || '').toLowerCase();
                        if (msg.includes('extracting') || msg.includes('parsing')) newProgress = Math.max(newProgress, 0.4);
                        if (msg.includes('chunking') || msg.includes('chunks')) newProgress = Math.max(newProgress, 0.6);
                        if (msg.includes('embedding')) newProgress = Math.max(newProgress, 0.8);
                        if (msg.includes('inserting') || msg.includes('saving')) newProgress = Math.max(newProgress, 0.9);
                        return { ...prev, currentFileProgress: newProgress };
                    });
                }
            }

            setSuccessfulResults(successPaths);
            if (successPaths.length > 0) {
                onImported(successPaths);
                onShowToast(`✓ Successfully processed ${successPaths.length} SEC filings`);
            } else if (!error) {
                setError('No new filings to ingest. All selected filings may have been already ingested.');
            }
        } catch (err) {
            console.error('Failed to ingest SEC filings:', err);
            setError((err instanceof Error && err.message) || 'Error ingesting SEC filings.');
        } finally {
            setIsIngesting(false);
            if (successPaths.length > 0) {
                setImportProgress(null);
            }
        }
    };

    const sectionHeader = (
        <div style={{ marginBottom: 'var(--space-4)' }}>
            <h2 style={{ fontSize: 'var(--font-size-xl)', fontWeight: 'var(--font-weight-semibold)', color: 'var(--color-text-primary)', margin: '0 0 var(--space-1) 0' }}>Secondary Research Ingestion</h2>
            <p style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-xs)', margin: 0 }}>Batch fetch and ingest documents directly from external primary data sources.</p>
        </div>
    );

    if (source === 'form4') {
        return (
            <div style={{ marginTop: 'var(--space-10)', paddingTop: 'var(--space-10)', borderTop: '1px solid var(--color-border)' }}>
                {sectionHeader}
                <Card style={{ padding: '0', overflow: 'hidden' }}>
                    <Form4IngestPanel onBack={resetState} onShowToast={onShowToast} />
                </Card>
            </div>
        );
    }

    return (
        <div style={{ marginTop: 'var(--space-10)', paddingTop: 'var(--space-10)', borderTop: '1px solid var(--color-border)' }}>
            {sectionHeader}

            <Card style={{ padding: '0', overflow: 'hidden' }}>
                <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                    
                    {/* Step 0: Source Selection */}
                    {step === 0 && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                            <h3 style={{ margin: 0, fontSize: '16px' }}>Select Data Source</h3>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '16px' }}>
                                <div 
                                    style={{ 
                                        padding: '24px', border: '1px solid var(--border-color)', borderRadius: '8px', 
                                        cursor: 'pointer', background: 'var(--bg-secondary)',
                                        transition: 'all 0.2s ease',
                                    }}
                                    onClick={() => { setStep(1); }}
                                    className="source-card-hover"
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                                        <div style={{ fontSize: '24px' }}>🏛️</div>
                                        <h4 style={{ margin: 0, fontSize: '16px', color: 'var(--color-text-primary)' }}>SEC EDGAR</h4>
                                    </div>
                                    <p style={{ fontSize: '13px', color: 'var(--color-text-secondary)', margin: 0 }}>Official company filings: 10-Ks, 10-Qs, 8-Ks, and 20-Fs / 6-Ks for foreign issuers.</p>
                                </div>
                                <div
                                    style={{
                                        padding: '24px', border: '1px solid var(--border-color)', borderRadius: '8px',
                                        cursor: 'pointer', background: 'var(--bg-secondary)',
                                        transition: 'all 0.2s ease',
                                    }}
                                    onClick={() => setSource('form4')}
                                    className="source-card-hover"
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                                        <div style={{ fontSize: '24px' }}>🕵️</div>
                                        <h4 style={{ margin: 0, fontSize: '16px', color: 'var(--color-text-primary)' }}>SEC Form 4 (Insider Trading)</h4>
                                    </div>
                                    <p style={{ fontSize: '13px', color: 'var(--color-text-secondary)', margin: 0 }}>Insider buy/sell transactions by officers, directors, and 10% owners. Powers the ALPHA alignment analysis. US-domestic issuers only.</p>
                                </div>
                                <div style={{ padding: '24px', border: '1px dashed var(--border-color)', borderRadius: '8px', opacity: 0.5, background: 'transparent' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                                        <div style={{ fontSize: '24px' }}>🌐</div>
                                        <h4 style={{ margin: 0, fontSize: '16px', color: 'var(--color-text-primary)' }}>More Sources</h4>
                                        <span style={{ fontSize: '10px', background: 'var(--bg-hover)', padding: '2px 6px', borderRadius: '4px' }}>Coming Soon</span>
                                    </div>
                                    <p style={{ fontSize: '13px', color: 'var(--color-text-secondary)', margin: 0 }}>Additional premium external data providers are on the roadmap.</p>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Step 1: Configuration */}
                    {step === 1 && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: 'var(--color-primary)', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', fontWeight: 'bold' }}>1</div>
                                <h3 style={{ margin: 0, fontSize: '16px' }}>Configure SEC Fetch</h3>
                            </div>
                            
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                <label style={{ fontSize: '14px', fontWeight: '500' }}>Ticker Symbol (Required)</label>
                                <Input 
                                    placeholder="e.g. AAPL" 
                                    value={ticker} 
                                    onChange={(e) => setTicker(e.target.value.toUpperCase())}
                                    maxLength={5}
                                />
                            </div>

                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                <label style={{ fontSize: '14px', fontWeight: '500' }}>Year (Optional)</label>
                                <Input 
                                    type="number"
                                    placeholder="e.g. 2023" 
                                    value={year} 
                                    onChange={(e) => setYear(e.target.value)}
                                    maxLength={4}
                                />
                            </div>

                            <div style={{ display: 'flex', gap: '16px' }}>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
                                    <label style={{ fontSize: '14px', fontWeight: '500' }}>Start Date (Optional)</label>
                                    <Input 
                                        type="date"
                                        value={startDate}
                                        onChange={(e) => setStartDate(e.target.value)}
                                    />
                                </div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
                                    <label style={{ fontSize: '14px', fontWeight: '500' }}>End Date (Optional)</label>
                                    <Input 
                                        type="date"
                                        value={endDate}
                                        onChange={(e) => setEndDate(e.target.value)}
                                    />
                                </div>
                            </div>

                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                <label style={{ fontSize: '14px', fontWeight: '500' }}>Form Types</label>
                                <div style={{ display: 'flex', gap: '12px' }}>
                                    {FORM_TYPE_OPTIONS.map(type => (
                                        <label key={type} style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                                            <input 
                                                type="checkbox" 
                                                checked={formTypes.includes(type)}
                                                onChange={() => toggleFormType(type)}
                                            />
                                            {type}
                                        </label>
                                    ))}
                                </div>
                                <p style={{ fontSize: '12px', color: 'var(--color-text-secondary)', margin: 0 }}>
                                    20-F / 6-K apply to foreign issuers (e.g. SNY, EMBJ, TM). 6-Ks are numerous — narrow the date range before selecting them.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Step 2: Selection */}
                    {step === 2 && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: 'var(--color-primary)', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', fontWeight: 'bold' }}>2</div>
                                    <h3 style={{ margin: 0, fontSize: '16px' }}>Select Filings</h3>
                                </div>
                                <Button variant="ghost" onClick={() => setStep(1)}>← Configure</Button>
                            </div>
                            
                            {availableFilings.length === 0 ? (
                                <div style={{ padding: '40px', textAlign: 'center', color: 'var(--color-text-secondary)', background: 'var(--bg-secondary)', borderRadius: '8px' }}>
                                    No filings found matching the criteria.
                                </div>
                            ) : (
                                <div style={{ overflowY: 'auto', maxHeight: '400px', border: '1px solid var(--border-color)', borderRadius: '8px', boxShadow: 'var(--shadow-sm)' }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '14px' }}>
                                        <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-primary)', zIndex: 1, borderBottom: '1px solid var(--border-color)' }}>
                                            <tr>
                                                <th style={{ padding: '12px 16px', textAlign: 'left', width: '40px' }}>
                                                    <input 
                                                        type="checkbox" 
                                                        checked={selectedAccessions.size === availableFilings.length && availableFilings.length > 0}
                                                        onChange={(e) => {
                                                            if (e.target.checked) {
                                                                setSelectedAccessions(new Set(availableFilings.map(f => f.accession)));
                                                            } else {
                                                                setSelectedAccessions(new Set());
                                                            }
                                                        }}
                                                    />
                                                </th>
                                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: '12px', fontWeight: '600', color: 'var(--color-text-tertiary)', textTransform: 'uppercase' }}>Form Type</th>
                                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: '12px', fontWeight: '600', color: 'var(--color-text-tertiary)', textTransform: 'uppercase' }}>Period Date</th>
                                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: '12px', fontWeight: '600', color: 'var(--color-text-tertiary)', textTransform: 'uppercase' }}>Year</th>
                                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: '12px', fontWeight: '600', color: 'var(--color-text-tertiary)', textTransform: 'uppercase' }}>Report</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {availableFilings.map(filing => (
                                                <tr key={filing.accession} style={{ borderBottom: '1px solid var(--border-color)', transition: 'background-color 0.2s ease' }} className="filing-row-hover">
                                                    <td style={{ padding: '12px 16px' }}>
                                                        <input 
                                                            type="checkbox" 
                                                            checked={selectedAccessions.has(filing.accession)}
                                                            onChange={() => toggleAccession(filing.accession)}
                                                        />
                                                    </td>
                                                    <td style={{ padding: '12px 16px' }}>
                                                        <span style={{ 
                                                            padding: '4px 8px', 
                                                            borderRadius: '4px', 
                                                            fontSize: '12px', 
                                                            fontWeight: '600',
                                                            backgroundColor: ANNUAL_FORMS.includes(filing.form) ? 'var(--color-indigo-light)' : filing.form === '10-Q' ? 'var(--color-info-light)' : 'var(--color-success-light)',
                                                            color: ANNUAL_FORMS.includes(filing.form) ? 'var(--color-indigo)' : filing.form === '10-Q' ? 'var(--color-info)' : 'var(--color-success)'
                                                        }}>
                                                            {filing.form}
                                                        </span>
                                                    </td>
                                                    <td style={{ padding: '12px 16px', color: 'var(--color-text-primary)', fontWeight: '500' }}>{filing.period_end_date || 'N/A'}</td>
                                                    <td style={{ padding: '12px 16px', color: 'var(--color-text-secondary)' }}>{filing.period_end_date ? filing.period_end_date.substring(0, 4) : filing.filing_date ? filing.filing_date.substring(0, 4) : 'N/A'}</td>
                                                    <td style={{ padding: '12px 16px', color: 'var(--color-text-secondary)', fontSize: '13px' }}>
                                                        <a 
                                                            href={filing.url}
                                                            target="_blank"
                                                            rel="noreferrer"
                                                            style={{ color: 'var(--color-primary)', textDecoration: 'underline', cursor: 'pointer' }}
                                                            onClick={(e) => {
                                                                e.preventDefault();
                                                                window.open(filing.url, 'sec_filing', 'width=800,height=800,scrollbars=yes');
                                                            }}
                                                            title="View SEC Filing"
                                                        >
                                                            {filing.accession}
                                                        </a>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Step 3: Ingestion Progress */}
                    {step === 3 && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: 'var(--color-primary)', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', fontWeight: 'bold' }}>3</div>
                                <h3 style={{ margin: 0, fontSize: '16px' }}>Ingestion Progress</h3>
                            </div>
                            
                            {importProgress && (
                                <div style={{ padding: '16px', backgroundColor: 'var(--color-bg-secondary)', borderRadius: '8px', border: '1px solid var(--border-color)', marginBottom: '16px' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                                        <span style={{ fontWeight: '500' }}>Processing Filings</span>
                                        <span style={{ color: 'var(--color-text-secondary)' }}>{importProgress.completed} / {importProgress.total}</span>
                                    </div>
                                    
                                    <div style={{ width: '100%', height: '8px', backgroundColor: 'var(--color-bg-tertiary)', borderRadius: '4px', overflow: 'hidden', marginBottom: '12px' }}>
                                        <div 
                                            style={{ 
                                                height: '100%', 
                                                backgroundColor: 'var(--color-primary)', 
                                                width: `${importProgress.total > 0 ? Math.min(100, Math.round(((importProgress.completed + (importProgress.currentFileProgress || 0)) / importProgress.total) * 100)) : 0}%`,
                                                transition: 'width 0.3s ease'
                                            }} 
                                        />
                                    </div>
                                    <div style={{ fontSize: '13px', color: 'var(--color-text-secondary)', wordBreak: 'break-all' }}>
                                        {importProgress.currentStatus}
                                    </div>
                                </div>
                            )}

                            {ingestLogs.length > 0 && (
                                <div style={{ marginBottom: '16px' }}>
                                    <div style={{ fontSize: '13px', fontWeight: '500', marginBottom: '8px', color: 'var(--color-text-secondary)' }}>Live Ingestion Logs</div>
                                    <div 
                                        ref={logContainerRef}
                                        style={{
                                            backgroundColor: 'var(--bg-secondary)',
                                            color: 'var(--color-text-secondary)',
                                            fontFamily: 'monospace',
                                            fontSize: '11px',
                                            padding: '12px',
                                            borderRadius: '6px',
                                            height: '240px',
                                            overflowY: 'auto',
                                            border: '1px solid var(--color-border)',
                                            boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.05)'
                                        }}
                                    >
                                        {ingestLogs.map((log, idx) => (
                                            <div key={idx} style={{ marginBottom: '4px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                                {log}
                                            </div>
                                        ))}
                                        {isIngesting && (
                                            <div style={{ display: 'flex', alignItems: 'center', marginTop: '8px', color: 'var(--color-text-tertiary)' }}>
                                                <span className="spinner spinner--small" style={{ marginRight: '8px', borderColor: 'var(--color-border)', borderTopColor: 'var(--color-primary)' }}></span>
                                                Processing...
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}

                            {!isIngesting && successfulResults.length > 0 && !error && (
                                <div style={{ padding: '16px', background: 'var(--color-success-surface)', color: 'var(--color-success-dark)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                                        <polyline points="22 4 12 14.01 9 11.01"></polyline>
                                    </svg>
                                    Successfully ingested {successfulResults.length} filings.
                                </div>
                            )}
                        </div>
                    )}

                    {/* Error Display */}
                    {error && (
                        <div className="import-modal__error" style={{ marginTop: '8px' }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <circle cx="12" cy="12" r="10" />
                                <line x1="12" y1="8" x2="12" y2="12" />
                                <line x1="12" y1="16" x2="12.01" y2="16" />
                            </svg>
                            <span>{error}</span>
                        </div>
                    )}
                </div>

                {/* Footer */}
                {step > 0 && (
                    <div style={{ borderTop: '1px solid var(--border-color)', padding: '16px 24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg-secondary)' }}>
                        <div style={{ color: 'var(--color-text-secondary)', fontSize: '14px' }}>
                            {step === 1 && `Source: SEC EDGAR`}
                            {step === 2 && `${selectedAccessions.size} filings selected`}
                            {step === 3 && !isIngesting && successfulResults.length > 0 && `${successfulResults.length} filings ingested successfully`}
                        </div>
                        <div style={{ display: 'flex', gap: '12px' }}>
                            {!isIngesting && (
                                <Button
                                    variant="secondary"
                                    onClick={resetState}
                                >
                                    {step === 3 && !error ? 'Ingest More' : 'Change Source'}
                                </Button>
                            )}

                            {step === 1 && (
                                <Button
                                    variant="primary"
                                    disabled={!ticker || formTypes.length === 0}
                                    loading={isLoading}
                                    onClick={fetchFilings}
                                >
                                    Fetch Filings
                                </Button>
                            )}

                            {step === 2 && (
                                <Button
                                    variant="primary"
                                    disabled={selectedAccessions.size === 0}
                                    onClick={startIngestion}
                                >
                                    Start Batch Ingestion
                                </Button>
                            )}

                            {step === 3 && !isIngesting && error && (
                                <Button
                                    variant="primary"
                                    onClick={startIngestion}
                                >
                                    Retry Failed
                                </Button>
                            )}
                        </div>
                    </div>
                )}
            </Card>
        </div>
    );
};

export default SecIngestSection;
