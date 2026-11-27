import React, { useState, useCallback } from 'react';
import { PageLayout } from '../components/layout/PageLayout';
import { PageHeader } from '../components/layout/PageHeader';
import { StatTile, StatGrid } from '../components/ui/StatTile';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { Modal } from '../components/ui/Modal';
import { CompanySelect } from '../components/ui/CompanySelect';
import { SecondaryResearch, QuantitativeResearch, Creation, BatchRunModal } from '../components/project';
import { PortfolioPulse } from '../features/analysis/PortfolioPulse';
import type { Company } from '../data/fortune500Companies';
import { allCompanies } from '../data/foreignCompanies';
import { useChatCache } from '../context/useChatCache';
import { useAuth } from '../context/useAuth';
import { useClipboardStore } from '../store/clipboardStore';
import { usePendingNavigationStore } from '../store/pendingNavigationStore';
import { useAnalysisRunsStore } from '../store/analysisRunsStore';
import type { AnalysisTask } from '../services/analysisTasksApi';
import { portfolioApi } from '../services/portfolioApi';
import { timeAgo } from '../lib/utils';
import './Home.css';
import '../components/project/workspace.css';

interface Project {
    id: string;
    name: string;
    description: string;
    startDate: string;
    targetDate: string;
    companies: Company[];
}

const mockProjects: Project[] = []; // Starting with empty, will fetch from backend

const detailTabs = ['Overview', 'Secondary Research', 'Quantitative Research', 'Creation'];

type SortKey = 'newest' | 'oldest' | 'name-asc' | 'name-desc' | 'target-date';

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
    { key: 'newest', label: 'Newest First' },
    { key: 'oldest', label: 'Oldest First' },
    { key: 'name-asc', label: 'Name (A–Z)' },
    { key: 'name-desc', label: 'Name (Z–A)' },
    { key: 'target-date', label: 'Target Date' },
];

const CARD_TASK_LABELS: Record<AnalysisTask['task_type'], string> = {
    ask: 'Secondary Research',
    compare: 'Company comparison',
    alpha: 'ALPHA analysis',
    query: 'Quantitative Research',
};

interface PortfolioRunSummary {
    running: number;
    ready: number;
    failed: number;
    latest?: { label: string; time: string };
}

// Shared between grid cards and list rows so both surface the same
// high-level "what's happening with this portfolio's analyses" summary —
// a spinner while something's running, counts for what's done/failed, and
// what the most recent run actually was.
const RunStatusSummary: React.FC<{ summary?: PortfolioRunSummary }> = ({ summary }) => {
    const hasActivity = summary && (summary.running > 0 || summary.ready > 0 || summary.failed > 0);
    if (!hasActivity) {
        return <span className="project-card__no-activity">No analysis run yet</span>;
    }
    return (
        <div className="project-card__run-status">
            {summary.running > 0 && (
                <span className="run-status-chip run-status-chip--running">
                    <span className="run-status-spinner" aria-hidden="true" />
                    {summary.running} running
                </span>
            )}
            {summary.ready > 0 && (
                <span className="run-status-chip run-status-chip--ready">
                    {summary.ready} ready
                </span>
            )}
            {summary.failed > 0 && (
                <span className="run-status-chip run-status-chip--failed">
                    {summary.failed} failed
                </span>
            )}
            {summary.latest && (
                <span className="project-card__last-activity">
                    {summary.latest.label} · {summary.latest.time}
                </span>
            )}
        </div>
    );
};

const greetingFor = (d: Date): string => {
    const h = d.getHours();
    if (h < 5) return 'Working late';
    if (h < 12) return 'Good morning';
    if (h < 18) return 'Good afternoon';
    return 'Good evening';
};

const monogram = (name: string): string =>
    name.trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() ?? '').join('') || '·';

