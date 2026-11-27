import { memo, useEffect, useImperativeHandle, useRef, useState, forwardRef } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import './textLayer.css';
import { getAuthHeader } from '../../services/authHeader';
import type { BBox } from './documentsApi';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export interface Overlay { page: number; rects: BBox[]; kind: 'hl' | 'region' | 'hover' | 'block' }

export interface PdfViewerHandle {
    scrollToPage: (page: number, smooth?: boolean) => void;
    scrollToRect: (page: number, rect: BBox) => void;
    /** Pages whose text contains `q` (case-insensitive), scanned in the background. */
    search: (q: string) => Promise<number[]>;
}

interface Props {
    url: string;
    scale: number;
    overlays: Overlay[];
    onPageCount: (n: number, pageWidth: number) => void;
    onVisiblePage: (n: number) => void;
    onBlockHover?: (page: number, idx: number | null) => void;
    onError: (msg: string) => void;
}

interface PageProps {
    pdf: pdfjs.PDFDocumentProxy;
    num: number;
    scale: number;
    base: { w: number; h: number };
    overlays: Overlay[];
    onBlockHover?: (page: number, idx: number | null) => void;
}

const PageView = memo(({ pdf, num, scale, base, overlays, onBlockHover }: PageProps) => {
    const wrap = useRef<HTMLDivElement>(null);
    const canvas = useRef<HTMLCanvasElement>(null);
    const textRef = useRef<HTMLDivElement>(null);
    const [near, setNear] = useState(false);
    const [measured, setMeasured] = useState<{ scale: number; w: number; h: number } | null>(null);
    const size = measured && measured.scale === scale ? measured : { w: base.w * scale, h: base.h * scale };

    useEffect(() => {
        const el = wrap.current;
        if (!el) return;
        const io = new IntersectionObserver(([e]) => setNear(e.isIntersecting), { rootMargin: '1500px 0px' });
        io.observe(el);
        return () => io.disconnect();
    }, []);

    useEffect(() => {
        if (!near) return;
        let cancelled = false;
        let task: pdfjs.RenderTask | null = null;
        let text: pdfjs.TextLayer | null = null;
        const c = canvas.current, tl = textRef.current;
        (async () => {
            const page = await pdf.getPage(num);
            if (cancelled || !c || !tl) return;
            const viewport = page.getViewport({ scale });
            const dpr = window.devicePixelRatio || 1;
            c.width = Math.floor(viewport.width * dpr);
            c.height = Math.floor(viewport.height * dpr);
            c.style.width = `${viewport.width}px`;
            c.style.height = `${viewport.height}px`;
            setMeasured({ scale, w: viewport.width, h: viewport.height });
            task = page.render({
                canvas: c, canvasContext: c.getContext('2d')!, viewport,
                transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
            });
            try { await task.promise; } catch { return; }
            if (cancelled || !tl) return;
            tl.replaceChildren();
            text = new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: tl, viewport });
            text.render().catch(() => undefined);
        })();
        return () => {
            cancelled = true;
            task?.cancel();
            text?.cancel();
            if (c) { c.width = 0; c.height = 0; }
            tl?.replaceChildren();
        };
    }, [near, pdf, num, scale]);

    return (
        <div
            ref={wrap} className="pdfv-page" data-page={num} style={{ width: size.w, height: size.h, ['--total-scale-factor' as string]: scale }}
        >
            <canvas ref={canvas} />
            <div className="pdfv-overlays">
                {overlays.flatMap((o, oi) => o.rects.map((r, ri) => (
                    <div
                        key={`${oi}-${ri}`} className={`pdfv-ov pdfv-ov--${o.kind}`}
                        style={{ left: r[0] * scale, top: r[1] * scale, width: (r[2] - r[0]) * scale, height: (r[3] - r[1]) * scale }}
                        onMouseEnter={o.kind === 'block' ? () => onBlockHover?.(num, ri) : undefined}
                        onMouseLeave={o.kind === 'block' ? () => onBlockHover?.(num, null) : undefined}
                    />
                )))}
            </div>
            <div ref={textRef} className="textLayer" />
            <span className="pdfv-num">{num}</span>
        </div>
    );
});

