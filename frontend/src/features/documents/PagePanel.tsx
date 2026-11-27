import React, { useEffect, useRef, useState } from 'react';
import type { BBox, PageResponse, PageTable } from './documentsApi';

export type Tab = 'text' | 'json' | 'tables' | 'images';
const TABS: { id: Tab; label: string }[] = [
    { id: 'text', label: 'Text' }, { id: 'json', label: 'JSON' }, { id: 'tables', label: 'Tables' }, { id: 'images', label: 'Images' },
];

// ── JSON tree (in-house, collapsible) ──────────────────────────────────────
const JsonNode: React.FC<{ name?: string; value: unknown; depth: number }> = ({ name, value, depth }) => {
    const [open, setOpen] = useState(depth < 1);
    const label = name !== undefined ? <span className="json-key">{name}: </span> : null;
    if (value === null || typeof value !== 'object') {
        const cls = typeof value === 'string' ? 'json-str' : 'json-lit';
        const shown = typeof value === 'string' && value.length > 400 ? `${value.slice(0, 400)}… (${value.length} chars)` : JSON.stringify(value);
        return <div className="json-row">{label}<span className={cls}>{shown}</span></div>;
    }
    const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value);
    const brackets = Array.isArray(value) ? ['[', ']'] : ['{', '}'];
    return (
        <div className="json-row">
            <button className="json-toggle" aria-expanded={open} onClick={() => setOpen(o => !o)}>{open ? '▾' : '▸'}</button>
            {label}<span className="json-lit">{brackets[0]}{!open && ` ${entries.length} ${Array.isArray(value) ? 'items' : 'keys'} `}{!open && brackets[1]}</span>
            {open && (
                <div className="json-children">
                    {entries.map(([k, v]) => <JsonNode key={k} name={k} value={v} depth={depth + 1} />)}
                    <span className="json-lit">{brackets[1]}</span>
                </div>
            )}
        </div>
    );
};

// ── Tables ────────────────────────────────────────────────────────────────
const NUMERIC = /^[($-]*\s*[\d,.]+\s*[)%]*$|^[—–-]$/;

const GridTable: React.FC<{ headers: string[]; rows: string[][] }> = ({ headers, rows }) => (
    <div className="pp-tablewrap">
        <table className="pp-table">
            <thead><tr>{headers.map((c, i) => <th key={i} className={i ? 'num' : ''}>{c}</th>)}</tr></thead>
            <tbody>
                {rows.map((r, i) => {
                    const sectionRow = r.slice(1).every(c => !c);
                    return (
                        <tr key={i} className={sectionRow ? 'is-section' : ''}>
                            {headers.map((_, j) => <td key={j} className={j && NUMERIC.test((r[j] || '').trim()) ? 'num' : j ? '' : 'lbl'}>{r[j] || ''}</td>)}
                        </tr>
                    );
                })}
            </tbody>
        </table>
    </div>
);

/** Pipe-table markdown -> cells (fallback when the page has no PDF to rebuild structured cells from). */
const parseMarkdown = (md: string): { headers: string[]; rows: string[][] } | null => {
    const rows = md.trim().split('\n').filter(l => l.includes('|')).map(l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim()));
    const body = rows.slice(1).filter(r => !r.every(c => /^:?-{2,}:?$/.test(c)));
    return rows[0] ? { headers: rows[0], rows: body } : null;
};

const TableView: React.FC<{ table: PageTable }> = ({ table }) => {
    const grid = table.headers && table.rows ? { headers: table.headers, rows: table.rows } : parseMarkdown(table.markdown);
    return grid ? <GridTable {...grid} /> : <pre className="pp-pre">{table.markdown}</pre>;
};

interface Props {
    page: PageResponse | null;
    loading: boolean;
    error: string | null;
    tab: Tab;
    onTab: (t: Tab) => void;
    hoverBlock: number | null;
    onHoverBlock: (i: number | null) => void;
    onHoverRect: (r: BBox | null) => void;
    byBlock: boolean;
    onByBlock: (v: boolean) => void;
    quote?: string;
}

