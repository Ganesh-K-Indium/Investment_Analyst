import type { BBox } from './documentsApi';
// Citation helpers: build per-file citations from retrieved docs (with the Document
// Inspector docId and a best-guess cited quote per page) and the deep links into the Inspector.

export interface DocCitation {
    file: string;
    pages: number[];
    company?: string;
    ticker?: string;
    filingType?: string;
    periodEnd?: string;
    year?: number;
    filePath?: string;
    docId?: string;
    sourceType?: string; // 'analyst_note' for the user's own notes (no PDF behind them)
    quotes?: Record<number, string>;
    answer?: string; // the answer text this citation supports (lets the quote be re-derived after a reload)
}

const TABLES_MARKER = '\n\n[TABLES]\n';
const tokens = (s: string) => s.toLowerCase().match(/[a-z0-9$%.]+/g)?.map(t => t.replace(/^\.+|\.+$/g, '')).filter(Boolean) ?? [];
const STOP = new Set(['the', 'and', 'for', 'that', 'with', 'from', 'this', 'was', 'were', 'are', 'has', 'have', 'its', 'our', 'their', 'than', 'which']);

/** Sentence on the cited page that best supports the answer: token overlap, numbers weighted double. No LLM call. */
export function pickQuote(pageContent: string, answer: string): string | undefined {
    const text = (pageContent || '').split(TABLES_MARKER)[0];
    const answerTokens = new Set(tokens(answer).filter(t => !STOP.has(t)));
    let best: { s: string; score: number } | undefined;
    for (const raw of text.split(/(?<=[.!?])\s+(?=[A-Z0-9"“(])|\n{2,}/)) {
        const s = raw.replace(/\s+/g, ' ').trim();
        if (s.length < 40 || s.length > 400) continue;
        const toks = tokens(s).filter(t => !STOP.has(t));
        if (toks.length < 5) continue;
        let hit = 0, weight = 0;
        for (const t of toks) {
            const w = /\d/.test(t) ? 2 : 1;
            weight += w;
            if (answerTokens.has(t)) hit += w;
        }
        const score = hit / weight;
        if (hit >= 4 && (!best || score > best.score)) best = { s, score };
    }
    return best && best.score >= 0.3 ? best.s : undefined;
}

/** Table row of the cited chunk that best matches the answer (numbers weighted), as plain text for locating on the PDF page. */
export function pickTableQuote(pageContent: string, answer: string): string | undefined {
    const part = (pageContent || '').split(TABLES_MARKER)[1] ?? ((pageContent || '').startsWith('[TABLES]') ? pageContent : '');
    if (!part) return undefined;
    const answerTokens = new Set(tokens(answer).filter(t => !STOP.has(t)));
    let best: { s: string; score: number } | undefined;
    for (const line of part.split('\n')) {
        if (!line.includes('|') || /^[\s|:-]+$/.test(line)) continue;
        const s = line.split('|').map(c => c.trim()).filter(Boolean).join(' ');
        const toks = tokens(s);
        if (toks.length < 3) continue;
        const score = toks.reduce((n, t) => n + (answerTokens.has(t) ? (/\d/.test(t) ? 2 : 1) : 0), 0);
        if (score >= 3 && (!best || score > best.score)) best = { s, score };
    }
    return best?.s;
}

/** One padded box around all highlight rects (the "boundary" of the cited data). */
export function regionOf(rects: number[][], pad = 4): BBox | null {
    if (!rects.length) return null;
    return [
        Math.min(...rects.map(r => r[0])) - pad, Math.min(...rects.map(r => r[1])) - pad,
        Math.max(...rects.map(r => r[2])) + pad, Math.max(...rects.map(r => r[3])) + pad,
    ];
}

export interface RetrievedDoc { page_content?: string; content?: string; quote?: string; metadata?: Record<string, any> } // eslint-disable-line @typescript-eslint/no-explicit-any

export function buildCitations(docs: RetrievedDoc[] | undefined, answer: string): DocCitation[] {
    const map = new Map<string, DocCitation & { pageSet: Set<number>; quoteScore: Record<number, number> }>();
    docs?.forEach(doc => {
        const m = doc?.metadata ?? {};
        const file = m.source_file || m.source;
        if (!file) return;
        let c = map.get(file);
        if (!c) {
            c = {
                file, pages: [], pageSet: new Set(), quoteScore: {}, quotes: {}, answer,
                company: m.company, ticker: m.ticker, filingType: m.filing_type, periodEnd: m.period_end_date,
                year: m.year, filePath: m.source_file, docId: m.content_hash, sourceType: m.source_type,
            };
            map.set(file, c);
        }
        const page = m.page_num;
        if (page === undefined || page === null) return;
        c.pageSet.add(page);
        if (m.content_type !== 'image' && answer) {
            // The server picks the quote when the answer is generated; the local pick only covers older saved chats.
            const content = doc.page_content ?? doc.content ?? '';
            const q = doc.quote ?? pickQuote(content, answer) ?? pickTableQuote(content, answer);
            if (q) {
                const score = tokens(q).filter(t => answer.toLowerCase().includes(t)).length;
                if (score > (c.quoteScore[page] ?? 0)) { c.quoteScore[page] = score; c.quotes![page] = q; }
            }
        }
    });
    return [...map.values()].map(c => {
        const { pageSet, quoteScore, ...rest } = c;
        void quoteScore;
        return { ...rest, pages: [...pageSet].sort((a, b) => a - b) };
    });
}

/** Quote for a cited page worked out from the page's own text, for citations restored from saved history. */
export function quoteFromPage(page: { text: string; tables: { markdown: string }[] }, answer: string): string | undefined {
    const content = `${page.text}${TABLES_MARKER}${page.tables.map(t => t.markdown).join('\n\n')}`;
    return pickQuote(content, answer) ?? pickTableQuote(content, answer);
}

export function inspectorUrl(docId: string, page: number, opts: { quote?: string; portfolioId?: string; sessionId?: string } = {}) {
    const p = new URLSearchParams({ page: String(page) });
    if (opts.quote) p.set('hl', opts.quote);
    if (opts.portfolioId) p.set('from', `chat:${opts.portfolioId}${opts.sessionId ? `:${opts.sessionId}` : ''}`);
    return `/documents/${encodeURIComponent(docId)}?${p}`;
}
