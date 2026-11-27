import React from 'react';
import { Button } from '../../components/ui/Button';

interface Props {
    page: number | null;
    total: number;
    hasPrev: boolean;
    hasNext: boolean;
    onPrev: () => void;
    onNext: () => void;
    children?: React.ReactNode;
}

/** Previous / "Page n of N" / Next, shared by a document's "What was read" and "Review" tabs. */
export const PageNav: React.FC<Props> = ({ page, total, hasPrev, hasNext, onPrev, onNext, children }) => (
    <nav className="ud-review__nav" aria-label="Page navigation">
        <Button size="sm" variant="outline" disabled={!hasPrev} onClick={onPrev} aria-label="Previous page">‹ Previous</Button>
        <span className="ud-review__pos tabular" aria-live="polite">Page {page ?? '–'} <span className="ud-sub">of {total}</span></span>
        <Button size="sm" variant="outline" disabled={!hasNext} onClick={onNext} aria-label="Next page">Next ›</Button>
        {children}
    </nav>
);