export const PagePanel: React.FC<Props> = ({ page, loading, error, tab, onTab, hoverBlock, onHoverBlock, onHoverRect, byBlock, onByBlock, quote }) => {
    const [copied, setCopied] = useState(false);
    const blockRefs = useRef<(HTMLParagraphElement | null)[]>([]);

    useEffect(() => { // scroll the hovered PDF block into view in the list
        if (byBlock && hoverBlock !== null) blockRefs.current[hoverBlock]?.scrollIntoView({ block: 'nearest' });
    }, [hoverBlock, byBlock]);

    const json = page ? JSON.stringify(page, null, 2) : '';
    const copy = async () => { await navigator.clipboard.writeText(json); setCopied(true); setTimeout(() => setCopied(false), 1500); };
    const download = () => {
        if (!page) return;
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
        a.download = `${page.doc_id.slice(0, 8)}-page-${page.page}.json`;
        a.click();
        URL.revokeObjectURL(a.href);
    };

    const onTabKey = (e: React.KeyboardEvent) => {
        const i = TABS.findIndex(t => t.id === tab);
        if (e.key === 'ArrowRight') onTab(TABS[(i + 1) % TABS.length].id);
        if (e.key === 'ArrowLeft') onTab(TABS[(i + TABS.length - 1) % TABS.length].id);
    };

    const renderQuote = (text: string) => {
        if (!quote) return text;
        const at = text.toLowerCase().indexOf(quote.toLowerCase().slice(0, 60));
        if (at < 0) return text;
        return <>{text.slice(0, at)}<mark>{text.slice(at, at + quote.length)}</mark>{text.slice(at + quote.length)}</>;
    };

    return (
        <section className="pp">
            <div className="pp-tabs" role="tablist" aria-label="Extracted page content" onKeyDown={onTabKey}>
                {TABS.map(t => (
                    <button
                        key={t.id} role="tab" id={`tab-${t.id}`} aria-selected={tab === t.id} aria-controls="pp-body" tabIndex={tab === t.id ? 0 : -1}
                        className={`pp-tab${tab === t.id ? ' is-active' : ''}`} onClick={() => onTab(t.id)}
                    >
                        {t.label}
                        {page && t.id === 'tables' && page.tables.length > 0 && <em>{page.tables.length}</em>}
                        {page && t.id === 'images' && page.images.length > 0 && <em>{page.images.length}</em>}
                    </button>
                ))}
            </div>
            <div className="pp-body" id="pp-body" role="tabpanel" aria-labelledby={`tab-${tab}`}>
                {error && <div className="docs-error" role="alert">{error}</div>}
                {!page && loading && <div className="docs-state">Loading page…</div>}
                {page && (
                    <>
                        {tab === 'text' && (
                            <>
                                <div className="pp-bar">
                                    <span>{page.text.length.toLocaleString()} chars · page {page.page}</span>
                                    <label className="docs-toggle" title="Show PyMuPDF text blocks; hover maps to the PDF">
                                        <input type="checkbox" checked={byBlock} disabled={page.blocks.length === 0} onChange={e => onByBlock(e.target.checked)} /> By block
                                    </label>
                                </div>
                                {byBlock && page.blocks.length > 0 ? page.blocks.map((b, i) => (
                                    <p
                                        key={b.id} ref={el => { blockRefs.current[i] = el; }}
                                        className={`pp-block${hoverBlock === i ? ' is-hover' : ''}`}
                                        onMouseEnter={() => onHoverBlock(i)} onMouseLeave={() => onHoverBlock(null)}
                                    >{b.text}</p>
                                )) : <pre className="pp-pre">{page.text ? renderQuote(page.text) : 'No text was extracted for this page.'}</pre>}
                            </>
                        )}
                        {tab === 'json' && (
                            <>
                                <div className="pp-bar">
                                    <span>Page JSON</span>
                                    <span><button className="docs-link" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button><button className="docs-link" onClick={download}>Download</button></span>
                                </div>
                                <JsonNode value={page} depth={0} />
                            </>
                        )}
                        {tab === 'tables' && (page.tables.length === 0
                            ? <div className="docs-state">No tables on this page.</div>
                            : page.tables.map((t, i) => (
                                <div key={i} className="pp-card" onMouseEnter={() => onHoverRect(t.bbox)} onMouseLeave={() => onHoverRect(null)}>
                                    <div className="pp-card__title">
                                        Table {i + 1}
                                        {t.verified && (
                                            <span className={`pp-verified${t.verified.matched === t.verified.numbers ? '' : ' is-partial'}`} title="Numbers printed in this table area on the PDF vs numbers found in the extracted cells">
                                                {t.verified.matched === t.verified.numbers ? '✓' : '!'} {t.verified.matched}/{t.verified.numbers} numbers verified
                                            </span>
                                        )}
                                        <em> · {t.source === 'pdf' ? 'Extracted from PDF' : 'Extracted at ingestion (PDF not available)'}{!t.bbox && ' · location unknown'}</em>
                                    </div>
                                    <TableView table={t} />
                                </div>
                            )))}
                        {tab === 'images' && (page.images.length === 0
                            ? <div className="docs-state">No charts or images on this page.</div>
                            : page.images.map((im, i) => (
                                <div key={im.image_id || i} className="pp-card" onMouseEnter={() => onHoverRect(im.bbox)} onMouseLeave={() => onHoverRect(null)}>
                                    <div className="pp-card__title">{im.type || 'Image'}{!im.bbox && <em> · location unknown</em>}</div>
                                    {im.summary && <p className="pp-summary">{im.summary}</p>}
                                    <details><summary>Full vision extraction</summary><pre className="pp-pre">{im.analysis}</pre></details>
                                </div>
                            )))}
                    </>
                )}
            </div>
        </section>
    );
};
