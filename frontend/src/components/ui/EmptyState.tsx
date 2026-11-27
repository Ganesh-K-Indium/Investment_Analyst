import type { ReactNode } from 'react';
import './EmptyState.css';

interface EmptyStateProps {
    icon: ReactNode;
    title: string;
    description?: ReactNode;
    /** 'lg' fills a main content panel (chat/report canvas); 'sm' fits a sidebar sub-panel. */
    size?: 'sm' | 'lg';
    /** Tints the icon circle — pass one of the *-light token classes, defaults to the brand orange. */
    tintClassName?: string;
    children?: ReactNode;
}

/** Consistent icon + title + description empty state, used wherever a panel has nothing to show yet. */
export function EmptyState({ icon, title, description, size = 'lg', tintClassName, children }: EmptyStateProps) {
    return (
        <div className={`empty-state empty-state--${size}`}>
            <div className={['empty-state__icon', tintClassName].filter(Boolean).join(' ')}>
                {icon}
            </div>
            <p className="empty-state__title">{title}</p>
            {description && <p className="empty-state__description">{description}</p>}
            {children}
        </div>
    );
}

export default EmptyState;
