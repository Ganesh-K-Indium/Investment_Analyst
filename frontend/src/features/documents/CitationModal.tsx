import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { documentsApi, type BBox, type DocumentSummary, type PageResponse } from './documentsApi';
import { PdfViewer, type Overlay, type PdfViewerHandle } from './PdfViewer';
import { inspectorUrl, regionOf } from './citations';
import './DocumentsPage.css';
import './Inspector.css';
import './CitationModal.css';

interface Props {
    docId: string;
    page: number;
    quote?: string;
    portfolioId?: string;
    sessionId?: string;
    onClose: () => void;
}

/** Small in-place preview of a cited page with the cited text highlighted; the full Inspector is one click away. */
export const CitationModal: React.FC<Props> = ({ docId, page, quote, portfolioId, sessionId, onClose }) => {
    const [doc, setDoc] = useState<DocumentSummary | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [current, setCurrent] = useState(page);
    const [scale, setScale] = useState(1);
    const [hl, setHl] = useState<{ page: number; rects: BBox[] } | null>(null);
    const [note, setNote] = useState<string | null>(null);
    const [text, setText] = useState<PageResponse | null>(null);
    const viewer = useRef<PdfViewerHandle>(null);
    const bodyRef = useRef<HTMLDivElement>(null);
    const ready = useRef(false);

    useEffect(() => { documentsApi.get(docId).then(setDoc).catch(e => setError(e.message)); }, [docId]);

    useEffect(() => {
        const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', h);
        return () => window.removeEventListener('keydown', h);
    }, [onClose]);

    const hasPdf = doc?.pdf_status === 'local';

    // No local PDF: show the indexed page text instead (quote marked).
    useEffect(() => {
        if (!doc || hasPdf) return;
        documentsApi.page(docId, current).then(setText).catch(e => setError(e.message));
    }, [doc, hasPdf, docId, current]);

    // Locate the cited text (adjacent pages too when the PDF pagination differs from the index).
    useEffect(() => {
        if (!quote) return;
        let dead = false;
        (async () => {
            // Starts straight away (warmed on hover); it does not wait for the document summary.
            const first = await documentsApi.locate(docId, page, quote).catch(() => null);
            if (dead) return;
            if (first?.method === 'no_pdf') return; // no local PDF: the indexed text view is shown instead
            if (first?.rects.length) {
                setHl({ page, rects: first.rects });
                setTimeout(() => viewer.current?.scrollToRect(page, first.rects[0]), 400);
                return;
            }
            if (first?.method === 'no_text_layer') { setNote('Scanned page — no text to highlight.'); return; }
            const d = await documentsApi.get(docId).catch(() => null);
            if (dead || !d) return;
            for (const n of d.aligned === false ? [page + 1, page - 1] : []) {
                if (n < 1 || n > d.page_count) continue;
                try {
                    const r = await documentsApi.locate(docId, n, quote);
                    if (dead) return;
                    if (r.rects.length) {
                        setHl({ page: n, rects: r.rects });
                        setTimeout(() => viewer.current?.scrollToRect(n, r.rects[0]), 400);
                        return;
                    }
                    if (r.method === 'no_text_layer') { setNote('Scanned page — no text to highlight.'); return; }
                } catch { /* try next */ }
            }
            if (!dead) setNote("Couldn't locate the exact text on this page.");
        })();
        return () => { dead = true; };
    }, [quote, docId, page]);

    const onPageCount = useCallback((_n: number, w: number) => {
        const width = bodyRef.current?.clientWidth || 800;
        setScale(Math.max(0.5, Math.min(1.6, (width - 56) / w)));
        requestAnimationFrame(() => { if (!ready.current) { ready.current = true; viewer.current?.scrollToPage(page); } });
    }, [page]);

    const region = hl ? regionOf(hl.rects) : null;
    const overlays: Overlay[] = hl ? [{ page: hl.page, rects: hl.rects, kind: 'hl' }, ...(region ? [{ page: hl.page, rects: [region], kind: 'region' as const }] : [])] : [];
    const title = doc ? `${doc.ticker || doc.company} · ${doc.form_type}${doc.fiscal_year ? ` FY${doc.fiscal_year}` : ''}${doc.fiscal_quarter ? ` Q${doc.fiscal_quarter}` : ''}` : 'Loading…';
    const total = doc?.page_count ?? 0;
    const step = (d: number) => {
        const n = Math.min(Math.max(1, current + d), total || current);
        setCurrent(n);
        viewer.current?.scrollToPage(n, true);
    };

    return createPortal(
        <div className="cm-backdrop" onClick={onClose}>
            <div className="cm" role="dialog" aria-modal="true" aria-label="Cited page" onClick={e => e.stopPropagation()}>
                <header className="cm-head">
                    <div className="cm-title">
                        <strong>{title}</strong>
                        <span>page {current}{total ? ` / ${total}` : ''}</span>
                    </div>
                    <div className="cm-actions">
                        <button className="docs-btn" onClick={() => step(-1)} disabled={current <= 1} aria-label="Previous page">‹</button>
                        <button className="docs-btn" onClick={() => step(1)} disabled={!!total && current >= total} aria-label="Next page">›</button>
                        <Link className="docs-btn cm-open" to={inspectorUrl(docId, current, { quote, portfolioId, sessionId })} onClick={onClose}>
                            Open in Inspector ↗
                        </Link>
                        <button className="docs-btn" onClick={onClose} aria-label="Close">✕</button>
                    </div>
                </header>
                {note && <div className="insp-banner">{note}</div>}
                {error && <div className="docs-error" role="alert">{error}</div>}
                <div className="cm-body" ref={bodyRef}>
                    {hasPdf ? (
                        <PdfViewer
                            ref={viewer} url={documentsApi.pdfUrl(docId)} scale={scale} overlays={overlays}
                            onPageCount={onPageCount} onVisiblePage={setCurrent} onError={setError}
                        />
                    ) : doc ? (
                        <div className="cm-text">
                            <p className="docs-subtitle">The PDF isn't stored locally, so this is the indexed text of the page. Open the Inspector to download it.</p>
                            <pre className="pp-pre">{text ? text.text : 'Loading…'}</pre>
                        </div>
                    ) : <div className="pdfv-loading">Loading…</div>}
                </div>
            </div>
        </div>,
        document.body,
    );
};

export default CitationModal;
