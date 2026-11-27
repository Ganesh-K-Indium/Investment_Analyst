import React from 'react';
import './Input.css';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
    label?: string;
    error?: string;
    icon?: React.ReactNode;
    fullWidth?: boolean;
}

export const Input: React.FC<InputProps> = ({
    label,
    error,
    icon,
    fullWidth = true,
    className = '',
    id,
    ...props
}) => {
    const generatedId = React.useId();      // stable across renders, unique per input
    const inputId = id || generatedId;

    const containerClasses = [
        'input-container',
        fullWidth ? 'input-container--full-width' : '',
        error ? 'input-container--error' : '',
        className
    ].filter(Boolean).join(' ');

    return (
        <div className={containerClasses}>
            {label && (
                <label htmlFor={inputId} className="input-label">
                    {label}
                </label>
            )}
            <div className="input-wrapper">
                {icon && <span className="input-icon">{icon}</span>}
                <input
                    id={inputId}
                    className={`input-field ${icon ? 'input-field--with-icon' : ''}`}
                    {...props}
                />
            </div>
            {error && <span className="input-error">{error}</span>}
        </div>
    );
};

export default Input;
