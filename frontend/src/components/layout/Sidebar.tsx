import React, { useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/useAuth';
import { Wordmark } from '../ui/Wordmark';
import './Sidebar.css';

interface NavItem {
    path: string;
    label: string;
    icon: React.ReactNode;
    roles?: string[];
}

type ModalType = 'terms' | 'privacy' | 'support';

interface BottomItem {
    id: ModalType;
    label: string;
    icon: React.ReactNode;
}

const navItems: NavItem[] = [
    {
        path: '/home',
        label: 'Home',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                <polyline points="9,22 9,12 15,12 15,22" />
            </svg>
        )
    },
    {
        path: '/dashboard',
        label: 'Analysis Dashboard',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="9" rx="1" />
                <rect x="14" y="3" width="7" height="5" rx="1" />
                <rect x="14" y="12" width="7" height="9" rx="1" />
                <rect x="3" y="16" width="7" height="5" rx="1" />
            </svg>
        )
    },
    {
        path: '/repository',
        label: 'Repository',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
            </svg>
        )
    },
    {
        path: '/documents',
        label: 'Documents',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="8" y1="13" x2="16" y2="13" />
                <line x1="8" y1="17" x2="14" y2="17" />
            </svg>
        )
    },

    {
        path: '/data-sources',
        label: 'Data Sources',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <ellipse cx="12" cy="5" rx="9" ry="3" />
                <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
                <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
            </svg>
        )
    },
    {
        path: '/settings',
        label: 'Settings',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" />
                <line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" />
                <line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" />
                <line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" />
            </svg>
        )
    }
];

const bottomItems: BottomItem[] = [
    {
        id: 'terms',
        label: 'Terms & Conditions',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14,2 14,8 20,8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
                <polyline points="10,9 9,9 8,9" />
            </svg>
        )
    },
    {
        id: 'privacy',
        label: 'Privacy Policy',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
        )
    },
    {
        id: 'support',
        label: 'Support',
        icon: (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
        )
    }
];

interface InfoSection {
    heading: string;
    body: React.ReactNode;
}

// Renders each clause as a numbered card — Terms/Privacy content is a genuine
// sequence (clause order matters, e.g. "as described in Section 2"), so the
// number is structural, not decorative.
function InfoSections({ sections }: { sections: InfoSection[] }) {
    return (
        <>
            {sections.map((s, i) => (
                <section key={s.heading} className="info-modal__section info-modal__section--numbered">
                    <div className="info-modal__section-head">
                        <span className="info-modal__section-num">{i + 1}</span>
                        <h3>{s.heading}</h3>
                    </div>
                    <p>{s.body}</p>
                </section>
            ))}
        </>
    );
}

