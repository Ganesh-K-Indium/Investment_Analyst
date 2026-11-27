import React, { useRef, useEffect, useCallback } from 'react';
import './AutoResizingTextarea.css';

interface AutoResizingTextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
    onSend?: () => void;
    maxRows?: number;
}

export const AutoResizingTextarea: React.FC<AutoResizingTextareaProps> = ({
    onSend,
    maxRows = 10,
    className = '',
    onChange,
    onKeyDown,
    value,
    ...props
}) => {
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const adjustHeight = useCallback(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;

        textarea.style.height = 'auto';
        const newHeight = Math.min(textarea.scrollHeight, maxRows * 20 + 20); // Estimation of row height
        textarea.style.height = `${newHeight}px`;
    }, [maxRows]);

    useEffect(() => {
        adjustHeight();
    }, [value, adjustHeight]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (onSend) {
                onSend();
            }
        }
        if (onKeyDown) {
            onKeyDown(e);
        }
    };

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        if (onChange) {
            onChange(e);
        }
        adjustHeight();
    };

    return (
        <textarea
            ref={textareaRef}
            className={`auto-resizing-textarea ${className}`}
            rows={1}
            value={value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            {...props}
        />
    );
};

export default AutoResizingTextarea;
