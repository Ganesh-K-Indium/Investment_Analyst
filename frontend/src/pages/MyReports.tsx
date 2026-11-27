import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/useAuth';
import { reportApi, type ReportResponse } from '../services/reportApi';
import './MyReports.css';

type FilterStatus = 'all' | 'draft' | 'published';

interface MyReportsProps {
    onReportChanged?: () => void;
}

export const MyReports: React.FC<MyReportsProps> = ({ onReportChanged }) => {
    const { userId } = useAuth();

    const [reports, setReports]         = useState<ReportResponse[]>([]);
    const [loading, setLoading]         = useState(true);
    const [filterStatus, setFilterStatus] = useState<FilterStatus>('all');
    const [toast, setToast]             = useState<string | null>(null);
    const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
    const [actionLoading, setActionLoading] = useState<number | null>(null);

    const showToast = (msg: string) => {
        setToast(msg);
        setTimeout(() => setToast(null), 3000);
    };

    const fetchReports = useCallback(async () => {
        if (!userId) return;
        setLoading(true);
        try {
            const res = await reportApi.getUserReports(userId, undefined);
            setReports(res.items);
        } catch (err) {
            console.error(err);
            showToast('Failed to load reports');
        } finally {
            setLoading(false);
        }
    }, [userId]);

    useEffect(() => { fetchReports(); }, [fetchReports]);

    const handlePublish = async (r: ReportResponse) => {
        setActionLoading(r.id);
        try {
            if (r.status === 'draft') {
                await reportApi.publishReport(r.id, userId);
                showToast('✓ Report published to repository');
            } else {
                await reportApi.unpublishReport(r.id, userId);
                showToast('✓ Report moved back to draft');
            }
            fetchReports();
            onReportChanged?.();
        } catch (err: unknown) {
            showToast(`✗ ${err instanceof Error ? err.message : 'Action failed'}`);
        } finally {
            setActionLoading(null);
        }
    };

    const handleDelete = async (id: number) => {
        setActionLoading(id);
        try {
            await reportApi.deleteReport(id, userId);
            showToast('Report deleted');
            setReports(prev => prev.filter(r => r.id !== id));
            onReportChanged?.();
        } catch (err: unknown) {
            showToast(`✗ ${err instanceof Error ? err.message : 'Delete failed'}`);
        } finally {
            setActionLoading(null);
            setConfirmDelete(null);
        }
    };

    const downloadPDF = async (id: number) => {
        try {
            await reportApi.downloadPdf(id);
        } catch (err: unknown) {
            showToast(`✗ ${err instanceof Error ? err.message : 'PDF download failed'}`);
        }
    };

    const counts = {
        all:       reports.length,
        draft:     reports.filter(r => r.status === 'draft').length,
        published: reports.filter(r => r.status === 'published').length,
    };

    const visible = filterStatus === 'all'
        ? reports
        : reports.filter(r => r.status === filterStatus);

    return (
        <div className="myrep-content">
            {/* Status filter tabs */}
            <div className="myrep-tabs">
                    {(['all', 'draft', 'published'] as FilterStatus[]).map(s => (
                        <button
                            key={s}
                            className={`myrep-tab ${filterStatus === s ? 'myrep-tab--active' : ''}`}
                            onClick={() => setFilterStatus(s)}
                        >
                            {s.charAt(0).toUpperCase() + s.slice(1)}
                            <span className="myrep-tab__count">{counts[s]}</span>
                        </button>
                    ))}
                </div>

                {/* Table */}
                {loading ? (
                    <div className="myrep-loading"><div className="myrep-spinner" /></div>
                ) : visible.length === 0 ? (
                    <div className="myrep-empty">
                        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                            <polyline points="14,2 14,8 20,8"/>
                            <line x1="16" y1="13" x2="8" y2="13"/>
                            <line x1="16" y1="17" x2="8" y2="17"/>
                        </svg>
                        <p>No {filterStatus !== 'all' ? filterStatus : ''} reports yet</p>
                        <span>Use the Creation tab to assemble and save your first report.</span>
                    </div>
                ) : (
                    <div className="myrep-table-wrap">
                        <table className="myrep-table">
                            <thead>
                                <tr>
                                    <th>Company</th>
                                    <th>Status</th>
                                    <th>Date</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visible.map(r => (
                                    <tr key={r.id} className={`myrep-row ${r.status === 'published' ? 'myrep-row--published' : ''}`}>
                                        <td className="myrep-cell myrep-cell--title">
                                            <span className="myrep-company">{r.company_name}</span>
                                            {r.ticker && <span className="myrep-ticker">{r.ticker}</span>}
                                        </td>
                                        <td className="myrep-cell">
                                            <span className={`myrep-status myrep-status--${r.status}`}>
                                                {r.status === 'published' ? '● Published' : '○ Draft'}
                                            </span>
                                        </td>
                                        <td className="myrep-cell myrep-cell--date">
                                            {(() => {
                                                const created = new Date(r.created_at);
                                                const updated = new Date(r.updated_at);
                                                const wasUpdated = updated.getTime() - created.getTime() > 60_000;
                                                return wasUpdated ? (
                                                    <>
                                                        <span className="myrep-date-updated">
                                                            Updated {updated.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                                                        </span>
                                                        <span className="myrep-date-created">
                                                            Created {created.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                                                        </span>
                                                    </>
                                                ) : (
                                                    <>
                                                        <span className="myrep-date-updated">
                                                            Created {created.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                                                        </span>
                                                    </>
                                                );
                                            })()}
                                        </td>
                                        <td className="myrep-cell myrep-cell--actions">
                                            <div className="myrep-actions">
                                                {/* Publish / Unpublish */}
                                                <button
                                                    className={`myrep-action-btn ${r.status === 'draft' ? 'myrep-action-btn--publish' : 'myrep-action-btn--unpublish'}`}
                                                    onClick={() => handlePublish(r)}
                                                    disabled={actionLoading === r.id}
                                                    title={r.status === 'draft' ? 'Publish to repository' : 'Move back to draft'}
                                                >
                                                    {actionLoading === r.id ? '…' : r.status === 'draft' ? 'Publish' : 'Unpublish'}
                                                </button>

                                                {/* Download PDF */}
                                                <button
                                                    className="myrep-action-btn myrep-action-btn--pdf"
                                                    onClick={() => downloadPDF(r.id)}
                                                    title="Download PDF"
                                                >
                                                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                                        <polyline points="7 10 12 15 17 10"/>
                                                        <line x1="12" y1="15" x2="12" y2="3"/>
                                                    </svg>
                                                </button>

                                                {/* Delete */}
                                                {confirmDelete === r.id ? (
                                                    <div className="myrep-confirm">
                                                        <span>Delete?</span>
                                                        <button className="myrep-confirm-yes" onClick={() => handleDelete(r.id)}>Yes</button>
                                                        <button className="myrep-confirm-no" onClick={() => setConfirmDelete(null)}>No</button>
                                                    </div>
                                                ) : (
                                                    <button
                                                        className="myrep-action-btn myrep-action-btn--delete"
                                                        onClick={() => setConfirmDelete(r.id)}
                                                        title="Delete report"
                                                    >
                                                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                            <polyline points="3 6 5 6 21 6"/>
                                                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                                                        </svg>
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            {toast && <div className="myrep-toast">{toast}</div>}
        </div>
    );
};

export default MyReports;
