import React, { useState, useEffect, useRef } from 'react';
import { Button } from '../../ui/Button';
import { Input } from '../../ui/Input';
import { form4Api, type Form4IngestResponse, type Form4BatchIngestResponse } from '../../../services/form4Api';
import { foreignCompanies } from '../../../data/foreignCompanies';
import './ImportFileModal.css';

const TICKER_PATTERN = /^[A-Z]{1,5}$/;
const MAX_TICKERS = 10;
const FOREIGN_TICKERS = new Set(foreignCompanies.map(c => c.ticker));

interface Progress {
    count: number;      // tickers in this run
    index: number;      // 0-based index of the ticker being processed
    ticker: string;
    processed: number;  // filings processed for the current ticker
    total: number;      // filings found for the current ticker
    status: string;
}

interface Form4IngestPanelProps {
    onBack: () => void;
    onShowToast: (msg: string) => void;
}

const parseTickers = (raw: string): string[] =>
    Array.from(new Set(raw.toUpperCase().split(/[\s,;]+/).filter(Boolean)));

/**
 * Form 4 (insider trading) ingestion for the Data Sources page. Streams progress and live logs
 * from POST /form4/ingest/stream. Foreign private issuers are excluded up front — they are
 * exempt from Section 16 and file no Form 4 (the backend also skips them with a message).
 */