export const Home: React.FC = () => {
    const { clearProjectCache } = useChatCache();
    const { userId: USER_ID, user, logout } = useAuth();
    const displayName = user?.full_name || user?.username || 'User';
    const clipboardTotal = useClipboardStore(s => s.totalCount());
    const clipboardInit  = useClipboardStore(s => s.init);
    const [projects, setProjects] = useState<Project[]>(mockProjects);
    const [portfolioSearch, setPortfolioSearch] = useState('');
    const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
    const [sortKey, setSortKey] = useState<SortKey>('newest');
    const [sortMenuOpen, setSortMenuOpen] = useState(false);
    const sortMenuRef = React.useRef<HTMLDivElement>(null);

    React.useEffect(() => {
        if (!sortMenuOpen) return;
        const onDocClick = (e: MouseEvent) => {
            if (sortMenuRef.current && !sortMenuRef.current.contains(e.target as Node)) setSortMenuOpen(false);
        };
        document.addEventListener('mousedown', onDocClick);
        return () => document.removeEventListener('mousedown', onDocClick);
    }, [sortMenuOpen]);

    const [tickerFilters, setTickerFilters] = useState<Set<string>>(new Set());
    const [filterMenuOpen, setFilterMenuOpen] = useState(false);
    const filterMenuRef = React.useRef<HTMLDivElement>(null);

    React.useEffect(() => {
        if (!filterMenuOpen) return;
        const onDocClick = (e: MouseEvent) => {
            if (filterMenuRef.current && !filterMenuRef.current.contains(e.target as Node)) setFilterMenuOpen(false);
        };
        document.addEventListener('mousedown', onDocClick);
        return () => document.removeEventListener('mousedown', onDocClick);
    }, [filterMenuOpen]);

    const toggleTickerFilter = (ticker: string) => {
        setTickerFilters(prev => {
            const next = new Set(prev);
            if (next.has(ticker)) next.delete(ticker);
            else next.add(ticker);
            return next;
        });
    };

    const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
    const [isBatchRunModalOpen, setIsBatchRunModalOpen] = useState(false);
    const [selectedProject, setSelectedProject] = useState<Project | null>(null);
    const [activeDetailTab, setActiveDetailTab] = useState('Overview');
    const [isEditingDetails, setIsEditingDetails] = useState(false);
    // Set when "View →" is clicked on a completed run in the Overview
    // dashboard, so the target research tab can jump straight to that
    // conversation instead of just opening on its default empty state.
    const [focusSession, setFocusSession] = useState<{ tab: 'Secondary Research' | 'Quantitative Research'; sessionId: string } | null>(null);
    // Track which tabs have mounted at least once — they stay alive after first visit
    const [mountedTabs, setMountedTabs] = useState<Set<string>>(new Set(['Overview']));
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Fetch portfolios on mount
    const fetchPortfolios = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await portfolioApi.getAllPortfolios(USER_ID);
            const mappedProjects: Project[] = data.map(portfolio => ({
                id: portfolio.id.toString(),
                name: portfolio.name,
                description: portfolio.description || '',
                startDate: portfolio.created_at.split('T')[0],
                targetDate: portfolio.updated_at.split('T')[0], // Simplified mapping
                companies: (portfolio.tickers || []).map(name => {
                    const nameLower = name.toLowerCase();
                    return allCompanies.find(c => c.name.toLowerCase() === nameLower) ||
                        allCompanies.find(c => c.ticker.toLowerCase() === nameLower) ||
                        { id: name, name, ticker: name, sector: 'Unknown' } as Company;
                }
                )
            }));
            setProjects(mappedProjects);
            setError(null);
        } catch (err) {
            console.error('Failed to fetch portfolios:', err);
            const status = (err as { status?: number })?.status;
            if (status === 401 || status === 403) {
                // Expired/invalid token — the generic "backend is running" message
                // is actively misleading here. Send the user back to log in
                // rather than showing an error they can't act on.
                logout();
                return;
            }
            setError('Could not load portfolios. Please make sure the backend is running.');
        } finally {
            setLoading(false);
        }
    }, [USER_ID, logout]);

    const loadInitial = useAnalysisRunsStore(s => s.loadInitial);

    React.useEffect(() => {
        fetchPortfolios();
    }, [fetchPortfolios]);

    React.useEffect(() => {
        if (projects.length === 0) return;
        projects.forEach(p => {
            const portfolioId = parseInt(p.id, 10);
            loadInitial(portfolioId);
        });
    }, [projects, loadInitial]);

    const runsByPortfolio = useAnalysisRunsStore(s => s.runsByPortfolio);

    const computeRunCounts = useCallback((): Record<string, PortfolioRunSummary> => {
        const counts: Record<string, PortfolioRunSummary> = {};
        for (const project of projects) {
            const portfolioId = parseInt(project.id, 10);
            const tasks = Object.values(runsByPortfolio[portfolioId] ?? {});
            const running = tasks.filter(t => t.status === 'PENDING' || t.status === 'RUNNING').length;
            const ready = tasks.filter(t => t.status === 'COMPLETED').length;
            const failed = tasks.filter(t => t.status === 'FAILED').length;
            const latestTask = [...tasks].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
            const latest = latestTask
                ? { label: CARD_TASK_LABELS[latestTask.task_type], time: timeAgo(latestTask.updated_at) }
                : undefined;
            counts[project.id] = { running, ready, failed, latest };
        }
        return counts;
    }, [projects, runsByPortfolio]);

    const runCounts = computeRunCounts();
    const totals = Object.values(runCounts).reduce(
        (acc, r) => ({ running: acc.running + r.running, ready: acc.ready + r.ready, failed: acc.failed + r.failed }),
        { running: 0, ready: 0, failed: 0 },
    );

    // Consumes a "jump to this portfolio/tab/session" handoff from the
    // notification bell (Header, rendered on every page) — the bell can't
    // reach into Home's local selected-project state directly, so it stashes
    // the target here and routes to /home; once the portfolio list has
    // loaded, open it exactly like clicking "View" in the Overview dashboard.
    const pendingNav = usePendingNavigationStore(s => s.pending);
    const clearPendingNav = usePendingNavigationStore(s => s.clearPending);

    // Edit form state for project details
    const [editName, setEditName] = useState('');
    const [editDescription, setEditDescription] = useState('');
    const [editTargetDate, setEditTargetDate] = useState('');

    // Form state
    const [projectName, setProjectName] = useState('');
    const [projectDescription, setProjectDescription] = useState('');
    const [targetDate, setTargetDate] = useState('');
    const [selectedCompanies, setSelectedCompanies] = useState<Company[]>([]);
    const [startDate, setStartDate] = useState(() => {
        const today = new Date();
        return today.toISOString().split('T')[0];
    });

    const handleViewProject = useCallback((project: Project) => {
        setSelectedProject(project);
        setEditName(project.name);
        setEditDescription(project.description);
        setEditTargetDate(project.targetDate);
        // Reset mounted tabs when opening a new project so stale state doesn't carry over
        setMountedTabs(new Set(['Overview']));
        setActiveDetailTab('Overview');
        setIsEditingDetails(false);
        setFocusSession(null);
        // Scope clipboard to this portfolio so clips from other portfolios don't bleed through
        clipboardInit(USER_ID, parseInt(project.id, 10));
    }, [USER_ID, clipboardInit]);

    // Open the portfolio the notification bell asked for (see pendingNav above), once the list has loaded.
    React.useEffect(() => {
        if (!pendingNav || projects.length === 0) return;
        const project = projects.find(p => p.id === pendingNav.portfolioId);
        if (!project) { clearPendingNav(); return; }
        handleViewProject(project);
        setActiveDetailTab(pendingNav.tab);
        setMountedTabs(prev => new Set([...prev, pendingNav.tab]));
        if (pendingNav.sessionId && pendingNav.tab !== 'Overview') setFocusSession({ tab: pendingNav.tab, sessionId: pendingNav.sessionId });
        clearPendingNav();
    }, [pendingNav, projects, clearPendingNav, handleViewProject]);

    const handleBackToList = () => {
        if (selectedProject) clearProjectCache(selectedProject.id);
        setSelectedProject(null);
        setEditName('');
        setEditDescription('');
        setEditTargetDate('');
        setActiveDetailTab('Overview');
        setMountedTabs(new Set(['Overview']));
        setIsEditingDetails(false);
        setFocusSession(null);
    };

    const handleCreateProject = async () => {
        if (!projectName.trim() || selectedCompanies.length === 0) return;
        setLoading(true);
        setError(null);

        try {
            const newPortfolio = await portfolioApi.createPortfolio({
                user_id: USER_ID,
                name: projectName,
                description: projectDescription,
                tickers: selectedCompanies.map(c => c.ticker)
            });

            const newProject: Project = {
                id: newPortfolio.id.toString(),
                name: newPortfolio.name,
                description: newPortfolio.description || '',
                startDate: newPortfolio.created_at.split('T')[0],
                targetDate: targetDate || new Date(Date.now() + 90 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
                companies: selectedCompanies
            };

            setProjects(prev => [newProject, ...prev]);

            // Reset form
            setProjectName('');
            setProjectDescription('');
            setTargetDate('');
            setSelectedCompanies([]);
            setStartDate(new Date().toISOString().split('T')[0]);
            setIsCreateModalOpen(false);
        } catch (err: unknown) {
            console.error('Failed to create portfolio:', err);
            setError((err instanceof Error && err.message) || 'Failed to create portfolio. Please try again.');
        } finally {
            setLoading(false);
        }
    }

    const handleUpdateProject = async (): Promise<boolean> => {
        if (!selectedProject) return false;
        setLoading(true);
        setError(null);

        try {
            await portfolioApi.updatePortfolio(parseInt(selectedProject.id), {
                name: editName,
                description: editDescription,
                // company_names: selectedProject.companies.map(c => c.name) // Currently only updating name/desc in UI form
            });

            setProjects(prev => prev.map(p =>
                p.id === selectedProject.id
                    ? { ...p, name: editName, description: editDescription, targetDate: editTargetDate }
                    : p
            ));

            setSelectedProject(prev => prev ? { ...prev, name: editName, description: editDescription, targetDate: editTargetDate } : null);
            return true;
        } catch (err) {
            console.error('Failed to update portfolio:', err);
            setError('Failed to update portfolio. Please try again.');
            return false;
        } finally {
            setLoading(false);
        }
    };

    const handleDeleteProject = async (id: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!window.confirm('Are you sure you want to delete this portfolio?')) return;

        setLoading(true);
        setError(null);

        try {
            await portfolioApi.deletePortfolio(parseInt(id));
            setProjects(prev => prev.filter(p => p.id !== id));
            if (selectedProject?.id === id) {
                setSelectedProject(null);
            }
            useAnalysisRunsStore.getState().removePortfolioTasks(parseInt(id));
        } catch (err) {
            console.error('Failed to delete portfolio:', err);
            setError('Failed to delete portfolio. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    // Derived list — computed unconditionally (before the detail-view early
    // return below) since these are hooks and hooks can't be called
    // conditionally; the detail view just doesn't use the result.
    const filteredProjects = portfolioSearch.trim()
        ? projects.filter(p =>
            p.name.toLowerCase().includes(portfolioSearch.toLowerCase()) ||
            p.companies.some(c =>
                c.name.toLowerCase().includes(portfolioSearch.toLowerCase()) ||
                c.ticker.toLowerCase().includes(portfolioSearch.toLowerCase())
            )
          )
        : projects;

    const availableTickers = React.useMemo(() => {
        const set = new Set<string>();
        projects.forEach(p => p.companies.forEach(c => set.add(c.ticker || c.name)));
        return Array.from(set).sort();
    }, [projects]);

    const tickerFilteredProjects = tickerFilters.size === 0
        ? filteredProjects
        : filteredProjects.filter(p => p.companies.some(c => tickerFilters.has(c.ticker || c.name)));

    const sortedProjects = React.useMemo(() => {
        const list = [...tickerFilteredProjects];
        switch (sortKey) {
            case 'name-asc':
                list.sort((a, b) => a.name.localeCompare(b.name));
                break;
            case 'name-desc':
                list.sort((a, b) => b.name.localeCompare(a.name));
                break;
            case 'oldest':
                list.sort((a, b) => a.startDate.localeCompare(b.startDate));
                break;
            case 'target-date':
                list.sort((a, b) => a.targetDate.localeCompare(b.targetDate));
                break;
            case 'newest':
            default:
                list.sort((a, b) => b.startDate.localeCompare(a.startDate));
                break;
        }
        return list;
    }, [tickerFilteredProjects, sortKey]);

    // Project Detail View
    if (selectedProject) {
        return (
            <PageLayout className="home-page" mode="fill">
                    {/* Back link + title share one line: the title already names where you are, so a separate
                        breadcrumb row above it only cost vertical space the research panes need. */}
                    <div className="project-detail__header">
                        <nav className="breadcrumbs" aria-label="Breadcrumb">
                            <button className="breadcrumbs__link" onClick={handleBackToList}>
                                Portfolio
                            </button>
                            <span className="breadcrumbs__separator" aria-hidden="true">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <polyline points="9 18 15 12 9 6" />
                                </svg>
                            </span>
                        </nav>
                        <h1 className="project-detail__title">{selectedProject.name}</h1>
                    </div>

                    {/* Detail Tabs */}
                    <div className="project-detail__tabs">
                        {detailTabs.map((tab) => (
                            <button
                                key={tab}
                                className={`project-detail__tab ${activeDetailTab === tab
                                    ? 'project-detail__tab--active'
                                    : ''
                                    }`}
                                onClick={() => {
                                    setActiveDetailTab(tab);
                                    setMountedTabs(prev => new Set([...prev, tab]));
                                }}
                            >
                                {tab}
                                {tab === 'Creation' && clipboardTotal > 0 && (
                                    <span className="project-detail__tab-badge">{clipboardTotal}</span>
                                )}
                            </button>
                        ))}
                    </div>

                    {/* Tab Content — Overview uses the same keep-alive pattern as the
                        other three tabs below: hiding with display:none instead of
                        unmounting keeps its PortfolioPulse (and SSE stream) alive
                        across tab switches, instead of tearing it down and refetching
                        every time the user glances at another tab and comes back. */}
                    <div style={{ display: activeDetailTab === 'Overview' ? 'contents' : 'none' }}>
                    {mountedTabs.has('Overview') && (
                        <div className="project-detail__content">
                            <div className="project-detail__details-card">
                                <div className="project-detail__details-header">
                                    <h3 className="project-detail__section-title">Portfolio Details</h3>
                                    {!isEditingDetails && (
                                        <Button type="button" variant="outline" size="sm" onClick={() => setIsEditingDetails(true)}>
                                            Edit
                                        </Button>
                                    )}
                                </div>

                                {!isEditingDetails ? (
                                    <div className="project-detail__details-grid">
                                        <div className="project-detail__meta">
                                            <span className="project-detail__meta-label">Project Name</span>
                                            <span className="project-detail__meta-value">{selectedProject.name}</span>
                                        </div>
                                        <div className="project-detail__meta">
                                            <span className="project-detail__meta-label">Target Date</span>
                                            <span className="project-detail__meta-value">{selectedProject.targetDate}</span>
                                        </div>
                                        <div className="project-detail__meta">
                                            <span className="project-detail__meta-label">Start Date</span>
                                            <span className="project-detail__meta-value">{selectedProject.startDate}</span>
                                        </div>
                                        <div className="project-detail__meta project-detail__meta--full">
                                            <span className="project-detail__meta-label">Project Description</span>
                                            <span className="project-detail__meta-value">
                                                {selectedProject.description || 'No description provided.'}
                                            </span>
                                        </div>
                                        <div className="project-detail__meta project-detail__meta--full">
                                            <span className="project-detail__meta-label">Companies</span>
                                            <div className="project-detail__companies">
                                                {selectedProject.companies.length > 0 ? (
                                                    selectedProject.companies.map(company => (
                                                        <span key={company.id} className="project-detail__company-tag">
                                                            <span className="project-detail__company-ticker">{company.ticker}</span>
                                                            <span className="project-detail__company-name">{company.name}</span>
                                                        </span>
                                                    ))
                                                ) : (
                                                    <span className="project-detail__no-companies">No companies selected</span>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                ) : (
                                    <form className="project-detail__form" onSubmit={(e) => e.preventDefault()}>
                                        <div className="modal-form__row">
                                            <div className="modal-form__field">
                                                <label className="modal-form__label">Project Name</label>
                                                <input
                                                    type="text"
                                                    className="input-field"
                                                    value={editName}
                                                    onChange={(e) => setEditName(e.target.value)}
                                                />
                                            </div>
                                            <div className="modal-form__field">
                                                <label className="modal-form__label">Target Date</label>
                                                <input
                                                    type="date"
                                                    className="input-field"
                                                    value={editTargetDate}
                                                    onChange={(e) => setEditTargetDate(e.target.value)}
                                                />
                                            </div>
                                        </div>
                                        <div className="modal-form__row">
                                            <div className="modal-form__field">
                                                <label className="modal-form__label">Project Description</label>
                                                <textarea
                                                    className="input-field"
                                                    value={editDescription}
                                                    onChange={(e) => setEditDescription(e.target.value)}
                                                    rows={4}
                                                />
                                            </div>
                                            <div className="modal-form__field">
                                                <label className="modal-form__label">Start Date</label>
                                                <input
                                                    type="date"
                                                    className="input-field input-field--disabled"
                                                    value={selectedProject.startDate}
                                                    disabled
                                                />
                                            </div>
                                        </div>
                                        <div className="modal-form__row modal-form__row--full">
                                            <div className="modal-form__field">
                                                <label className="modal-form__label">Companies</label>
                                                <div className="project-detail__companies">
                                                    {selectedProject.companies.length > 0 ? (
                                                        selectedProject.companies.map(company => (
                                                            <span key={company.id} className="project-detail__company-tag">
                                                                <span className="project-detail__company-ticker">{company.ticker}</span>
                                                                <span className="project-detail__company-name">{company.name}</span>
                                                            </span>
                                                        ))
                                                    ) : (
                                                        <span className="project-detail__no-companies">No companies selected</span>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                        <div className="project-detail__form-actions">
                                            <Button
                                                type="button"
                                                variant="primary"
                                                loading={loading}
                                                onClick={async () => {
                                                    if (await handleUpdateProject()) setIsEditingDetails(false);
                                                }}
                                            >
                                                Save
                                            </Button>
                                            <Button
                                                type="button"
                                                variant="outline"
                                                onClick={() => {
                                                    setEditName(selectedProject.name);
                                                    setEditDescription(selectedProject.description);
                                                    setEditTargetDate(selectedProject.targetDate);
                                                    setError(null);
                                                    setIsEditingDetails(false);
                                                }}
                                            >
                                                Cancel
                                            </Button>
                                            {error && <span className="error-message" style={{ color: 'red', marginLeft: '1rem' }}>{error}</span>}
                                        </div>
                                    </form>
                                )}
                            </div>

                            <PortfolioPulse
                                portfolioId={parseInt(selectedProject.id, 10)}
                                onNavigateToTab={(tab, focusSessionId) => {
                                    setActiveDetailTab(tab);
                                    setMountedTabs(prev => new Set([...prev, tab]));
                                    if (focusSessionId) setFocusSession({ tab, sessionId: focusSessionId });
                                }}
                            />
                        </div>
                    )}
                    </div>

                    {/* Keep-alive panels — components mount once and stay alive.
                        Hiding with display:none preserves all local state (chat history,
                        active session, scroll position) across tab switches. */}
                    <div style={{ display: activeDetailTab === 'Secondary Research' ? 'contents' : 'none' }}>
                        {mountedTabs.has('Secondary Research') && (
                            <SecondaryResearch
                                projectId={selectedProject.id}
                                projectName={selectedProject.name}
                                companies={selectedProject.companies}
                                focusSessionId={focusSession?.tab === 'Secondary Research' ? focusSession.sessionId : undefined}
                                onFocusApplied={() => setFocusSession(null)}
                                onAnalysisEnqueued={() => {}}
                            />
                        )}
                    </div>

                    <div style={{ display: activeDetailTab === 'Quantitative Research' ? 'contents' : 'none' }}>
                        {mountedTabs.has('Quantitative Research') && (
                            <QuantitativeResearch
                                projectId={selectedProject.id}
                                projectName={selectedProject.name}
                                focusSessionId={focusSession?.tab === 'Quantitative Research' ? focusSession.sessionId : undefined}
                                onFocusApplied={() => setFocusSession(null)}
                                onAnalysisEnqueued={() => {}}
                            />
                        )}
                    </div>

                    <div style={{ display: activeDetailTab === 'Creation' ? 'contents' : 'none' }}>
                        {mountedTabs.has('Creation') && (
                            <Creation
                                projectId={selectedProject.id}
                                projectName={selectedProject.name}
                                companies={selectedProject.companies}
                            />
                        )}
                    </div>
            </PageLayout>
        );
    }

    // Project List View
    return (
        <>
        <PageLayout className="home-page" header={{ showSearch: true, onSearch: setPortfolioSearch }}>
                <PageHeader
                    title={<>{greetingFor(new Date())}, {displayName}</>}
                    description={totals.running > 0
                        ? `${totals.running} ${totals.running === 1 ? 'analysis is' : 'analyses are'} running right now. Pick up where you left off.`
                        : projects.length === 0
                            ? 'Create your first portfolio to start researching companies.'
                            : 'Open a portfolio to continue your research, or start a new one.'}
                    actions={
                        <>
                            <Button
                                variant="outline"
                                onClick={() => setIsBatchRunModalOpen(true)}
                                disabled={projects.length === 0}
                                icon={
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <rect x="3" y="3" width="7" height="7" />
                                        <rect x="14" y="3" width="7" height="7" />
                                        <rect x="3" y="14" width="7" height="7" />
                                        <rect x="14" y="14" width="7" height="7" />
                                    </svg>
                                }
                            >
                                Batch Run
                            </Button>
                            <Button
                                variant="primary"
                                onClick={() => setIsCreateModalOpen(true)}
                                icon={
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <line x1="12" y1="5" x2="12" y2="19" />
                                        <line x1="5" y1="12" x2="19" y2="12" />
                                    </svg>
                                }
                            >
                                New Portfolio
                            </Button>
                        </>
                    }
                />

                {projects.length > 0 && (
                    <StatGrid label="Workspace summary">
                        <StatTile label="Portfolios" value={projects.length} hint="in your workspace" />
                        <StatTile label="Companies tracked" value={availableTickers.length} hint="unique tickers" />
                        <StatTile label="Running" value={totals.running} hint="analyses in flight" tone="running" />
                        <StatTile label="Ready to review" value={totals.ready} hint={totals.failed > 0 ? `${totals.failed} failed` : 'completed runs'} tone="ready" />
                    </StatGrid>
                )}

                {error && !selectedProject && (
                    <div className="error-banner" style={{
                        backgroundColor: 'var(--color-error-surface)',
                        color: 'var(--color-error-dark)',
                        padding: '1rem',
                        borderRadius: '0.5rem',
                        marginBottom: '1rem',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center'
                    }}>
                        <span>{error}</span>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => fetchPortfolios()}
                            style={{ borderColor: 'var(--color-error-dark)', color: 'var(--color-error-dark)', padding: '0.25rem 0.75rem', fontSize: '0.75rem' }}
                        >
                            Retry
                        </Button>
                    </div>
                )}

                {loading && !selectedProject && <div className="loading-spinner">Loading portfolios...</div>}


                {/* Project Section */}
                <div className="home-page__section">
                    <div className="home-page__section-header">
                        <h2>All Portfolios</h2>
                        <div className="home-page__controls">
                            <div className="home-page__filter-wrap" ref={filterMenuRef}>
                                <button className="home-page__filter-btn" onClick={() => setFilterMenuOpen(o => !o)}>
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
                                    </svg>
                                    Filter
                                    {tickerFilters.size > 0 && (
                                        <span className="home-page__filter-badge">{tickerFilters.size}</span>
                                    )}
                                </button>
                                {filterMenuOpen && (
                                    <div className="home-page__filter-menu">
                                        <div className="home-page__filter-menu-header">
                                            <span>Filter by ticker</span>
                                            {tickerFilters.size > 0 && (
                                                <button
                                                    className="home-page__filter-clear"
                                                    onClick={() => setTickerFilters(new Set())}
                                                >
                                                    Clear
                                                </button>
                                            )}
                                        </div>
                                        {availableTickers.length === 0 ? (
                                            <p className="home-page__filter-empty">No companies yet.</p>
                                        ) : (
                                            <div className="home-page__filter-options">
                                                {availableTickers.map(ticker => (
                                                    <label key={ticker} className="home-page__filter-option">
                                                        <input
                                                            type="checkbox"
                                                            checked={tickerFilters.has(ticker)}
                                                            onChange={() => toggleTickerFilter(ticker)}
                                                        />
                                                        {ticker}
                                                    </label>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                            <div className="home-page__sort-wrap" ref={sortMenuRef}>
                                <button className="home-page__sort-btn" onClick={() => setSortMenuOpen(o => !o)}>
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <line x1="4" y1="6" x2="20" y2="6" />
                                        <line x1="4" y1="12" x2="16" y2="12" />
                                        <line x1="4" y1="18" x2="12" y2="18" />
                                    </svg>
                                    Sort by
                                </button>
                                {sortMenuOpen && (
                                    <div className="home-page__sort-menu">
                                        {SORT_OPTIONS.map(opt => (
                                            <button
                                                key={opt.key}
                                                className={`home-page__sort-option ${sortKey === opt.key ? 'home-page__sort-option--active' : ''}`}
                                                onClick={() => { setSortKey(opt.key); setSortMenuOpen(false); }}
                                            >
                                                {opt.label}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>
                            <div className="home-page__view-toggle">
                                <button
                                    className={`home-page__view-btn ${viewMode === 'grid' ? 'home-page__view-btn--active' : ''}`}
                                    onClick={() => setViewMode('grid')}
                                >
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <rect x="3" y="3" width="7" height="7" />
                                        <rect x="14" y="3" width="7" height="7" />
                                        <rect x="14" y="14" width="7" height="7" />
                                        <rect x="3" y="14" width="7" height="7" />
                                    </svg>
                                </button>
                                <button
                                    className={`home-page__view-btn ${viewMode === 'list' ? 'home-page__view-btn--active' : ''}`}
                                    onClick={() => setViewMode('list')}
                                >
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <line x1="8" y1="6" x2="21" y2="6" />
                                        <line x1="8" y1="12" x2="21" y2="12" />
                                        <line x1="8" y1="18" x2="21" y2="18" />
                                        <line x1="3" y1="6" x2="3.01" y2="6" />
                                        <line x1="3" y1="12" x2="3.01" y2="12" />
                                        <line x1="3" y1="18" x2="3.01" y2="18" />
                                    </svg>
                                </button>
                            </div>
                        </div>
                    </div>

                    {/* Project Grid */}
                    <div className={`home-page__grid ${viewMode === 'list' ? 'home-page__grid--list' : ''} ${!loading && sortedProjects.length === 0 ? 'home-page__grid--empty' : ''}`}>
                        {!loading && sortedProjects.length === 0 && (
                            <div className="home-page__empty">
                                <div className="empty-state__icon">
                                    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
                                    </svg>
                                </div>
                                {portfolioSearch.trim() || tickerFilters.size > 0 ? (
                                    <>
                                        <h2>{portfolioSearch.trim() ? `No portfolios match "${portfolioSearch}"` : 'No portfolios match this filter'}</h2>
                                        <p>Try a different company name or ticker.</p>
                                    </>
                                ) : (
                                    <>
                                        <h2>No portfolios available</h2>
                                        <p>You haven't created any portfolios yet. Create a new portfolio to start your research and analysis.</p>
                                        <Button
                                            variant="primary"
                                            onClick={() => setIsCreateModalOpen(true)}
                                            icon={
                                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                    <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
                                                </svg>
                                            }
                                        >
                                            Create First Portfolio
                                        </Button>
                                    </>
                                )}
                            </div>
                        )}
                        {!loading && viewMode === 'grid' && sortedProjects.map(project => (
                            <Card key={project.id} hoverable className="project-card" onClick={() => handleViewProject(project)}>
                                <div className="project-card__body">
                                    <div className="project-card__header">
                                        <span className="project-card__monogram" aria-hidden="true">{monogram(project.name)}</span>
                                        <h3 className="project-card__title">{project.name}</h3>
                                        <button
                                            className="project-card__delete-btn"
                                            onClick={(e) => handleDeleteProject(project.id, e)}
                                            title="Delete Portfolio"
                                            style={{ background: 'none', border: 'none', color: 'var(--color-text-placeholder)', cursor: 'pointer', padding: '0.25rem' }}
                                        >
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                <polyline points="3 6 5 6 21 6" />
                                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                            </svg>
                                        </button>
                                    </div>
                                    {project.description && (
                                        <p className="project-card__description">{project.description}</p>
                                    )}
                                    <div className="project-card__companies-row">
                                        {project.companies.length > 0 ? (
                                            <>
                                                {project.companies.slice(0, 4).map(c => (
                                                    <span key={c.id} className="project-list__chip">{c.ticker || c.name}</span>
                                                ))}
                                                {project.companies.length > 4 && (
                                                    <span className="project-list__chip project-list__chip--more">
                                                        +{project.companies.length - 4}
                                                    </span>
                                                )}
                                            </>
                                        ) : (
                                            <span className="project-list__muted">No companies</span>
                                        )}
                                    </div>
                                    <RunStatusSummary summary={runCounts[project.id]} />
                                </div>
                                <div className="project-card__footer">
                                    <div className="project-card__date">
                                        <span className="project-card__label">Target Date</span>
                                        <span className="project-card__value">{project.targetDate}</span>
                                    </div>
                                    <button
                                        className="project-card__link"
                                        onClick={(e) => { e.stopPropagation(); handleViewProject(project); }}
                                    >
                                        View More
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <line x1="5" y1="12" x2="19" y2="12" />
                                            <polyline points="12,5 19,12 12,19" />
                                        </svg>
                                    </button>
                                </div>
                            </Card>
                        ))}

                        {!loading && viewMode === 'list' && sortedProjects.length > 0 && (
                            <div className="project-list">
                                <div className="project-list__row project-list__row--head">
                                    <span className="project-list__col project-list__col--name">Name</span>
                                    <span className="project-list__col project-list__col--companies">Companies</span>
                                    <span className="project-list__col project-list__col--date">Target Date</span>
                                    <span className="project-list__col project-list__col--actions" />
                                </div>
                                {sortedProjects.map(project => {
                                    const visibleCompanies = project.companies.slice(0, 3);
                                    const extraCount = project.companies.length - visibleCompanies.length;
                                    return (
                                        <Card
                                            key={project.id}
                                            hoverable
                                            className="project-list__row"
                                            onClick={() => handleViewProject(project)}
                                        >
                                            <div className="project-list__col project-list__col--name">
                                                <span className="project-list__title">{project.name}</span>
                                                {project.description && (
                                                    <span className="project-list__desc">{project.description}</span>
                                                )}
                                            </div>
                                            <div className="project-list__col project-list__col--companies">
                                                {visibleCompanies.length > 0 ? (
                                                    <>
                                                        {visibleCompanies.map(c => (
                                                            <span key={c.id} className="project-list__chip">{c.ticker || c.name}</span>
                                                        ))}
                                                        {extraCount > 0 && (
                                                            <span className="project-list__chip project-list__chip--more">+{extraCount}</span>
                                                        )}
                                                    </>
                                                ) : (
                                                    <span className="project-list__muted">—</span>
                                                )}
                                            </div>
                                            <div className="project-list__col project-list__col--date">
                                                {project.targetDate}
                                                <RunStatusSummary summary={runCounts[project.id]} />
                                            </div>
                                            <div className="project-list__col project-list__col--actions">
                                                <button
                                                    className="project-list__icon-btn"
                                                    onClick={(e) => handleDeleteProject(project.id, e)}
                                                    title="Delete Portfolio"
                                                >
                                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <polyline points="3 6 5 6 21 6" />
                                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                                    </svg>
                                                </button>
                                                <button
                                                    className="project-list__link"
                                                    onClick={(e) => { e.stopPropagation(); handleViewProject(project); }}
                                                >
                                                    View More
                                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                                        <line x1="5" y1="12" x2="19" y2="12" />
                                                        <polyline points="12,5 19,12 12,19" />
                                                    </svg>
                                                </button>
                                            </div>
                                        </Card>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </div>
        </PageLayout>

            {/* Batch Run Modal */}
            <BatchRunModal
                isOpen={isBatchRunModalOpen}
                onClose={() => setIsBatchRunModalOpen(false)}
                userId={USER_ID}
                portfolios={projects.map(p => ({
                    id: p.id,
                    name: p.name,
                    tickers: p.companies.map(c => c.ticker),
                    companies: p.companies,
                }))}
            />

            {/* Create Project Modal */}
            <Modal
                isOpen={isCreateModalOpen}
                onClose={() => setIsCreateModalOpen(false)}
                title="Create New"
            >
                <form className="modal-form" onSubmit={(e) => { e.preventDefault(); handleCreateProject(); }}>
                    <div className="modal-form__row">
                        <div className="modal-form__field">
                            <label className="modal-form__label">Project Name <span style={{ color: 'var(--color-error-dark)' }}>*</span></label>
                            <input
                                type="text"
                                className="input-field"
                                placeholder="Enter Project Name (Eg. Nippon Paints.. Etc.,)"
                                value={projectName}
                                onChange={(e) => setProjectName(e.target.value)}
                                required
                            />
                        </div>
                        <div className="modal-form__field">
                            <label className="modal-form__label">Target Date</label>
                            <input
                                type="date"
                                value={targetDate}
                                onChange={(e) => setTargetDate(e.target.value)}
                            />
                        </div>
                    </div>
                    <div className="modal-form__row">
                        <div className="modal-form__field">
                            <label className="modal-form__label">Project Description</label>
                            <textarea
                                placeholder="Enter Project Description"
                                value={projectDescription}
                                onChange={(e) => setProjectDescription(e.target.value)}
                            />
                        </div>
                        <div className="modal-form__field">
                            <label className="modal-form__label">Start Date</label>
                            <input
                                type="date"
                                value={startDate}
                                onChange={(e) => setStartDate(e.target.value)}
                            />
                        </div>
                    </div>
                    <div className="modal-form__row modal-form__row--full">
                        <div className="modal-form__field">
                            <label className="modal-form__label">Select Companies <span style={{ color: 'var(--color-error-dark)' }}>*</span></label>
                            <CompanySelect
                                companies={allCompanies}
                                selectedCompanies={selectedCompanies}
                                onChange={setSelectedCompanies}
                                placeholder="Search and select companies..."
                            />
                        </div>
                    </div>
                    <div className="modal-form__actions">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => {
                                setProjectName('');
                                setProjectDescription('');
                                setTargetDate('');
                                setSelectedCompanies([]);
                                setStartDate(new Date().toISOString().split('T')[0]);
                                setIsCreateModalOpen(false);
                            }}
                        >
                            Cancel
                        </Button>
                        <Button type="submit" variant="primary" disabled={!projectName.trim() || selectedCompanies.length === 0}>
                            Create
                        </Button>
                    </div>
                </form>
            </Modal>
        </>
    );
};

export default Home;