const modalContent: Record<ModalType, { title: string; body: React.ReactNode }> = {
    terms: {
        title: 'Terms & Conditions',
        body: (
            <div className="info-modal__content">
                <p className="info-modal__date">Effective Date: June 2026</p>
                <InfoSections sections={[
                    { heading: 'Acceptance of Terms', body: 'By accessing or using the Investment Analyst platform ("Platform"), you agree to be bound by these Terms and Conditions. If you do not agree, you may not use the Platform.' },
                    { heading: 'No Financial Advice', body: 'All analysis, reports, and insights generated by this Platform are for informational purposes only. Nothing on this Platform constitutes financial, investment, legal, or tax advice. Always consult a qualified financial advisor before making investment decisions.' },
                    { heading: 'Permitted Use', body: 'The Platform is intended for authorized business use only. You agree not to misuse the Platform, attempt unauthorized access, or use it for any unlawful purpose.' },
                    { heading: 'Intellectual Property', body: 'All content, features, and functionality of the Platform are owned by Indium Software and are protected by applicable intellectual property laws. Unauthorized reproduction or distribution is prohibited.' },
                    { heading: 'Limitation of Liability', body: 'Indium Software shall not be liable for any direct, indirect, incidental, or consequential damages arising from your use of the Platform or any investment decisions made based on Platform output.' },
                    { heading: 'Modifications', body: 'We reserve the right to modify these terms at any time. Continued use of the Platform after changes constitutes acceptance of the updated terms. Users will be notified of material changes.' },
                ]} />
            </div>
        )
    },
    privacy: {
        title: 'Privacy Policy',
        body: (
            <div className="info-modal__content">
                <p className="info-modal__date">Effective Date: June 2026</p>
                <InfoSections sections={[
                    { heading: 'Information We Collect', body: 'We collect account information (name, email, role), usage data (queries, reports generated, features accessed), and data associated with connected integrations and data sources.' },
                    { heading: 'How We Use Your Data', body: 'Your data is used to provide and improve the Platform, personalize your experience, ensure security, and maintain service reliability. We do not use your data to train external AI models.' },
                    { heading: 'Data Sharing', body: 'We do not sell your personal data. Data may be shared with trusted service providers who assist in operating the Platform, subject to strict confidentiality agreements.' },
                    { heading: 'Data Security', body: 'We implement industry-standard security measures including encryption in transit (TLS) and at rest to protect your data against unauthorized access or disclosure.' },
                    { heading: 'Data Retention', body: 'We retain your data for as long as your account is active or as required by applicable law. You may request deletion of your account data at any time.' },
                    { heading: 'Your Rights', body: 'You have the right to access, correct, or request deletion of your personal data. To exercise these rights, contact our privacy team.' },
                    { heading: 'Contact', body: <>For privacy-related concerns, contact us at <strong>privacy@indiumsoftware.com</strong></> },
                ]} />
            </div>
        )
    },
    support: {
        title: 'Support',
        body: (
            <div className="info-modal__content">
                <section className="info-modal__section info-modal__contact-section">
                    <h3>Contact Us</h3>
                    <div className="info-modal__contact-grid">
                        <div className="info-modal__contact-item">
                            <span className="info-modal__contact-label">Email</span>
                            <strong className="info-modal__contact-value">support@indiumsoftware.com</strong>
                        </div>
                        <div className="info-modal__contact-item">
                            <span className="info-modal__contact-label">Business Hours</span>
                            <strong className="info-modal__contact-value">Mon – Fri, 9 AM – 6 PM IST</strong>
                        </div>
                    </div>
                </section>
                <section className="info-modal__section">
                    <h3>Frequently Asked Questions</h3>
                    <div className="info-modal__faq">
                        <div className="info-modal__faq-item">
                            <p className="info-modal__faq-q">How do I add a new data source?</p>
                            <p className="info-modal__faq-a">Navigate to <strong>Data Sources</strong> in the sidebar, then click <strong>Add Data Source</strong> and follow the configuration steps.</p>
                        </div>
                        <div className="info-modal__faq-item">
                            <p className="info-modal__faq-q">How do I generate an investment report?</p>
                            <p className="info-modal__faq-a">Go to <strong>Home</strong>, select or search for a company, and use the chat interface to request analysis. Reports are saved automatically under <strong>My Reports</strong>.</p>
                        </div>
                        <div className="info-modal__faq-item">
                            <p className="info-modal__faq-q">What export formats are supported?</p>
                            <p className="info-modal__faq-a">Reports can be exported as <strong>PDF</strong> from the report viewer toolbar.</p>
                        </div>
                        <div className="info-modal__faq-item">
                            <p className="info-modal__faq-q">How do I manage user roles and access?</p>
                            <p className="info-modal__faq-a">Admins can manage user accounts and roles through the <strong>Integrations</strong> section. Contact your admin if you need a role change.</p>
                        </div>
                    </div>
                </section>
            </div>
        )
    }
};

