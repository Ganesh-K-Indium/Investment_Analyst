import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Sidebar } from '../../components/layout/Sidebar';
import { Header } from '../../components/layout/Header';
import { usePendingNavigationStore } from '../../store/pendingNavigationStore';
import { documentsApi, type BBox, type DocumentSummary, type PageIndexResponse, type PageResponse } from './documentsApi';
import { useDocumentsStore } from './documentsStore';
import { PdfViewer, type Overlay, type PdfViewerHandle } from './PdfViewer';
import { regionOf } from './citations';
import { PagePanel, type Tab } from './PagePanel';
import './DocumentsPage.css';
import './Inspector.css';

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const TABS: Tab[] = ['text', 'json', 'tables', 'images'];
const isTyping = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);

/** `from=chat:<portfolioId>:<sessionId>` written by citation links. */
const parseFrom = (from: string | null) => {
    const m = /^chat:([^:]+)(?::(.+))?$/.exec(from || '');
    return m ? { portfolioId: m[1], sessionId: m[2] } : null;
};

export const InspectorPage: React.FC = () => {
    const { docId = '' } = useParams();
    const [params, setParams] = useSearchParams();
    const navigate = useNavigate();
    const { ids, search: listSearch } = useDocumentsStore();
    const setPending = usePendingNavigationStore(s => s.setPending);

    const quote = params.get('hl') || '';
    const back = parseFrom(params.get('from'));
    const tabParam = params.get('tab') as Tab;
    const tab: Tab = TABS.includes(tabParam) ? tabParam : 'text';
    const initialPage = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);

    const [doc, setDoc] = useState<DocumentSummary | null>(null);
    const [docError, setDocError] = useState<string | null>(null);
    const [index, setIndex] = useState<PageIndexResponse | null>(null);
    const [pdfKey, setPdfKey] = useState(0);
    const [pdfError, setPdfError] = useState<string | null>(null);
    const [downloading, setDownloading] = useState(false);
    const [pageCount, setPageCount] = useState(0);
    const [pageWidth, setPageWidth] = useState(612);
    const [current, setCurrent] = useState(initialPage);
    const [scale, setScale] = useState(1);
    const [page, setPage] = useState<PageResponse | null>(null);
    const [pageLoading, setPageLoading] = useState(false);
    const [pageError, setPageError] = useState<string | null>(null);
    const [hoverRect, setHoverRect] = useState<BBox | null>(null);
    const [hoverBlock, setHoverBlock] = useState<number | null>(null);
    const [byBlock, setByBlock] = useState(false);
    const [hl, setHl] = useState<{ page: number; rects: BBox[] } | null>(null);
    const [hlNote, setHlNote] = useState<string | null>(null);
    const [searchQ, setSearchQ] = useState('');
    const [hits, setHits] = useState<number[] | null>(null);
    const [pageInput, setPageInput] = useState(String(initialPage));
    const [rightW, setRightW] = useState(Number(localStorage.getItem('inspector.rightW')) || 420);
    const [rightOpen, setRightOpen] = useState(true);

    const viewer = useRef<PdfViewerHandle>(null);
    const cache = useRef(new Map<string, PageResponse>());
    const searchRef = useRef<HTMLInputElement>(null);
    const railRef = useRef<HTMLDivElement>(null);
    const didInitialScroll = useRef(false);

    // ── Document + page index ───────────────────────────────────────────────
    useEffect(() => {
        setDoc(null); setDocError(null); setIndex(null); setPage(null); setHl(null); setHits(null);
        cache.current.clear(); didInitialScroll.current = false; setPdfError(null);
        documentsApi.get(docId).then(setDoc).catch(e => setDocError(e.message));
        documentsApi.pageIndex(docId).then(setIndex).catch(() => undefined);
    }, [docId]);

    const hasPdf = doc?.pdf_status === 'local';

    // ── Missing PDF: download and reopen ────────────────────────────────────
    const download = async () => {
        setDownloading(true);
        setPdfError(null);
        try {
            let st = await documentsApi.ensurePdf(docId);
            while (st.status === 'downloading') {
                await new Promise(r => setTimeout(r, 2000));
                st = await documentsApi.pdfStatus(docId);
            }
            documentsApi.forget(docId);
            setDoc(await documentsApi.get(docId));
            setPdfKey(k => k + 1);
            if (st.status !== 'local') setPdfError(st.error || 'The download failed.');
        } catch (e) { setPdfError((e as Error).message); } finally { setDownloading(false); }
    };

    // ── Page JSON (debounced, cached, neighbours prefetched) ────────────────
    useEffect(() => {
        if (!doc) return;
        const key = (n: number) => `${docId}:${n}:${doc.pdf_status}`;
        const cached = cache.current.get(key(current));
        if (cached) { setPage(cached); setPageError(null); }
        const ctl = new AbortController();
        const t = setTimeout(async () => {
            try {
                if (!cached) {
                    setPageLoading(true);
                    const r = await documentsApi.page(docId, current, ctl.signal);
                    cache.current.set(key(current), r);
                    setPage(r); setPageError(null);
                }
                [current + 1, current - 1].forEach(n => {
                    if (n >= 1 && n <= doc.page_count && !cache.current.has(key(n))) {
                        documentsApi.page(docId, n).then(r => cache.current.set(key(n), r)).catch(() => undefined);
                    }
                });
            } catch (e) { if ((e as Error).name !== 'AbortError') setPageError((e as Error).message); }
            finally { setPageLoading(false); }
        }, cached ? 0 : 120);
        return () => { clearTimeout(t); ctl.abort(); };
    }, [doc, docId, current]);

    // ── currentPage <-> URL / input / rail ─────────────────────────────────
    // Scrolling updates the URL at most every 300 ms (and not at all mid-scroll) so the router doesn't re-render per page.
    const urlTimer = useRef<number>(0);
    const syncUrl = useCallback((n: number) => {
        window.clearTimeout(urlTimer.current);
        urlTimer.current = window.setTimeout(() => {
            setParams(prev => { const p = new URLSearchParams(prev); p.set('page', String(n)); return p; }, { replace: true });
        }, 300);
    }, [setParams]);
    useEffect(() => () => window.clearTimeout(urlTimer.current), []);
    const onVisible = useCallback((n: number) => { setCurrent(n); setPageInput(String(n)); syncUrl(n); }, [syncUrl]);
    const goto = useCallback((n: number, smooth = false) => {
        const max = pageCount || doc?.page_count || 1;
        const clamped = Math.min(Math.max(1, n), max);
        viewer.current?.scrollToPage(clamped, smooth);
        setCurrent(clamped); setPageInput(String(clamped)); syncUrl(clamped);
    }, [pageCount, doc, syncUrl]);

    useEffect(() => { // keep the active thumbnail visible
        railRef.current?.querySelector('.insp-thumb.is-active')?.scrollIntoView({ block: 'nearest' });
    }, [current, index]);

    const onPageCount = useCallback((n: number, w: number) => {
        setPageCount(n); setPageWidth(w);
        requestAnimationFrame(() => {
            if (!didInitialScroll.current) { didInitialScroll.current = true; viewer.current?.scrollToPage(initialPage); }
        });
    }, [initialPage]);

    // ── Cited-text highlight ────────────────────────────────────────────────
    useEffect(() => {
        setHl(null); setHlNote(null);
        if (!quote || !doc || !hasPdf) return;
        let dead = false;
        (async () => {
            const order = doc.aligned === false ? [initialPage, initialPage + 1, initialPage - 1] : [initialPage];
            for (const n of order) {
                if (n < 1 || n > doc.page_count) continue;
                try {
                    const r = await documentsApi.locate(docId, n, quote);
                    if (dead) return;
                    if (r.rects.length) {
                        setHl({ page: n, rects: r.rects });
                        if (n !== initialPage) goto(n);
                        setTimeout(() => viewer.current?.scrollToRect(n, r.rects[0]), 500);
                        return;
                    }
                    if (r.method === 'no_text_layer') { setHlNote('This page has no text layer (scanned) — showing the cited page.'); return; }
                } catch { /* try next candidate */ }
            }
            if (!dead) setHlNote("Couldn't locate the exact cited text — showing the cited page.");
        })();
        return () => { dead = true; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [quote, doc?.doc_id, hasPdf]);

    const clearHl = useCallback(() => {
        setHl(null); setHlNote(null);
        setParams(prev => { const p = new URLSearchParams(prev); p.delete('hl'); return p; }, { replace: true });
    }, [setParams]);

    // ── Search in document ──────────────────────────────────────────────────
    const runSearch = async () => {
        if (!searchQ.trim()) { setHits(null); return; }
        const found = await viewer.current?.search(searchQ) ?? [];
        setHits(found);
        const next = found.find(n => n > current) ?? found[0];
        if (next) goto(next, true);
    };

    // ── Keyboard shortcuts ──────────────────────────────────────────────────
    useEffect(() => {
        const h = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && (hl || hlNote)) { clearHl(); return; }
            if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
            if (e.key === 'j') goto(current + 1);
            else if (e.key === 'k') goto(current - 1);
            else if (e.key === '/') { e.preventDefault(); searchRef.current?.focus(); }
            else if (e.key === '+' || e.key === '=') setScale(s => ZOOMS.find(z => z > s) ?? s);
            else if (e.key === '-') setScale(s => [...ZOOMS].reverse().find(z => z < s) ?? s);
            else if (/^[1-4]$/.test(e.key)) setParams(prev => { const p = new URLSearchParams(prev); p.set('tab', TABS[+e.key - 1]); return p; }, { replace: true });
        };
        window.addEventListener('keydown', h);
        return () => window.removeEventListener('keydown', h);
    }, [current, goto, hl, hlNote, clearHl, setParams]);

    // ── Resizable right pane ────────────────────────────────────────────────
    const startDrag = (e: React.PointerEvent) => {
        e.preventDefault();
        const move = (ev: PointerEvent) => setRightW(Math.min(Math.max(window.innerWidth - ev.clientX, 280), 900));
        const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
    };
    useEffect(() => { localStorage.setItem('inspector.rightW', String(rightW)); }, [rightW]);

    // ── Overlays ────────────────────────────────────────────────────────────
    const overlays = useMemo<Overlay[]>(() => {
        const o: Overlay[] = [];
        if (hl) {
            o.push({ page: hl.page, rects: hl.rects, kind: 'hl' });
            const region = regionOf(hl.rects);
            if (region) o.push({ page: hl.page, rects: [region], kind: 'region' });
        }
        if (hoverRect) o.push({ page: current, rects: [hoverRect], kind: 'hover' });
        if (page && byBlock && tab === 'text') {
            o.push({ page: page.page, rects: page.blocks.map(b => b.bbox), kind: 'block' });
            if (hoverBlock !== null && page.blocks[hoverBlock]) o.push({ page: page.page, rects: [page.blocks[hoverBlock].bbox], kind: 'hover' });
        }
        return o;
    }, [hl, hoverRect, current, page, byBlock, tab, hoverBlock]);

    // ── Previous / next document inside the library's current view ─────────
    const at = ids.indexOf(docId);
    const prevId = at > 0 ? ids[at - 1] : null;
    const nextId = at >= 0 && at < ids.length - 1 ? ids[at + 1] : null;
    const toDoc = (id: string) => navigate(`/documents/${encodeURIComponent(id)}`);
    const libraryUrl = `/documents${listSearch ? `?${listSearch}` : ''}`;

    const goBackToChat = () => {
        if (!back) return;
        setPending({ portfolioId: back.portfolioId, tab: 'Secondary Research', sessionId: back.sessionId });
        navigate('/home');
    };

    const fit = () => setScale(Math.max(0.4, Math.min(2.5, ((document.querySelector('.insp-left')?.clientWidth || 700) - 72) / pageWidth)));
    const total = pageCount || doc?.page_count || 0;

    return (
        <div className="docs-page">
            <Sidebar />
            <Header />
            <main className="docs-page__main insp">
                <div className="insp-top">
                    <nav className="insp-crumbs" aria-label="Breadcrumb">
                        {back && <button className="docs-btn" onClick={goBackToChat}>← Back to chat</button>}
                        <Link to={libraryUrl} className="docs-link">{back ? 'Documents' : '← Documents'}</Link>
                        {doc && <span>› <strong>{doc.ticker || doc.company}</strong> › {doc.form_type}{doc.fiscal_year ? ` · FY${doc.fiscal_year}` : ''}{doc.fiscal_quarter ? ` Q${doc.fiscal_quarter}` : ''}</span>}
                    </nav>
                    <div className="insp-top__actions">
                        {at >= 0 && <span className="docs-subtitle">{at + 1} of {ids.length}</span>}
                        <button className="docs-btn" disabled={!prevId} onClick={() => prevId && toDoc(prevId)} aria-label="Previous document">‹ Prev doc</button>
                        <button className="docs-btn" disabled={!nextId} onClick={() => nextId && toDoc(nextId)} aria-label="Next document">Next doc ›</button>
                        <button className="docs-btn" onClick={() => setRightOpen(o => !o)} aria-pressed={rightOpen}>{rightOpen ? 'Hide' : 'Show'} text</button>
                    </div>
                </div>

                {docError && <div className="docs-error" role="alert">{docError}</div>}
                {doc?.pdf_status === 'local' && doc.aligned === false && (
                    <div className="insp-banner" role="status">This PDF's pagination differs from the indexed copy, so page numbers and highlights may be off by a page.</div>
                )}
                {hlNote && <div className="insp-banner" role="status">{hlNote} <button className="docs-link" onClick={clearHl}>Dismiss</button></div>}
                {hl && <div className="insp-banner insp-banner--hl" role="status">Highlighting cited text on page {hl.page}. <button className="docs-link" onClick={clearHl}>Clear highlight (Esc)</button></div>}

                <div className="insp-grid" style={{ gridTemplateColumns: `76px minmax(0,1fr) ${rightOpen ? `6px ${rightW}px` : ''}` }}>
                    <div className="insp-rail" ref={railRef} aria-label="Pages">
                        {(index?.pages ?? Array.from({ length: total }, (_, i) => ({ page: i + 1, chars: 0, has_tables: false, has_images: false }))).map(p => (
                            <button
                                key={p.page} className={`insp-thumb${p.page === current ? ' is-active' : ''}${hits?.includes(p.page) ? ' is-hit' : ''}`}
                                onClick={() => goto(p.page)} aria-current={p.page === current ? 'page' : undefined}
                                title={`Page ${p.page}${p.chars === 0 ? ' · no text' : ''}`}
                            >
                                {p.page}
                                <span className="insp-dots">
                                    {p.has_tables && <i className="insp-dot insp-dot--table" />}
                                    {p.has_images && <i className="insp-dot insp-dot--img" />}
                                    {index && p.chars === 0 && <i className="insp-dot insp-dot--empty" />}
                                </span>
                            </button>
                        ))}
                    </div>

                    <div className="insp-left">
                        <div className="insp-toolbar">
                            <span>Page <input
                                className="docs-input insp-pageinput" value={pageInput} aria-label="Go to page"
                                onChange={e => setPageInput(e.target.value.replace(/\D/g, ''))}
                                onKeyDown={e => { if (e.key === 'Enter' && pageInput) goto(parseInt(pageInput, 10), true); }}
                            /> / {total || '…'}</span>
                            <span className="insp-zoom">
                                <button className="docs-btn" aria-label="Zoom out" onClick={() => setScale(s => [...ZOOMS].reverse().find(z => z < s) ?? s)}>−</button>
                                <span>{Math.round(scale * 100)}%</span>
                                <button className="docs-btn" aria-label="Zoom in" onClick={() => setScale(s => ZOOMS.find(z => z > s) ?? s)}>+</button>
                                <button className="docs-btn" onClick={fit}>Fit</button>
                            </span>
                            <span className="insp-search">
                                <input
                                    ref={searchRef} className="docs-input" type="search" placeholder="Search in PDF  ( / )" value={searchQ} aria-label="Search in document"
                                    onChange={e => { setSearchQ(e.target.value); if (!e.target.value) setHits(null); }}
                                    onKeyDown={e => { if (e.key === 'Enter') runSearch(); if (e.key === 'Escape') e.currentTarget.blur(); }}
                                />
                                {hits && <span className="docs-subtitle">{hits.length ? `${hits.length} page${hits.length > 1 ? 's' : ''}` : 'No matches'}</span>}
                            </span>
                        </div>

                        {hasPdf ? (
                            <PdfViewer
                                key={`${docId}:${pdfKey}`} ref={viewer} url={documentsApi.pdfUrl(docId)} scale={scale} overlays={overlays}
                                onPageCount={onPageCount} onVisiblePage={onVisible} onError={setPdfError}
                                onBlockHover={(pg, i) => { if (pg === current) setHoverBlock(i); }}
                            />
                        ) : doc ? (
                            <div className="insp-missing">
                                <h3>PDF not available locally</h3>
                                <p>The extracted text and JSON still work. Download the filing from SEC EDGAR to view the original pages.</p>
                                <button className="docs-btn" onClick={download} disabled={downloading || doc.pdf_status === 'unsupported'}>
                                    {downloading ? 'Downloading…' : doc.pdf_status === 'unsupported' ? 'Not downloadable' : 'Download PDF'}
                                </button>
                            </div>
                        ) : <div className="pdfv-loading">Loading…</div>}
                        {pdfError && <div className="docs-error" role="alert">{pdfError}</div>}
                    </div>

                    {rightOpen && (
                        <>
                            <div className="insp-divider" role="separator" aria-orientation="vertical" aria-label="Resize panel" onPointerDown={startDrag} />
                            <PagePanel
                                page={page} loading={pageLoading} error={pageError} tab={tab}
                                onTab={t => setParams(prev => { const p = new URLSearchParams(prev); p.set('tab', t); return p; }, { replace: true })}
                                hoverBlock={hoverBlock} onHoverBlock={setHoverBlock} onHoverRect={setHoverRect}
                                byBlock={byBlock} onByBlock={setByBlock} quote={quote}
                            />
                        </>
                    )}
                </div>
            </main>
        </div>
    );
};

export default InspectorPage;
