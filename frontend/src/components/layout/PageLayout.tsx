import React from 'react';
import { Sidebar } from './Sidebar';
import { Header } from './Header';
import './PageLayout.css';

interface PageLayoutProps {
    children: React.ReactNode;
    /** Props forwarded to the top Header (e.g. showSearch / onSearch). */
    header?: React.ComponentProps<typeof Header>;
    /**
     * `scroll` (default): the page scrolls and content is capped at a readable width.
     * `fill`: the page does not scroll itself — children manage their own panes
     * (chat, split views, dashboards with internal scrolling).
     */
    mode?: 'scroll' | 'fill';
    /** Extra class on the outer element, for page-scoped styling hooks. */
    className?: string;
}

/**
 * The one app shell: sidebar, header and a main area with the same gutters on
 * every page. Pages used to each re-declare these rules with slightly
 * different padding, which is why content started at a different offset
 * on every screen.
 */
export const PageLayout: React.FC<PageLayoutProps> = ({ children, header, mode = 'scroll', className = '' }) => (
    <div className={`app-page ${className}`}>
        <Sidebar />
        <Header {...header} />
        <main className={`app-main app-main--${mode}`}>
            <div className="app-main__inner">{children}</div>
        </main>
    </div>
);

export default PageLayout;
