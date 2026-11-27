import React from 'react';
import './Wordmark.css';

/** The A[i]LPHA wordmark. The bracketed "i" is the brand's nod to AI. */
export const Wordmark: React.FC<{ className?: string }> = ({ className = '' }) => (
    <span className={`wordmark ${className}`} aria-label="AiLPHA">
        A<span className="wordmark__i" aria-hidden="true">[i]</span>LPHA
    </span>
);

export default Wordmark;
