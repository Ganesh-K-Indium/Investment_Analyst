import React, { useEffect, useRef } from 'react';

interface SlideOverProps {
    open: boolean;
    onClose: () => void;
    title: React.ReactNode;
    subtitle?: React.ReactNode;
    width?: 'md' | 'lg';
    children: React.ReactNode;
    footer?: React.ReactNode;
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** A side panel over the page: Esc closes it, focus stays inside while open and returns to where it was on close. */
export const SlideOver: React.FC<SlideOverProps> = ({ open, onClose, title, subtitle, width = 'md', children, footer }) => {
    const panelRef = useRef<HTMLDivElement>(null);
    const returnTo = useRef<Element | null>(null);

    useEffect(() => {
        if (!open) return;
        returnTo.current = document.activeElement;
        const panel = panelRef.current;
        panel?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') { e.stopPropagation(); onClose(); return; }
            if (e.key !== 'Tab' || !panel) return;
            const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
            if (items.length === 0) return;
            const first = items[0], last = items[items.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        };
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('keydown', onKey);
            (returnTo.current as HTMLElement | null)?.focus?.();
        };
    }, [open, onClose]);

    if (!open) return null;
    return (
        <div className="ad-slideover" role="presentation" onClick={onClose}>
            <div
                ref={panelRef}
                className={`ad-slideover__panel ad-slideover__panel--${width}`}
                role="dialog"
                aria-modal="true"
                aria-label={typeof title === 'string' ? title : undefined}
                onClick={(e) => e.stopPropagation()}
            >
                <header className="ad-slideover__header">
                    <div className="ad-slideover__titles">
                        <h2 className="ad-slideover__title">{title}</h2>
                        {subtitle && <div className="ad-slideover__subtitle">{subtitle}</div>}
                    </div>
                    <button type="button" className="ad-icon-btn" onClick={onClose} aria-label="Close panel" title="Close (Esc)">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                    </button>
                </header>
                <div className="ad-slideover__body">{children}</div>
                {footer && <footer className="ad-slideover__footer">{footer}</footer>}
            </div>
        </div>
    );
};

export default SlideOver;
