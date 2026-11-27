import React from 'react';
import './StatTile.css';

interface StatTileProps {
    label: string;
    value: React.ReactNode;
    hint?: React.ReactNode;
    /** Semantic accent for the value. Default is neutral. */
    tone?: 'default' | 'running' | 'ready' | 'failed';
    /** When given, the tile is a toggle button (e.g. filter the list below to this). */
    onClick?: () => void;
    pressed?: boolean;
}

/** One stat tile, used on every page that shows summary numbers. */
export const StatTile: React.FC<StatTileProps> = ({ label, value, hint, tone = 'default', onClick, pressed }) => {
    const body = (
        <>
            <span className="stat-tile__label">{label}</span>
            <span className="stat-tile__value tabular">{value}</span>
            {hint && <span className="stat-tile__hint">{hint}</span>}
        </>
    );
    const cls = `stat-tile stat-tile--${tone}${onClick ? ' stat-tile--action' : ''}${pressed ? ' stat-tile--pressed' : ''}`;
    return onClick
        ? <button type="button" className={cls} onClick={onClick} aria-pressed={pressed}>{body}</button>
        : <div className={cls} role="listitem">{body}</div>;
};

export const StatGrid: React.FC<{ children: React.ReactNode; label?: string }> = ({ children, label }) => (
    <div className="stat-grid" role="list" aria-label={label}>{children}</div>
);

export default StatTile;