export const PdfViewer = forwardRef<PdfViewerHandle, Props>(({ url, scale, overlays, onPageCount, onVisiblePage, onBlockHover, onError }, ref) => {
    const [pdf, setPdf] = useState<pdfjs.PDFDocumentProxy | null>(null);
    const [base, setBase] = useState({ w: 612, h: 792 });
    const scroller = useRef<HTMLDivElement>(null);
    const lastVisible = useRef(0);

    useEffect(() => {
        const task = pdfjs.getDocument({ url, httpHeaders: getAuthHeader(), disableAutoFetch: true, rangeChunkSize: 262144 });
        let cancelled = false; // StrictMode / unmount destroys the task; its "Loading aborted" rejection is not an error
        task.promise.then(async d => {
            const p1 = await d.getPage(1);
            if (cancelled) return;
            const v = p1.getViewport({ scale: 1 });
            setBase({ w: v.width, h: v.height });
            setPdf(d);
            onPageCount(d.numPages, v.width);
        }).catch(e => { if (!cancelled) onError(e?.message || 'Could not open the PDF'); });
        return () => { cancelled = true; task.destroy(); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [url]);

    const pageEl = (n: number) => scroller.current?.querySelector<HTMLElement>(`[data-page="${n}"]`) || null;
    useImperativeHandle(ref, () => ({
        scrollToPage: (n, smooth) => pageEl(n)?.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' }),
        scrollToRect: (n, r) => {
            const el = pageEl(n), sc = scroller.current;
            if (el && sc) sc.scrollTo({ top: el.offsetTop + r[1] * scale - sc.clientHeight / 3, behavior: 'smooth' });
        },
        search: async q => {
            const needle = q.trim().toLowerCase();
            if (!pdf || !needle) return [];
            const hits: number[] = [];
            for (let n = 1; n <= pdf.numPages; n++) {
                const tc = await (await pdf.getPage(n)).getTextContent();
                const text = tc.items.map(i => ('str' in i ? i.str : '')).join(' ').toLowerCase();
                if (text.includes(needle)) hits.push(n);
            }
            return hits;
        },
    }));

    // Visible page = the page under the top third of the scroll viewport.
    useEffect(() => {
        const sc = scroller.current;
        if (!sc || !pdf) return;
        let raf = 0;
        const check = () => {
            raf = 0;
            const probe = sc.scrollTop + sc.clientHeight / 3;
            let lo = 1, hi = pdf.numPages;
            const pages = sc.children[0]?.children;
            if (!pages) return;
            while (lo < hi) { // binary search on offsetTop
                const mid = (lo + hi + 1) >> 1;
                if ((pages[mid - 1] as HTMLElement).offsetTop <= probe) lo = mid; else hi = mid - 1;
            }
            if (lo !== lastVisible.current) { lastVisible.current = lo; onVisiblePage(lo); }
        };
        const onScroll = () => { if (!raf) raf = requestAnimationFrame(check); };
        sc.addEventListener('scroll', onScroll, { passive: true });
        return () => { sc.removeEventListener('scroll', onScroll); if (raf) cancelAnimationFrame(raf); };
    }, [pdf, onVisiblePage]);

    if (!pdf) return <div className="pdfv-loading">Opening PDF…</div>;
    return (
        <div className="pdfv-scroll" ref={scroller}>
            <div className="pdfv-pages">
                {Array.from({ length: pdf.numPages }, (_, i) => i + 1).map(n => (
                    <PageView
                        key={n} pdf={pdf} num={n} scale={scale} base={base} onBlockHover={onBlockHover}
                        overlays={overlays.filter(o => o.page === n)}
                    />
                ))}
            </div>
        </div>
    );
});
