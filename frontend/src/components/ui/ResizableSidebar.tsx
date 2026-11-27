import type { RefObject } from 'react';
import './ResizableSidebar.css';

interface ResizableSidebarProps {
    collapsed: boolean;
    width: number;
    onExpand: () => void;
    onCollapse: () => void;
    resizeHandleRef: RefObject<HTMLDivElement | null>;
    onResizeMouseDown: (e: React.MouseEvent) => void;
    /** Extra class for page-specific width/appearance tweaks — see useResizableSidebar's min/max/default. */
    className?: string;
    children: React.ReactNode;
}

/**
 * Collapsible, drag-to-resize side panel shell — the collapse toggle, floating
 * re-open toggle and resize handle are identical across every research page,
 * so the behavior lives here once. Pair with useResizableSidebar for the state.
 */
export function ResizableSidebar({
    collapsed,
    width,
    onExpand,
    onCollapse,
    resizeHandleRef,
    onResizeMouseDown,
    className,
    children,
}: ResizableSidebarProps) {
    return (
        <>
            {collapsed && (
                <button
                    className="resizable-sidebar__toggle-floating"
                    onClick={onExpand}
                    title="Show sidebar"
                >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="9 18 15 12 9 6" />
                    </svg>
                </button>
            )}

            {!collapsed && (
                <button
                    className="resizable-sidebar__toggle"
                    style={{ left: width - 10 }}
                    onClick={onCollapse}
                    title="Hide sidebar"
                >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="15 18 9 12 15 6" />
                    </svg>
                </button>
            )}

            <aside
                className={['resizable-sidebar', collapsed && 'resizable-sidebar--collapsed', className].filter(Boolean).join(' ')}
                style={!collapsed ? { width } : undefined}
            >
                <div
                    ref={resizeHandleRef}
                    className="resizable-sidebar__resize-handle"
                    onMouseDown={onResizeMouseDown}
                />
                {children}
            </aside>
        </>
    );
}

export default ResizableSidebar;
