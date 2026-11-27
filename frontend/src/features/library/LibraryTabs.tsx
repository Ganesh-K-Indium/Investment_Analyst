import React from 'react';
import { NavLink } from 'react-router-dom';
import './LibraryTabs.css';

const TABS = [
    { to: '/documents', label: 'SEC filings', hint: 'Filings ingested into the knowledge base', end: true },
    { to: '/documents/mine', label: 'My documents', hint: 'Files you uploaded: statements, models, broker notes, scans', end: false },
    { to: '/notes', label: 'My notes', hint: 'Your own research notes', end: false },
];

/** The three kinds of source material, as one area. Filings, your files and your notes all feed the same answers. */
export const LibraryTabs: React.FC = () => (
    <nav className="lib-tabs" aria-label="Documents">
        {TABS.map(t => (
            <NavLink key={t.to} to={t.to} end={t.end} title={t.hint} className={({ isActive }) => `lib-tab${isActive ? ' lib-tab--on' : ''}`}>
                {t.label}
            </NavLink>
        ))}
    </nav>
);
