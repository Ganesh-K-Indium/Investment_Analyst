// URL <-> filter state for the Documents library. The URL is the source of truth, e.g.
//   /documents?ticker=AAPL,MSFT&form=10-K,10-Q&year=2023-2025&pdf=local&sort=ticker&order=asc

import type { ListQuery, PdfStatus, SortKey } from './documentsApi';

export interface DocFilters {
    ticker: string[];
    form: string[];
    yearFrom?: number;
    yearTo?: number;
    quarter?: number;
    q: string;
    pdf?: PdfStatus;
    images?: boolean;
    sort: SortKey;
    order: 'asc' | 'desc';
    group: boolean;
}

export const DEFAULT_FILTERS: DocFilters = { ticker: [], form: [], q: '', sort: 'filing_date', order: 'desc', group: false };

const SORTS: SortKey[] = ['filing_date', 'ticker', 'ingested_at', 'pages'];
const PDF: PdfStatus[] = ['local', 'missing', 'downloading', 'failed', 'unsupported'];
const list = (v: string | null) => (v ? v.split(',').map(s => s.trim()).filter(Boolean) : []);
const int = (v: string | null) => (v && /^\d+$/.test(v) ? parseInt(v, 10) : undefined);

export function parseFilters(p: URLSearchParams): DocFilters {
    const [from, to] = (p.get('year') || '').split('-');
    const sort = p.get('sort') as SortKey;
    const pdf = p.get('pdf') as PdfStatus;
    return {
        ticker: list(p.get('ticker')).map(t => t.toUpperCase()),
        form: list(p.get('form')),
        yearFrom: int(from || null),
        yearTo: int((to ?? from) || null),
        quarter: int(p.get('quarter')),
        q: p.get('q') || '',
        pdf: PDF.includes(pdf) ? pdf : undefined,
        images: p.get('images') === '1' ? true : p.get('images') === '0' ? false : undefined,
        sort: SORTS.includes(sort) ? sort : DEFAULT_FILTERS.sort,
        order: p.get('order') === 'asc' ? 'asc' : 'desc',
        group: p.get('group') === '1',
    };
}

/** Only non-default values are written, so a clean library has a clean URL. */
export function filtersToParams(f: DocFilters): URLSearchParams {
    const p = new URLSearchParams();
    if (f.ticker.length) p.set('ticker', f.ticker.join(','));
    if (f.form.length) p.set('form', f.form.join(','));
    if (f.yearFrom !== undefined || f.yearTo !== undefined) {
        p.set('year', f.yearFrom === f.yearTo ? `${f.yearFrom}` : `${f.yearFrom ?? ''}-${f.yearTo ?? ''}`);
    }
    if (f.quarter) p.set('quarter', String(f.quarter));
    if (f.q) p.set('q', f.q);
    if (f.pdf) p.set('pdf', f.pdf);
    if (f.images !== undefined) p.set('images', f.images ? '1' : '0');
    if (f.sort !== DEFAULT_FILTERS.sort) p.set('sort', f.sort);
    if (f.order !== DEFAULT_FILTERS.order) p.set('order', f.order);
    if (f.group) p.set('group', '1');
    return p;
}

export function filtersToQuery(f: DocFilters): ListQuery {
    return {
        ticker: f.ticker, form: f.form, year_from: f.yearFrom, year_to: f.yearTo, quarter: f.quarter,
        q: f.q.trim() || undefined, pdf_status: f.pdf, has_images: f.images, sort: f.sort, order: f.order,
    };
}

export const hasActiveFilters = (f: DocFilters) =>
    f.ticker.length > 0 || f.form.length > 0 || f.yearFrom !== undefined || f.yearTo !== undefined ||
    f.quarter !== undefined || !!f.q || f.pdf !== undefined || f.images !== undefined;
