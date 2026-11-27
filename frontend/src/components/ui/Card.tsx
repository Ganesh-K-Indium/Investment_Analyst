import React from 'react';
import './Card.css';

interface CardProps {
    children: React.ReactNode;
    className?: string;
    style?: React.CSSProperties;
    variant?: 'default' | 'elevated' | 'outlined';
    padding?: 'none' | 'sm' | 'md' | 'lg';
    onClick?: () => void;
    hoverable?: boolean;
}

export const Card: React.FC<CardProps> = ({
    children,
    className = '',
    style,
    variant = 'default',
    padding = 'md',
    onClick,
    hoverable = false
}) => {
    const classes = [
        'card',
        `card--${variant}`,
        `card--padding-${padding}`,
        hoverable ? 'card--hoverable' : '',
        onClick ? 'card--clickable' : '',
        className
    ].filter(Boolean).join(' ');

    return (
        <div className={classes} onClick={onClick} style={style}>
            {children}
        </div>
    );
};

export default Card;
