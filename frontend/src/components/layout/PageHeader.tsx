import React from 'react';
import './PageHeader.css';

interface PageHeaderProps {
    title: React.ReactNode;
    description?: React.ReactNode;
    /** Right-aligned actions. Use at most one primary Button here. */
    actions?: React.ReactNode;
    /** Small label above the title, e.g. the section name. */
    eyebrow?: React.ReactNode;
}

/** The standard page heading — same scale, spacing and action placement everywhere. */
export const PageHeader: React.FC<PageHeaderProps> = ({ title, description, actions, eyebrow }) => (
    <header className="page-header">
        <div className="page-header__text">
            {eyebrow && <span className="page-header__eyebrow">{eyebrow}</span>}
            <h1 className="page-header__title">{title}</h1>
            {description && <p className="page-header__desc">{description}</p>}
        </div>
        {actions && <div className="page-header__actions">{actions}</div>}
    </header>
);

export default PageHeader;