export const Sidebar: React.FC = () => {
    const location = useLocation();
    const navigate  = useNavigate();
    const { user, logout } = useAuth();
    const [isCollapsed, setIsCollapsed] = useState(() => {
        const collapsed = localStorage.getItem('sidebar-collapsed') === 'true';
        if (collapsed) document.body.classList.add('sidebar-collapsed');
        else document.body.classList.remove('sidebar-collapsed');
        return collapsed;
    });
    const [activeModal, setActiveModal] = useState<ModalType | null>(null);

    const toggleCollapse = () => {
        setIsCollapsed(prev => {
            const next = !prev;
            localStorage.setItem('sidebar-collapsed', String(next));
            if (next) document.body.classList.add('sidebar-collapsed');
            else document.body.classList.remove('sidebar-collapsed');
            return next;
        });
    };

    const handleLogout = () => {
        logout();
        navigate('/login');
    };

    const visibleNavItems = navItems.filter(item =>
        !item.roles || (user && item.roles.includes(user.role))
    );

    return (
        <>
            <aside className={`sidebar${isCollapsed ? ' sidebar--collapsed' : ''}`}>
                <div className="sidebar__header">
                    <div className="sidebar__logo">
                        {isCollapsed ? (
                            <span className="sidebar__mark" aria-label="AiLPHA">[i]</span>
                        ) : (
                            <Wordmark className="sidebar__wordmark" />
                        )}
                    </div>
                    <button className="sidebar__toggle" onClick={toggleCollapse} title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            {isCollapsed
                                ? <><polyline points="9 18 15 12 9 6" /></>
                                : <><polyline points="15 18 9 12 15 6" /></>
                            }
                        </svg>
                    </button>
                </div>

                <nav className="sidebar__nav">
                    <ul className="sidebar__nav-list">
                        {visibleNavItems.map((item) => (
                            <li key={item.path} className="sidebar__nav-item">
                                <NavLink
                                    to={item.path}
                                    className={({ isActive }) =>
                                        `sidebar__nav-link ${isActive || location.pathname === item.path || (item.path === '/documents' && location.pathname.startsWith('/notes')) ? 'sidebar__nav-link--active' : ''}`
                                    }
                                >
                                    <span className="sidebar__nav-icon" title={isCollapsed ? item.label : ''}>{item.icon}</span>
                                    {!isCollapsed && <span className="sidebar__nav-label">{item.label}</span>}
                                </NavLink>
                            </li>
                        ))}
                    </ul>
                </nav>

                <div className="sidebar__bottom">
                    <ul className="sidebar__nav-list sidebar__nav-list--bottom">
                        {bottomItems.map((item) => (
                            <li key={item.id} className="sidebar__nav-item">
                                <button
                                    className="sidebar__nav-link sidebar__nav-link--secondary"
                                    onClick={() => setActiveModal(item.id)}
                                    title={isCollapsed ? item.label : ''}
                                >
                                    <span className="sidebar__nav-icon">{item.icon}</span>
                                    {!isCollapsed && <span className="sidebar__nav-label">{item.label}</span>}
                                </button>
                            </li>
                        ))}
                    </ul>

                    <button className="sidebar__logout" onClick={handleLogout} title={isCollapsed ? 'Logout' : ''}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                            <polyline points="16,17 21,12 16,7" />
                            <line x1="21" y1="12" x2="9" y2="12" />
                        </svg>
                        {!isCollapsed && <span>Logout</span>}
                    </button>
                </div>
            </aside>

            {activeModal && (
                <div className="info-modal__overlay" onClick={() => setActiveModal(null)}>
                    <div className="info-modal" onClick={e => e.stopPropagation()}>
                        <div className="info-modal__header">
                            <div className="info-modal__header-left">
                                <span className="info-modal__icon-badge">
                                    {bottomItems.find(i => i.id === activeModal)?.icon}
                                </span>
                                <h2 className="info-modal__title">{modalContent[activeModal].title}</h2>
                            </div>
                            <button className="info-modal__close" onClick={() => setActiveModal(null)} aria-label="Close">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <line x1="18" y1="6" x2="6" y2="18" />
                                    <line x1="6" y1="6" x2="18" y2="18" />
                                </svg>
                            </button>
                        </div>
                        <div className="info-modal__body">
                            {modalContent[activeModal].body}
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};

export default Sidebar;
