import { useState } from 'react';
import { Modal, Button } from '../../ui';
import { form4Api } from '../../../services/form4Api';
import type { Company } from '../../../data/fortune500Companies';
import { isForeignCompany } from '../../../data/foreignCompanies';
import './Form4ImportModal.css';

interface Form4ImportModalProps {
    isOpen: boolean;
    onClose: () => void;
    companies: Company[];
    onShowToast: (message: string) => void;
}

/** Fetches and ingests SEC EDGAR Form 4 insider-trading filings for a ticker. */
export function Form4ImportModal({ isOpen, onClose, companies, onShowToast }: Form4ImportModalProps) {
    const [ticker, setTicker] = useState('');
    const [startDate, setStartDate] = useState('2025-01-01');
    const [isIngesting, setIsIngesting] = useState(false);

    const handleIngest = async () => {
        if (!ticker) {
            onShowToast('⚠️ Please select a ticker.');
            return;
        }
        if (companies.some(c => c.ticker === ticker && isForeignCompany(c))) {
            onShowToast(`ℹ️ ${ticker} is a foreign private issuer — exempt from Section 16, so no Form 4 filings exist.`);
            return;
        }
        setIsIngesting(true);
        try {
            const result = await form4Api.ingest({ ticker, start_date: startDate || undefined });
            onShowToast(
                result.total_url_fetched === 0 && result.message
                    ? `ℹ️ ${result.message}`
                    : `✓ Form 4 ingestion complete: ${result.transactions_saved_total} saved, ${result.skipped_already_in_db} duplicates skipped.`
            );
            onClose();
        } catch (err) {
            console.error('Form4 ingestion failed:', err);
            onShowToast(`⚠️ Form 4 ingestion failed: ${(err instanceof Error && err.message) || 'Unknown error'}`);
        } finally {
            setIsIngesting(false);
        }
    };

    if (!isOpen) return null;

    return (
        <Modal
            isOpen
            onClose={onClose}
            title={
                <>
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <polyline points="14 2 14 8 20 8" />
                        <line x1="12" y1="18" x2="12" y2="12" />
                        <line x1="9" y1="15" x2="15" y2="15" />
                    </svg>
                    <span>Import Form 4 Data</span>
                </>
            }
        >
            <div className="form4-modal__body">
                <p className="form4-modal__description">
                    Fetch and ingest SEC EDGAR Form 4 insider trading filings for a specific ticker.
                    {companies.some(isForeignCompany) && (
                        <> Foreign private issuers (20-F filers) are greyed out: their insiders are exempt from
                        Section 16, so no Form 4 exists — ownership data is in their 20-F (Items 6 and 7).</>
                    )}
                </p>

                <div className="form4-modal__field">
                    <label htmlFor="form4-ticker">Ticker</label>
                    <select
                        id="form4-ticker"
                        value={ticker}
                        onChange={(e) => setTicker(e.target.value)}
                        className="form4-modal__select"
                    >
                        <option value="" disabled>Select a ticker</option>
                        {companies.map(c => (
                            <option key={c.ticker} value={c.ticker} disabled={isForeignCompany(c)}>
                                {c.ticker} — {c.name}{isForeignCompany(c) ? ' (foreign issuer — no Form 4)' : ''}
                            </option>
                        ))}
                    </select>
                </div>

                <div className="form4-modal__field">
                    <label htmlFor="form4-start-date">Start Date</label>
                    <input
                        id="form4-start-date"
                        type="date"
                        value={startDate}
                        onChange={(e) => setStartDate(e.target.value)}
                        className="form4-modal__date-input"
                    />
                </div>
            </div>

            <div className="form4-modal__footer">
                <Button variant="secondary" onClick={onClose}>Cancel</Button>
                <Button variant="primary" disabled={!ticker} loading={isIngesting} onClick={handleIngest}>
                    Ingest
                </Button>
            </div>
        </Modal>
    );
}

export default Form4ImportModal;
