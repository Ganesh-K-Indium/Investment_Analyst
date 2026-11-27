import React, { useMemo, useState } from 'react';
import { Button } from '../../components/ui/Button';
import type { Transcription } from '../../services/notesApi';

interface ReviewLine {
    key: string;
    page: number;
    text: string;
    flagged: boolean; // the two readers disagreed, or the line has an unreadable part
    reason: string | null;
    alternatives: string[];
    resolved: boolean; // the user confirmed or corrected it
}

interface TranscriptionReviewProps {
    result: Transcription;
    previewUrl: string | null; // the uploaded image or PDF, shown beside the text so lines can be checked against it
    previewKind: 'image' | 'pdf';
    onConfirm: (text: string) => void;
    onCancel: () => void;
}

const REASON_LABEL: Record<string, string> = {
    'numbers differ': 'The two readers saw different numbers',
    'wording differs': 'The two readers saw different words',
    'unreadable part': 'Part of this line could not be read',
    'not cross-checked': 'Not cross-checked: only one reader was available',
    'second reader did not see this line': 'Only one reader saw this line',
    'only the second reader saw this line': 'Only one reader saw this line',
};

const toLines = (result: Transcription): ReviewLine[] =>
    result.pages.flatMap(p =>
        p.lines.map((l, i) => ({
            key: `${p.page}-${i}`,
            page: p.page,
            text: l.text,
            flagged: l.status === 'review',
            reason: l.reason,
            alternatives: l.alternatives,
            resolved: l.status !== 'review',
        })),
    );

export const TranscriptionReview: React.FC<TranscriptionReviewProps> = ({ result, previewUrl, previewKind, onConfirm, onCancel }) => {
    const [lines, setLines] = useState<ReviewLine[]>(() => toLines(result));
    const [showImage, setShowImage] = useState(true);

    const pending = useMemo(() => lines.filter(l => l.flagged && !l.resolved).length, [lines]);
    const multiPage = result.pages.length > 1;

    const patch = (key: string, changes: Partial<ReviewLine>) =>
        setLines(prev => prev.map(l => (l.key === key ? { ...l, ...changes } : l)));

    const confirm = () => {
        const parts: string[] = [];
        let page = lines[0]?.page;
        let block: string[] = [];
        for (const l of lines) {
            const text = l.text.trim();
            if (!text) continue;
            if (l.page !== page) { parts.push(block.join('\n')); block = []; page = l.page; }
            block.push(text);
        }
        parts.push(block.join('\n'));
        onConfirm(parts.filter(Boolean).join('\n\n'));
    };

    const nonEmpty = lines.some(l => l.text.trim());

    return (
        <div className="tr">
            <div className="tr__banner" role="status">
                {result.needs_review > 0
                    ? <>
                        <strong>{pending === 0 ? 'All flagged lines checked.' : `${pending} of ${result.needs_review} flagged line${result.needs_review === 1 ? '' : 's'} still to check.`}</strong>
                        {' '}Compare each flagged line with your original, then correct it or mark it as right. Numbers matter most.
                    </>
                    : <><strong>Both readers agree on every line.</strong> Still skim it against your original before saving.</>}
            </div>
            {!result.verified && (
                <div className="tr__warn" role="alert">
                    Only one reader was available, so numbers were <strong>not cross-checked</strong>. Check every flagged line carefully.
                </div>
            )}

            <div className={`tr__body${previewUrl && showImage ? ' tr__body--split' : ''}`}>
                {previewUrl && showImage && (
                    <div className="tr__image">
                        {previewKind === 'pdf'
                            ? <iframe className="tr__pdf" src={previewUrl} title="Your uploaded PDF" />
                            : <img src={previewUrl} alt="Your uploaded note" />}
                    </div>
                )}
                <div className="tr__lines">
                    {lines.length === 0 && <p className="tr__empty">Nothing could be read from this file. Try a clearer photo, or type the note.</p>}
                    {lines.map((l, idx) => (
                        <React.Fragment key={l.key}>
                            {multiPage && (idx === 0 || lines[idx - 1].page !== l.page) && <div className="tr__page">Page {l.page}</div>}
                            <div className={`tr__line${l.flagged ? (l.resolved ? ' tr__line--done' : ' tr__line--flag') : ''}`}>
                                <input
                                    className="tr__input"
                                    value={l.text}
                                    aria-label={`Line ${idx + 1}`}
                                    onChange={e => patch(l.key, { text: e.target.value, resolved: l.flagged ? true : l.resolved })}
                                />
                                {l.flagged && !l.resolved && (
                                    <div className="tr__meta">
                                        <span className="tr__reason">{(l.reason && REASON_LABEL[l.reason]) || 'Please check this line'}</span>
                                        {l.alternatives.map(alt => (
                                            <button
                                                key={alt} type="button" className="tr__alt"
                                                title="Use the other reader's version"
                                                onClick={() => patch(l.key, { text: alt, resolved: true })}
                                            >
                                                Other reading: <code>{alt}</code>
                                            </button>
                                        ))}
                                        <button type="button" className="tr__ok" onClick={() => patch(l.key, { resolved: true })}>Looks right</button>
                                    </div>
                                )}
                                {l.flagged && l.resolved && <span className="tr__checked">✓ checked</span>}
                            </div>
                        </React.Fragment>
                    ))}
                </div>
            </div>

            <div className="tr__actions">
                {previewUrl && (
                    <button type="button" className="analyst-notes__link" onClick={() => setShowImage(s => !s)}>
                        {showImage ? 'Hide original' : 'Show original'}
                    </button>
                )}
                <span className="tr__spacer" />
                <Button type="button" variant="outline" onClick={onCancel}>Discard</Button>
                <Button type="button" variant="primary" onClick={confirm} disabled={pending > 0 || !nonEmpty}
                    title={pending > 0 ? 'Check the flagged lines first' : undefined}>
                    Use this text
                </Button>
            </div>
        </div>
    );
};

export default TranscriptionReview;