export const Form4IngestPanel: React.FC<Form4IngestPanelProps> = ({ onBack, onShowToast }) => {
    const [tickerInput, setTickerInput] = useState('');
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');

    const [isIngesting, setIsIngesting] = useState(false);
    const [hasStarted, setHasStarted] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [progress, setProgress] = useState<Progress | null>(null);
    const [completedTickers, setCompletedTickers] = useState(0);
    const [results, setResults] = useState<Form4IngestResponse[]>([]);
    const [totals, setTotals] = useState<Form4BatchIngestResponse['totals'] | null>(null);
    const [logs, setLogs] = useState<string[]>([]);

    const logContainerRef = useRef<HTMLDivElement>(null);
    const abortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        if (logContainerRef.current) {
            logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
        }
    }, [logs]);

    // Closing the page mid-run drops the stream; re-running is safe (dedup by accession number).
    useEffect(() => () => abortRef.current?.abort(), []);

    const tickers = parseTickers(tickerInput);
    const invalidTickers = tickers.filter(t => !TICKER_PATTERN.test(t));
    const foreignTickers = tickers.filter(t => FOREIGN_TICKERS.has(t));
    const ingestTickers = tickers.filter(t => TICKER_PATTERN.test(t) && !FOREIGN_TICKERS.has(t));
    const tooMany = ingestTickers.length > MAX_TICKERS;
    const dateOrderInvalid = !!startDate && !!endDate && startDate > endDate;
    const canStart = ingestTickers.length > 0 && invalidTickers.length === 0 && !tooMany && !dateOrderInvalid;

    const resetForNewRun = () => {
        setHasStarted(false);
        setError(null);
        setProgress(null);
        setCompletedTickers(0);
        setResults([]);
        setTotals(null);
        setLogs([]);
    };

    const startIngestion = async () => {
        if (!canStart) return;

        resetForNewRun();
        setHasStarted(true);
        setIsIngesting(true);

        const controller = new AbortController();
        abortRef.current = controller;
        let finished = false;

        try {
            const stream = form4Api.ingestStream(
                {
                    tickers: ingestTickers,
                    start_date: startDate || undefined,
                    end_date: endDate || undefined,
                },
                controller.signal,
            );

            for await (const evt of stream) {
                switch (evt.event) {
                    case 'start':
                        setProgress({
                            count: evt.data.total, index: 0, ticker: evt.data.tickers[0] ?? '',
                            processed: 0, total: 0, status: 'Connecting to SEC EDGAR...',
                        });
                        break;
                    case 'ticker_start':
                        setProgress({
                            count: evt.data.count, index: evt.data.index, ticker: evt.data.ticker,
                            processed: 0, total: 0, status: `${evt.data.ticker}: fetching Form 4 filing list...`,
                        });
                        break;
                    case 'fetched':
                        setProgress(prev => prev && {
                            ...prev, total: evt.data.total, processed: 0,
                            status: evt.data.total > 0
                                ? `${evt.data.ticker}: found ${evt.data.total} filings`
                                : `${evt.data.ticker}: no filings in range`,
                        });
                        break;
                    case 'progress':
                        setProgress(prev => prev && {
                            ...prev, processed: evt.data.processed, total: evt.data.total,
                            status: `${evt.data.ticker}: processed ${evt.data.processed} of ${evt.data.total} filings`,
                        });
                        break;
                    case 'ticker_completed':
                        setCompletedTickers(n => n + 1);
                        setResults(prev => [...prev, evt.data.result]);
                        break;
                    case 'log':
                        setLogs(prev => [...prev, evt.data.message]);
                        break;
                    case 'completed':
                        finished = true;
                        setResults(evt.data.results);
                        setTotals(evt.data.totals);
                        onShowToast(`✓ Form 4 ingestion complete: ${evt.data.totals.transactions_saved_total} new transactions saved`);
                        break;
                    case 'error':
                        finished = true;
                        setError(evt.data.detail || 'An error occurred during Form 4 ingestion.');
                        break;
                }
            }
            if (!finished && !controller.signal.aborted) {
                setError('Connection closed before ingestion finished. Re-run to resume — filings already saved are skipped.');
            }
        } catch (err) {
            if (!(err instanceof Error && err.name === 'AbortError')) {
                console.error('Form 4 ingestion failed:', err);
                setError((err instanceof Error && err.message) || 'Error ingesting Form 4 filings.');
            }
        } finally {
            setIsIngesting(false);
            setProgress(null);
        }
    };

    const overallPercent = progress && progress.count > 0
        ? Math.min(100, Math.round(((progress.index + (progress.total > 0 ? progress.processed / progress.total : 0)) / progress.count) * 100))
        : 0;
    const done = hasStarted && !isIngesting && totals !== null && !error;

    return (
        <>
            <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                {/* Configuration */}
                {!hasStarted && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: 'var(--color-primary)', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', fontWeight: 'bold' }}>1</div>
                            <h3 style={{ margin: 0, fontSize: '16px' }}>Configure Form 4 Ingestion</h3>
                        </div>

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            <label style={{ fontSize: '14px', fontWeight: '500' }}>Ticker Symbols (Required)</label>
                            <Input
                                placeholder="e.g. NVDA, AAPL, MSFT"
                                value={tickerInput}
                                onChange={(e) => setTickerInput(e.target.value.toUpperCase())}
                            />
                            <p style={{ fontSize: '12px', color: 'var(--color-text-secondary)', margin: 0 }}>
                                Separate multiple tickers with commas or spaces (up to {MAX_TICKERS}). Insider transactions are saved to the database that feeds the ALPHA alignment analysis.
                            </p>
                            {invalidTickers.length > 0 && (
                                <p style={{ fontSize: '12px', color: 'var(--color-error, #d33)', margin: 0 }}>
                                    Invalid ticker{invalidTickers.length > 1 ? 's' : ''}: {invalidTickers.join(', ')} — use 1–5 letters.
                                </p>
                            )}
                            {tooMany && (
                                <p style={{ fontSize: '12px', color: 'var(--color-error, #d33)', margin: 0 }}>
                                    Too many tickers — ingest at most {MAX_TICKERS} at a time.
                                </p>
                            )}
                            {foreignTickers.length > 0 && (
                                <p style={{ fontSize: '12px', color: 'var(--color-warning-dark, #b26a00)', margin: 0 }}>
                                    {foreignTickers.join(', ')} {foreignTickers.length > 1 ? 'are foreign private issuers' : 'is a foreign private issuer'} — exempt from Section 16, so no Form 4 exists. {foreignTickers.length > 1 ? 'They' : 'It'} will be skipped; ownership data is in the 20-F (Items 6 and 7).
                                </p>
                            )}
                        </div>

                        <div style={{ display: 'flex', gap: '16px' }}>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
                                <label style={{ fontSize: '14px', fontWeight: '500' }}>Start Date (Optional)</label>
                                <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
                                <label style={{ fontSize: '14px', fontWeight: '500' }}>End Date (Optional)</label>
                                <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                            </div>
                        </div>
                        {dateOrderInvalid && (
                            <p style={{ fontSize: '12px', color: 'var(--color-error, #d33)', margin: 0 }}>Start date must be on or before the end date.</p>
                        )}
                        <p style={{ fontSize: '12px', color: 'var(--color-text-secondary)', margin: 0 }}>
                            Leave the dates blank to use the backend defaults (a recent window up to today). Re-running is safe — filings already saved are skipped.
                        </p>
                    </div>
                )}

                {/* Progress / results */}
                {hasStarted && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: 'var(--color-primary)', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', fontWeight: 'bold' }}>2</div>
                            <h3 style={{ margin: 0, fontSize: '16px' }}>Ingestion Progress</h3>
                        </div>

                        {progress && (
                            <div style={{ padding: '16px', backgroundColor: 'var(--color-bg-secondary)', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                                    <span style={{ fontWeight: '500' }}>Ingesting Form 4 filings</span>
                                    <span style={{ color: 'var(--color-text-secondary)' }}>
                                        Ticker {Math.min(completedTickers + 1, progress.count)} / {progress.count}
                                    </span>
                                </div>
                                <div style={{ width: '100%', height: '8px', backgroundColor: 'var(--color-bg-tertiary)', borderRadius: '4px', overflow: 'hidden', marginBottom: '12px' }}>
                                    <div style={{ height: '100%', backgroundColor: 'var(--color-primary)', width: `${overallPercent}%`, transition: 'width 0.3s ease' }} />
                                </div>
                                <div style={{ fontSize: '13px', color: 'var(--color-text-secondary)', wordBreak: 'break-all' }}>{progress.status}</div>
                            </div>
                        )}

                        {logs.length > 0 && (
                            <div>
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
                                        boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.05)',
                                    }}
                                >
                                    {logs.map((log, idx) => (
                                        <div key={idx} style={{ marginBottom: '4px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{log}</div>
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

                        {results.length > 0 && (
                            <div style={{ border: '1px solid var(--border-color)', borderRadius: '8px', overflow: 'hidden' }}>
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                                    <thead>
                                        <tr style={{ background: 'var(--bg-secondary)', textAlign: 'left' }}>
                                            <th style={{ padding: '8px 12px' }}>Ticker</th>
                                            <th style={{ padding: '8px 12px' }}>Filings found</th>
                                            <th style={{ padding: '8px 12px' }}>New transactions</th>
                                            <th style={{ padding: '8px 12px' }}>Already in DB</th>
                                            <th style={{ padding: '8px 12px' }}>Failed</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {results.map(r => (
                                            <tr key={r.ticker} style={{ borderTop: '1px solid var(--border-color)' }}>
                                                <td style={{ padding: '8px 12px', fontWeight: 500 }}>
                                                    {r.ticker}
                                                    {r.total_url_fetched === 0 && r.message && (
                                                        <div style={{ fontWeight: 400, fontSize: '12px', color: 'var(--color-text-secondary)' }}>{r.message}</div>
                                                    )}
                                                </td>
                                                <td style={{ padding: '8px 12px' }}>{r.total_url_fetched}</td>
                                                <td style={{ padding: '8px 12px' }}>{r.transactions_saved_total}</td>
                                                <td style={{ padding: '8px 12px' }}>{r.skipped_already_in_db}</td>
                                                <td style={{ padding: '8px 12px' }}>{r.failed}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}

                        {done && totals && (
                            <div style={{ padding: '16px', background: 'var(--color-success-surface)', color: 'var(--color-success-dark)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                                    <polyline points="22 4 12 14.01 9 11.01"></polyline>
                                </svg>
                                Saved {totals.transactions_saved_total} new insider transactions from {totals.total_url_fetched} filings.
                            </div>
                        )}
                    </div>
                )}

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
            <div style={{ borderTop: '1px solid var(--border-color)', padding: '16px 24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg-secondary)' }}>
                <div style={{ color: 'var(--color-text-secondary)', fontSize: '14px' }}>
                    {!hasStarted && 'Source: SEC EDGAR — Form 4 (insider trading)'}
                    {isIngesting && `${completedTickers} of ${progress?.count ?? ingestTickers.length} tickers done`}
                </div>
                <div style={{ display: 'flex', gap: '12px' }}>
                    {!isIngesting && (
                        <Button variant="secondary" onClick={() => { resetForNewRun(); onBack(); }}>
                            {done ? 'Ingest More' : 'Change Source'}
                        </Button>
                    )}
                    {!hasStarted && (
                        <Button variant="primary" disabled={!canStart} onClick={startIngestion}>
                            Start Ingestion
                        </Button>
                    )}
                    {!isIngesting && hasStarted && error && (
                        <Button variant="primary" onClick={startIngestion}>
                            Retry
                        </Button>
                    )}
                </div>
            </div>
        </>
    );
};

export default Form4IngestPanel;
