import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/useAuth';
import { useTheme } from '../../context/useTheme';
import { portfolioApi } from '../../services/portfolioApi';
import { extractSessionId, type AnalysisTask } from '../../services/analysisTasksApi';
import { usePendingNavigationStore } from '../../store/pendingNavigationStore';
import { useAnalysisRunsStore } from '../../store/analysisRunsStore';
import { getInitials } from '../../lib/utils';
import './Header.css';

interface HeaderProps {
    showSearch?: boolean;
    onSearch?: (query: string) => void;
}

const LAST_SEEN_RUNS_KEY = 'notif-last-seen-runs';
// Notifications aren't a persisted table — "Clear all" just moves this
// cursor forward, so fetchCompletedRuns drops anything at/before it. Cheaper
// than tracking dismissed ids (bounded storage, and naturally self-prunes).
const CLEARED_BEFORE_KEY = 'notif-runs-cleared-before';

const RUN_TASK_LABELS: Record<AnalysisTask['task_type'], string> = {
    ask: 'Secondary Research',
    compare: 'Company comparison',
    alpha: 'ALPHA analysis',
    query: 'Quantitative Research',
};

function timeAgo(isoString: string): string {
    const diff = Date.now() - new Date(isoString).getTime();
    const mins  = Math.floor(diff / 60000);
    if (mins < 60)   return mins <= 1 ? 'Just now' : `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24)  return `${hours}h ago`;
    const days  = Math.floor(hours / 24);
    if (days < 7)    return `${days}d ago`;
    return new Date(isoString).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export const Header: React.FC<HeaderProps> = ({ showSearch = false, onSearch }) => {
    const { user, userId, logout } = useAuth();
    const { theme, toggleTheme } = useTheme();
    const navigate = useNavigate();
    const [searchQuery, setSearchQuery]             = useState('');
    const [showUserMenu, setShowUserMenu]           = useState(false);
    const [showNotifications, setShowNotifications] = useState(false);
    // Bumped whenever we move the "seen" / "cleared" cursors in localStorage, so the
    // derived notification list and unread count below recompute from them.
    const [cursorTick, setCursorTick]               = useState(0);
    const notifRef = useRef<HTMLDivElement>(null);
    const setPendingNavigation = usePendingNavigationStore(s => s.setPending);

    const name      = user?.full_name || user?.username || 'User';
    const roleLabel = user?.role === 'fund_manager' ? 'Fund Manager'
                    : user?.role === 'admin'        ? 'Admin'
                    : 'Analyst';

    const runsByPortfolio = useAnalysisRunsStore(s => s.runsByPortfolio);
    const [portfolioNames, setPortfolioNames] = useState<Record<number, string>>({});

    useEffect(() => {
        if (!userId) return;
        portfolioApi.getAllPortfolios(userId)
            .then(portfolios => {
                const names: Record<number, string> = {};
                portfolios.forEach(p => { names[p.id] = p.name; });
                setPortfolioNames(names);
            })
            .catch(() => {});
    }, [userId]);

    const completedRuns = useMemo(() => {
        const clearedBefore = localStorage.getItem(CLEARED_BEFORE_KEY);
        const allCompleted: (AnalysisTask & { portfolioName: string })[] = [];
        for (const [portfolioId, tasks] of Object.entries(runsByPortfolio)) {
            for (const task of Object.values(tasks)) {
                if (task.status === 'COMPLETED') {
                    const portfolioName = portfolioNames[parseInt(portfolioId)] || 'Unknown Portfolio';
                    allCompleted.push({ ...task, portfolioName });
                }
            }
        }
        const filtered = allCompleted
            .filter(r => !clearedBefore || r.updated_at > clearedBefore)
            .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
            .slice(0, 20);
        return filtered;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- cursorTick re-reads the localStorage cursors
    }, [runsByPortfolio, portfolioNames, cursorTick]);

    const unreadRunCount = useMemo(() => {
        const lastSeen = localStorage.getItem(LAST_SEEN_RUNS_KEY);
        return lastSeen
            ? completedRuns.filter(r => new Date(r.updated_at) > new Date(lastSeen)).length
            : completedRuns.length;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- cursorTick re-reads the localStorage cursor
    }, [completedRuns, cursorTick]);

    const handleLogout = () => {
        logout();
        navigate('/login');
    };

    const toggleNotifications = () => {
        if (!showNotifications) {
            localStorage.setItem(LAST_SEEN_RUNS_KEY, new Date().toISOString());
            setCursorTick(t => t + 1);
        }
        setShowNotifications(v => !v);
        setShowUserMenu(false);
    };

    const handleClearNotifications = (e: React.MouseEvent) => {
        e.stopPropagation();
        localStorage.setItem(CLEARED_BEFORE_KEY, new Date().toISOString());
        setCursorTick(t => t + 1);
    };

    const handleRunClick = (run: AnalysisTask & { portfolioName: string }) => {
        if (run.portfolio_id == null) return;
        setPendingNavigation({
            portfolioId: String(run.portfolio_id),
            tab: run.agent_type === 'quant' ? 'Quantitative Research' : 'Secondary Research',
            sessionId: extractSessionId(run),
        });
        setShowNotifications(false);
        navigate('/home');
    };

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
                setShowNotifications(false);
            }
        };
        if (showNotifications) document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [showNotifications]);

    return (
        <header className="header">
            {showSearch ? (
                <div className="header__search">
                    <svg className="header__search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="11" cy="11" r="8" />
                        <line x1="21" y1="21" x2="16.65" y2="16.65" />
                    </svg>
                    <input
                        type="text"
                        className="header__search-input"
                        placeholder="Search portfolios…"
                        value={searchQuery}
                        onChange={e => { setSearchQuery(e.target.value); onSearch?.(e.target.value); }}
                    />
                </div>
            ) : (
                <div className="header__search-placeholder" />
            )}

            <div className="header__actions">
                <button
                    className="header__action-btn"
                    title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                    onClick={toggleTheme}
                >
                    {theme === 'dark' ? (
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="12" cy="12" r="5" />
                            <line x1="12" y1="1" x2="12" y2="3" />
                            <line x1="12" y1="21" x2="12" y2="23" />
                            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                            <line x1="1" y1="12" x2="3" y2="12" />
                            <line x1="21" y1="12" x2="23" y2="12" />
                            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                        </svg>
                    ) : (
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                        </svg>
                    )}
                </button>

                <div className="header__notif-wrapper" ref={notifRef}>
                    <button
                        className={`header__action-btn${showNotifications ? ' header__action-btn--active' : ''}`}
                        title="Notifications"
                        onClick={toggleNotifications}
                    >
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
                        </svg>
                        {unreadRunCount > 0 && (
                            <span className="header__notification-badge">{unreadRunCount}</span>
                        )}
                    </button>

                    {showNotifications && (
                        <div className="header__notif-panel">
                            <div className="header__notif-panel-header">
                                <span className="header__notif-panel-title">Analyses Ready</span>
                                {completedRuns.length > 0 && (
                                    <button className="header__notif-clear-btn" onClick={handleClearNotifications}>
                                        Clear all
                                    </button>
                                )}
                            </div>

                            {completedRuns.length > 0 ? (
                                <ul className="header__notif-list">
                                    {completedRuns.map(r => (
                                        <li
                                            key={r.id}
                                            className="header__notif-item header__notif-item--clickable"
                                            onClick={() => handleRunClick(r)}
                                        >
                                            <span className="header__notif-icon header__notif-icon--run">
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                                    <polyline points="20 6 9 17 4 12" />
                                                </svg>
                                            </span>
                                            <div className="header__notif-body">
                                                <span className="header__notif-title">
                                                    {RUN_TASK_LABELS[r.task_type]} ready
                                                </span>
                                                <span className="header__notif-message">
                                                    {r.portfolioName}
                                                </span>
                                                <span className="header__notif-time">{timeAgo(r.updated_at)}</span>
                                            </div>
                                        </li>
                                    ))}
                                </ul>
                            ) : (
                                <div className="header__notif-empty">
                                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                                        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
                                    </svg>
                                    <span>No analyses ready yet</span>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                <div className="header__user">
                    <button className="header__user-btn" onClick={() => { setShowUserMenu(v => !v); setShowNotifications(false); }}>
                        <div className="header__user-avatar header__user-avatar--placeholder">
                            {getInitials(name)}
                        </div>
                        <div className="header__user-info">
                            <span className="header__user-name">{name}</span>
                            <span className="header__user-role">{roleLabel}</span>
                        </div>
                        <svg className="header__user-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <polyline points="6,9 12,15 18,9" />
                        </svg>
                    </button>

                    {showUserMenu && (
                        <div className="header__user-menu">
                            <span className="header__user-menu-email">{user?.email}</span>
                            <hr className="header__user-menu-divider" />
                            <button className="header__user-menu-item header__user-menu-item--danger" onClick={handleLogout}>
                                Logout
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </header>
    );
};

export default Header;
