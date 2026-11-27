import React, { useState, useEffect, useCallback } from 'react';
import { PageLayout } from '../components/layout/PageLayout';
import { PageHeader } from '../components/layout/PageHeader';
import { Modal } from '../components/ui';
import { Button } from '../components/ui/Button';
import { StatTile } from '../components/ui/StatTile';
import { reportApi, type ReportResponse, type RepositoryStats } from '../services/reportApi';
import MyReports from './MyReports';
import './Repository.css';

export const Repository: React.FC = () => {
    const [activeView, setActiveView] = useState<'company' | 'my'>('company');

    // ── Stats ──────────────────────────────────────────────────────────────
    const [stats, setStats]           = useState<RepositoryStats | null>(null);
    const [statsLoading, setStatsLoading] = useState(true);

    // ── Search / filter ────────────────────────────────────────────────────
    const [searchQ, setSearchQ]               = useState('');
    const [filterCompany, setFilterCompany]   = useState('');
    const [filterFrom, setFilterFrom]         = useState('');
    const [filterTo, setFilterTo]             = useState('');

    // ── Viewer modal ───────────────────────────────────────────────────────
    const [viewReport, setViewReport] = useState<ReportResponse | null>(null);
    const [viewPdfUrl, setViewPdfUrl] = useState<string | null>(null);
    const [viewPdfError, setViewPdfError] = useState<string | null>(null);

    // ── Results ────────────────────────────────────────────────────────────
    const [reports, setReports]     = useState<ReportResponse[]>([]);
    const [total, setTotal]         = useState(0);
    const [page, setPage]           = useState(1);
    const [loading, setLoading]     = useState(false);
    const PAGE_SIZE = 12;

    // ── Fetch stats ────────────────────────────────────────────────────────
    const fetchStats = () => {
        reportApi.getRepositoryStats()
            .then(setStats)
            .catch(console.error)
            .finally(() => setStatsLoading(false));
    };

    useEffect(() => {
        fetchStats();
    }, []);

    // Refresh when switching back to company view
    useEffect(() => {
        if (activeView === 'company') {
            fetchStats();
            // We will fetch reports again in a moment, but since fetchReports isn't defined yet here,
            // we'll put that part below.
        }
    }, [activeView]);

    // ── Fetch reports ──────────────────────────────────────────────────────
    const fetchReports = useCallback(async (p = 1) => {
        setLoading(true);
        try {
            const params = {
                company: filterCompany || undefined,
                from_date: filterFrom ? `${filterFrom}T00:00:00` : undefined,
                to_date:   filterTo   ? `${filterTo}T23:59:59`   : undefined,
                page: p, page_size: PAGE_SIZE,
            };
            const res = searchQ.trim().length >= 2
                ? await reportApi.searchReports(searchQ.trim(), params)
                : await reportApi.listPublished(params);
            setReports(res.items);
            setTotal(res.total);
            setPage(p);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [searchQ, filterCompany, filterFrom, filterTo]);

    useEffect(() => { fetchReports(1); }, [fetchReports]);

    // Also refresh the current page when switching back from 'My Reports'
    useEffect(() => {
        if (activeView === 'company') {
            fetchReports(page);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeView]);

    // ── PDF viewer modal: fetch as a blob (the endpoint requires the auth
    // header a plain <iframe src> URL can't carry) whenever the viewed
    // report changes, and revoke the object URL when it's replaced/closed.
    useEffect(() => {
        if (!viewReport) {
            setViewPdfUrl(null);
            setViewPdfError(null);
            return;
        }
        let cancelled = false;
        let objectUrl: string | null = null;
        setViewPdfUrl(null);
        setViewPdfError(null);
        reportApi.fetchPdfBlobUrl(viewReport.id, true)
            .then(url => {
                if (cancelled) { URL.revokeObjectURL(url); return; }
                objectUrl = url;
                setViewPdfUrl(url);
            })
            .catch(err => {
                if (!cancelled) setViewPdfError(err instanceof Error ? err.message : 'Failed to load PDF');
            });
        return () => {
            cancelled = true;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
    }, [viewReport]);

    const totalPages = Math.ceil(total / PAGE_SIZE);

    // ── PDF download ───────────────────────────────────────────────────────
    const downloadPDF = async (id: number) => {
        try {
            await reportApi.downloadPdf(id);
        } catch (err) {
            console.error('Failed to download PDF:', err);
        }
    };

    return (
        <>
        <PageLayout className="repo-page">
            <PageHeader
                title={activeView === 'company' ? 'Report Repository' : (
                    <>
                        <button type="button" className="repo-crumb" onClick={() => setActiveView('company')}>Repository</button>
                        <span className="repo-crumb-sep" aria-hidden="true">/</span>
                        My Reports
                    </>
                )}
                description={activeView === 'company'
                    ? 'Published analyst reports — browse, filter, and download.'
                    : 'Manage, review, and publish your generated reports.'}
                actions={activeView === 'company' ? <Button variant="outline" onClick={() => setActiveView('my')}>My Reports</Button> : undefined}
            />

                {activeView === 'my' ? (
                    <MyReports onReportChanged={() => { fetchStats(); fetchReports(page); }} />
                ) : (
                    <>
                        {/* Top row: stats + recently published */}
                        <div className="repo-top-row">
                    {!statsLoading && stats && (
                        <div className="repo-stats">
                            <StatTile label="Published reports" value={stats.total_published} />
                            {stats.top_companies.length > 0 && (
                                <StatTile label="Top company" value={<span className="repo-stats__name">{stats.top_companies[0].company}</span>} />
                            )}
                        </div>
                    )}

                    {/* Recently Published strip */}
                    {!statsLoading && stats && (
                        <div className="repo-recent-strip">
                            <span className="repo-recent-strip__title">
                                Recently Published
                                <span className="repo-recent-strip__badge">last 2 days</span>
                            </span>
                            {stats.recent_reports.length === 0 ? (
                                <span className="repo-recent-strip__empty">No reports in the last 2 days</span>
                            ) : (
                                <div className="repo-recent-strip__items">
                                    {stats.recent_reports.map(r => (
                                        <button key={r.id} className="repo-recent-strip__item" onClick={() => downloadPDF(r.id)}>
                                            <div className="repo-recent-strip__item-left">
                                                <span className="repo-recent-strip__company">{r.company_name}{r.ticker ? ` · ${r.ticker}` : ''}</span>
                                                <span className="repo-recent-strip__author">{r.author}</span>
                                            </div>
                                            <span className="repo-recent-strip__date">
                                                {new Date(r.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Search + Filters */}
                <div className="repo-filters">
                    <div className="repo-filters__search">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                        </svg>
                        <input
                            className="repo-filters__search-input"
                            placeholder="Search reports — title, company, body…"
                            value={searchQ}
                            onChange={e => setSearchQ(e.target.value)}
                        />
                        {searchQ && (
                            <button className="repo-filters__clear" onClick={() => setSearchQ('')}>✕</button>
                        )}
                    </div>

                    <div className="repo-filters__row">
                        <input
                            className="repo-filters__input"
                            placeholder="Company / ticker"
                            value={filterCompany}
                            onChange={e => setFilterCompany(e.target.value)}
                        />

                        <label className="repo-filters__date-label">From</label>
                        <input type="date" className="repo-filters__date" value={filterFrom} onChange={e => setFilterFrom(e.target.value)} />
                        <label className="repo-filters__date-label">To</label>
                        <input type="date" className="repo-filters__date" value={filterTo} onChange={e => setFilterTo(e.target.value)} />

                        {(filterCompany || filterFrom || filterTo) && (
                            <button className="repo-filters__reset" onClick={() => { setFilterCompany(''); setFilterFrom(''); setFilterTo(''); }}>
                                Reset filters
                            </button>
                        )}
                    </div>
                </div>

                {/* Results */}
                <div className="repo-results">
                    <p className="repo-results__count">
                        {loading ? 'Loading…' : `${total} report${total !== 1 ? 's' : ''} found`}
                    </p>

                    {loading ? (
                        <div className="repo-results__loading">
                            <div className="repo-spinner" />
                        </div>
                    ) : reports.length === 0 ? (
                        <div className="repo-results__empty">
                            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
                                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
                            </svg>
                            <p>No reports found</p>
                            <span>Try adjusting your search or filters</span>
                        </div>
                    ) : (
                        <div className="repo-grid">
                            {reports.map(r => (
                                <div key={r.id} className="repo-card">
                                    <div className="repo-card__top">
                                        <div className="repo-card__meta">
                                            <span className="repo-card__company">{r.company_name}</span>
                                            {r.ticker && <span className="repo-card__ticker">{r.ticker}</span>}
                                        </div>
                                    </div>

                                    <div className="repo-card__footer">
                                        <div className="repo-card__author-info">
                                            <div className="repo-card__avatar">{r.author_name.split(' ').map((w: string) => w[0]).join('').toUpperCase().slice(0, 2)}</div>
                                            <div>
                                                <span className="repo-card__author">{r.author_name}</span>
                                                {(() => {
                                                    const created = new Date(r.created_at);
                                                    const updated = new Date(r.updated_at);
                                                    const wasUpdated = updated.getTime() - created.getTime() > 60_000;
                                                    return wasUpdated ? (
                                                        <>
                                                            <span className="repo-card__date">
                                                                Updated {updated.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                                                            </span>
                                                            <span className="repo-card__date repo-card__date--secondary">
                                                                Created {created.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                                            </span>
                                                        </>
                                                    ) : (
                                                        <span className="repo-card__date">
                                                            {created.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}
                                                        </span>
                                                    );
                                                })()}
                                            </div>
                                        </div>
                                        <div className="repo-card__actions">
                                            <button className="repo-card__view-btn" onClick={() => setViewReport(r)} title="Preview report">
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                                                    <circle cx="12" cy="12" r="3"/>
                                                </svg>
                                            </button>
                                            <button className="repo-card__pdf-btn" onClick={() => downloadPDF(r.id)} title="Download PDF">
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                                    <polyline points="7 10 12 15 17 10"/>
                                                    <line x1="12" y1="15" x2="12" y2="3"/>
                                                </svg>
                                                PDF
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div className="repo-pagination">
                            <button className="repo-pagination__btn" disabled={page === 1} onClick={() => fetchReports(page - 1)}>← Prev</button>
                            <span className="repo-pagination__info">Page {page} of {totalPages}</span>
                            <button className="repo-pagination__btn" disabled={page === totalPages} onClick={() => fetchReports(page + 1)}>Next →</button>
                        </div>
                    )}
                </div>
                </>
                )}
        </PageLayout>

            {/* ── Report Viewer Modal ─────────────────────────────────────── */}
            {viewReport && (
                <Modal
                    isOpen
                    onClose={() => setViewReport(null)}
                    size="xl"
                    fullHeight
                    title={
                        <div className="repo-modal__meta">
                            <span className="repo-modal__company">{viewReport.company_name}</span>
                            {viewReport.ticker && <span className="repo-modal__ticker">{viewReport.ticker}</span>}
                            <span className="repo-modal__author">by {viewReport.author_name}</span>
                            <span className="repo-modal__date">
                                {new Date(viewReport.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                            </span>
                        </div>
                    }
                >
                    {viewPdfError ? (
                        <div className="repo-modal__pdf-error">✗ {viewPdfError}</div>
                    ) : viewPdfUrl ? (
                        <iframe
                            className="repo-modal__pdf-frame"
                            src={viewPdfUrl}
                            title={`${viewReport.company_name} report`}
                        />
                    ) : (
                        <div className="repo-modal__pdf-loading">Loading PDF…</div>
                    )}
                </Modal>
            )}
        </>
    );
};

export default Repository;
