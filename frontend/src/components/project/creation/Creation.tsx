import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useAuth } from '../../../context/useAuth';
import { useClipboardStore } from '../../../store/clipboardStore';
import { useReportEditorStore } from '../../../store/reportEditorStore';
import { chatApi, type SummaryItem, type ChatSessionResponse } from '../../../services/chatApi';
import { reportApi, type ReportResponse, type DraftItem } from '../../../services/reportApi';
import ReactMarkdown from 'react-markdown';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { TextStyle } from '@tiptap/extension-text-style';
import Color from '@tiptap/extension-color';
import Highlight from '@tiptap/extension-highlight';
import Underline from '@tiptap/extension-underline';
import TextAlign from '@tiptap/extension-text-align';
import { Table } from '@tiptap/extension-table';
import TableRow from '@tiptap/extension-table-row';
import TableCell from '@tiptap/extension-table-cell';
import TableHeader from '@tiptap/extension-table-header';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import { ResizableImage } from './ResizableImage';
import { FontSize } from './FontSize';
import { ResizableSidebar, EmptyState, Tooltip, TooltipTrigger, TooltipContent } from '../../ui';
import { useResizableSidebar } from '../../../hooks/useResizableSidebar';
import './Creation.css';
import { cssVar } from '../../../lib/cssVar';

const SANITIZE_CONFIG = {
    ADD_TAGS: ['iframe'] as string[],
    ADD_ATTR: ['target'] as string[],
};

function sanitizeHtml(html: string): string {
    return DOMPurify.sanitize(html, SANITIZE_CONFIG);
}

// Swatches of the report editor. The picked value is written into the report, so they are fixed document tokens
// (--color-doc-ink-*, --color-doc-mark-* in index.css), not theme colours.
const docColors = (prefix: string, n: number) => Array.from({ length: n }, (_, i) => cssVar(`${prefix}${i + 1}`));
const FONT_SIZES = ['12px', '14px', '16px', '18px', '20px', '24px', '28px', '32px', '40px'];

interface ColorPopoverProps {
    label: string;
    colors: string[];
    onPick: (color: string) => void;
    onClear?: () => void;
    icon: React.ReactNode;
}

function ColorPopover({ label, colors, onPick, onClear, icon }: ColorPopoverProps) {
    const [open, setOpen] = useState(false);
    const wrapRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        const onDocClick = (e: MouseEvent) => {
            if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener('mousedown', onDocClick);
        return () => document.removeEventListener('mousedown', onDocClick);
    }, [open]);

    return (
        <div className="creation__color-popover-wrap" ref={wrapRef}>
            <ToolbarButton label={label} onClick={() => setOpen(o => !o)}>{icon}</ToolbarButton>
            {open && (
                <div className="creation__color-popover">
                    {onClear && (
                        <button type="button" className="creation__color-clear" onClick={() => { onClear(); setOpen(false); }}>
                            Clear
                        </button>
                    )}
                    <div className="creation__color-swatches">
                        {colors.map(c => (
                            <button
                                key={c}
                                type="button"
                                className="creation__color-swatch"
                                style={{ backgroundColor: c }}
                                onClick={() => { onPick(c); setOpen(false); }}
                                aria-label={c}
                            />
                        ))}
                    </div>
                    <input
                        type="color"
                        className="creation__color-custom"
                        onChange={e => { onPick(e.target.value); setOpen(false); }}
                    />
                </div>
            )}
        </div>
    );
}

interface ToolbarButtonProps {
    label: string;
    onClick: () => void;
    children: React.ReactNode;
    wide?: boolean;
    disabled?: boolean;
}

function ToolbarButton({ label, onClick, children, wide, disabled }: ToolbarButtonProps) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button
                    type="button"
                    className={`creation__toolbar-btn${wide ? ' creation__toolbar-btn--wide' : ''}`}
                    onClick={onClick}
                    disabled={disabled}
                    aria-label={label}
                >
                    {children}
                </button>
            </TooltipTrigger>
            <TooltipContent>{label}</TooltipContent>
        </Tooltip>
    );
}

interface Company {
    id: string;
    name: string;
    ticker: string;
    sector?: string;
}

interface CreationProps {
    projectId: string;
    projectName: string;
    companies?: Company[];
}

type TabType = 'secondary' | 'quant';

const Creation: React.FC<CreationProps> = ({ projectId, projectName, companies = [] }) => {
    const [activeTab, setActiveTab] = useState<TabType>('secondary');
    const reportEditorRef = useRef<HTMLDivElement>(null);
    const { userId: USER_ID } = useAuth();

    // ── Zustand stores ───────────────────────────────────────────────────────
    // Clipboard store — items synced with backend, reactive across all tabs
    const clipItems        = useClipboardStore(s => s.items);
    const clipIsLoading    = useClipboardStore(s => s.isLoading);
    const clipInit         = useClipboardStore(s => s.init);
    const clipRemoveItem   = useClipboardStore(s => s.removeItem);
    const clipClearAll     = useClipboardStore(s => s.clearAll);


    // Report editor store — persists across tab switches
    const editingReportId    = useReportEditorStore(s => s.editingReportId);
    const editingReportLabel = useReportEditorStore(s => s.editingReportLabel);
    const storedEditorHtml   = useReportEditorStore(s => s.editorHtml);
    const setStoredEditorHtml = useReportEditorStore(s => s.setEditorHtml);
    const savedReportId      = useReportEditorStore(s => s.savedReportId);
    const setSavedReportId   = useReportEditorStore(s => s.setSavedReportId);
    const draftReports       = useReportEditorStore(s => s.draftReports);
    const isDraftReportsLoading = useReportEditorStore(s => s.isDraftReportsLoading);
    const fetchDraftReports  = useReportEditorStore(s => s.fetchDraftReports);
    const storeStartEditing  = useReportEditorStore(s => s.startEditing);
    const storeStopEditing   = useReportEditorStore(s => s.stopEditing);
    const removeDraftFromList = useReportEditorStore(s => s.removeDraftFromList);

    // Alias draftItems for the clipboard panel (kept for code that references it)
    const draftItems = clipItems;
    const isDraftLoading = clipIsLoading;

    // Bumped on every editor transaction/selection change so toolbar disabled/active
    // state stays in sync (see onTransaction/onSelectionUpdate below).
    const [, forceToolbarRerender] = useState(0);

    // Rich-text editor — content restored from the store on mount so it survives tab switches
    const editor = useEditor({
        extensions: [
            StarterKit,
            TextStyle,
            FontSize,
            Color,
            Highlight.configure({ multicolor: true }),
            Underline,
            TextAlign.configure({ types: ['heading', 'paragraph'] }),
            Table.configure({ resizable: true }),
            TableRow,
            TableHeader,
            TableCell,
            Link.configure({ openOnClick: false, autolink: true }),
            ResizableImage,
            Placeholder.configure({ placeholder: 'Start drafting the final executive report here...' }),
        ],
        content: storedEditorHtml || '',
        onUpdate: ({ editor: e }) => {
            setStoredEditorHtml(e.getHTML());
        },
        // Re-render on every transaction so toolbar disabled/active state (undo/redo,
        // "inside a table") stays in sync even when a change doesn't touch content
        // (e.g. moving the cursor, which onUpdate alone wouldn't catch).
        onTransaction: () => forceToolbarRerender(n => n + 1),
        onSelectionUpdate: () => forceToolbarRerender(n => n + 1),
    }, []);

    // ── Company selection (simplified — no modal needed) ─────────────────────
    // If portfolio has multiple companies, analyst picks one inline
    const [selectedCompanyIdx, setSelectedCompanyIdx] = useState(0);
    const selectedCompany = companies[selectedCompanyIdx] ?? null;
    const [isSaving, setIsSaving] = useState(false);
    const [isDownloading, setIsDownloading] = useState(false);

    // ── Draft reports helpers ─────────────────────────────────────────────────
    const loadDraftIntoEditor = (report: ReportResponse) => {
        storeStartEditing(report);

        if (editor) {
            let html = report.content_html
                ? report.content_html
                : report.content_markdown
                    ? marked.parse(report.content_markdown) as string
                    : '';
            // Legacy reports (Markdown-only, no content_html) had images stripped
            // from the body on save — append them back from image_urls.
            if (!report.content_html) {
                for (const url of report.image_urls) {
                    html += `<img src="${url}" />`;
                }
            }
            html = sanitizeHtml(html)
                // Whitespace/newlines between tags (marked output, pretty-printed HTML)
                // would otherwise be kept as blank lines by the pre-wrap editor.
                .replace(/>\s+</g, '><')
                .trim();
            editor.commands.setContent(html, { parseOptions: { preserveWhitespace: false } });
            setStoredEditorHtml(html);
        }

        setViewMode('clipboard');
        showToast(`Draft for ${report.company_name} loaded — continue editing`);
    };

    const startNewReport = () => {
        storeStopEditing();
        editor?.commands.clearContent();
        showToast('Editor cleared — starting new report');
    };

    // View mode: clipboard | summaries | drafts
    type ViewMode = 'clipboard' | 'summaries' | 'drafts';
    const [viewMode, setViewMode] = useState<ViewMode>('clipboard');

    // Inner sidebar collapse + resize state
    const {
        collapsed: sidebarCollapsed,
        setCollapsed: setSidebarCollapsed,
        width: sidebarWidth,
        resizeHandleRef,
        handleResizeMouseDown,
    } = useResizableSidebar({ defaultWidth: 265, min: 200, max: 420 });

    // Summaries state
    const [summaries, setSummaries] = useState<{ rag: SummaryItem[]; quant: SummaryItem[] }>({ rag: [], quant: [] });
    const [isLoadingSummaries, setIsLoadingSummaries] = useState(false);
    const [expandedSummaryId, setExpandedSummaryId] = useState<string | null>(null);

    // Generate dialog state
    const [showGenerateDialog, setShowGenerateDialog] = useState(false);
    const [groupedSessions, setGroupedSessions] = useState<{ rag: ChatSessionResponse[]; compare: ChatSessionResponse[]; quant: ChatSessionResponse[] }>({ rag: [], compare: [], quant: [] });
    const [selectedSessionIds, setSelectedSessionIds] = useState<Set<string>>(new Set());
    const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['rag', 'compare', 'quant']));
    const [isLoadingChats, setIsLoadingChats] = useState(false);
    const [isGenerating, setIsGenerating] = useState(false);

    const fetchSummaries = useCallback(async () => {
        setIsLoadingSummaries(true);
        try {
            const data = await chatApi.getUserSummaries(USER_ID);
            setSummaries(data);
        } catch (err) {
            console.error('Failed to fetch summaries:', err);
        } finally {
            setIsLoadingSummaries(false);
        }
    }, [USER_ID]);

    // ── On mount: prefetch all sidebar counts so badges show immediately ─────
    useEffect(() => {
        if (!USER_ID) return;
        clipInit(USER_ID, projectId ? parseInt(projectId) : undefined);
        fetchSummaries();
        fetchDraftReports(USER_ID, projectId);
    }, [USER_ID, projectId, clipInit, fetchSummaries, fetchDraftReports]);

    const handleViewModeChange = (mode: ViewMode) => {
        setViewMode(mode);
        if (mode === 'summaries') fetchSummaries();
        if (mode === 'drafts') fetchDraftReports(USER_ID, projectId);
    };

    const clearAllSummaries = useCallback(async () => {
        if (!window.confirm('Clear all summaries?')) return;
        try {
            await chatApi.clearAllUserSummaries(USER_ID);
            setSummaries({ rag: [], quant: [] });
        } catch (err) {
            console.error('Failed to clear summaries:', err);
        }
    }, [USER_ID]);

    const openGenerateDialog = useCallback(async () => {
        setShowGenerateDialog(true);
        setIsLoadingChats(true);
        setSelectedSessionIds(new Set());
        try {
            const sessions = await chatApi.getUserSessions(USER_ID, undefined, parseInt(projectId));
            const grouped = { rag: [] as ChatSessionResponse[], compare: [] as ChatSessionResponse[], quant: [] as ChatSessionResponse[] };
            for (const s of sessions) {
                if (s.agent_type === 'quant') {
                    grouped.quant.push(s);
                } else if (s.session_metadata?.type === 'compare') {
                    grouped.compare.push(s);
                } else {
                    grouped.rag.push(s);
                }
            }
            setGroupedSessions(grouped);
        } catch (err) {
            console.error('Failed to fetch chat sessions:', err);
        } finally {
            setIsLoadingChats(false);
        }
    }, [projectId, USER_ID]);

    const toggleSessionSelection = (sessionId: string) => {
        setSelectedSessionIds(prev => {
            const next = new Set(prev);
            if (next.has(sessionId)) next.delete(sessionId);
            else next.add(sessionId);
            return next;
        });
    };

    const toggleSection = (section: string) => {
        setExpandedSections(prev => {
            const next = new Set(prev);
            if (next.has(section)) next.delete(section);
            else next.add(section);
            return next;
        });
    };

    const handleProceedGenerate = async () => {
        if (selectedSessionIds.size === 0) return;
        setIsGenerating(true);
        try {
            const result = await chatApi.generateConsolidatedSummary(Array.from(selectedSessionIds));
            if (result.consolidated_summary) {
                showToast('✓ Summary generated successfully');
            }
            setShowGenerateDialog(false);
            // Also refresh summaries list
            fetchSummaries();
        } catch (err) {
            console.error('Failed to generate consolidated summary:', err);
            showToast('✗ Failed to generate summary');
        } finally {
            setIsGenerating(false);
        }
    };

    // Core save logic — returns the report ID on success, null on failure
    const performSave = async (): Promise<number | null> => {
        const company = selectedCompany ?? { name: projectName, ticker: '', id: '' };
        const editorHtml = editor?.getHTML()?.trim() || '';

        let contentMarkdown = '';
        if (editorHtml) {
            const TurndownService = (await import('turndown')).default;
            const td = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced', bulletListMarker: '-' });
            td.addRule('stripImages', { filter: 'img', replacement: () => '' });
            td.keep(['table', 'thead', 'tbody', 'tr', 'th', 'td']);
            contentMarkdown = td.turndown(editorHtml);
        }

        const editorImgUrls = Array.from(editorHtml.matchAll(/<img[^>]+src="([^"]+)"/g)).map((m: RegExpMatchArray) => m[1]);
        const draftImgUrls = draftItems.filter(i => i.item_type === 'image' && i.image_url).map(i => i.image_url as string);
        const imageUrls = [...new Set([...editorImgUrls, ...draftImgUrls])];
        const sessionIds = [...new Set(draftItems.filter(i => i.session_id).map(i => i.session_id as string))];

        let savedId: number;
        if (editingReportId) {
            const report = await reportApi.updateReport(editingReportId, USER_ID, {
                company_name: company.name,
                ticker: company.ticker || undefined,
                content_markdown: contentMarkdown,
                content_html: editorHtml,
                image_urls: imageUrls,
                source_session_ids: sessionIds,
            });
            savedId = report.id;
            storeStopEditing();
            removeDraftFromList(savedId);
        } else {
            const report = await reportApi.createReport({
                user_id: USER_ID,
                company_name: company.name,
                ticker: company.ticker || undefined,
                content_markdown: contentMarkdown,
                content_html: editorHtml,
                image_urls: imageUrls,
                source_session_ids: sessionIds,
                portfolio_id: projectId ? parseInt(projectId) : undefined,
            });
            savedId = report.id;
        }

        editor?.commands.clearContent();
        setStoredEditorHtml('');
        setSavedReportId(savedId);
        return savedId;
    };

    const triggerPdfDownload = async (reportId: number) => {
        await reportApi.downloadPdf(reportId);
    };

    const handleSaveReport = async () => {
        const wasEditing = !!editingReportId;
        setIsSaving(true);
        try {
            await performSave();
            showToast(wasEditing ? '✓ Draft updated' : '✓ Report saved');
        } catch (err: unknown) {
            showToast(`✗ ${err instanceof Error ? err.message : 'Failed to save report'}`);
        } finally {
            setIsSaving(false);
        }
    };

    const handleDownloadPDF = async () => {
        const editorHtml = editor?.getHTML()?.trim() || '';
        const hasContent = !!editorHtml;

        // If the editor is empty but we already have a saved report, just download it
        if (!hasContent && savedReportId) {
            showToast('✓ Downloading PDF');
            triggerPdfDownload(savedReportId);
            return;
        }

        if (!hasContent && !savedReportId) {
            showToast('Write some content before downloading');
            return;
        }

        const wasEditing = !!editingReportId;
        setIsDownloading(true);
        try {
            const reportId = await performSave();
            if (reportId) {
                showToast(wasEditing ? '✓ Draft updated' : '✓ Report saved');
                triggerPdfDownload(reportId);
            }
        } catch (err: unknown) {
            showToast(`✗ ${err instanceof Error ? err.message : 'Failed to save report'}`);
        } finally {
            setIsDownloading(false);
        }
    };

    const copyToClipboard = async (text: string) => {
        try {
            const htmlContent = marked.parse(text) as string;
            const clipboardItem = new ClipboardItem({
                'text/plain': new Blob([text], { type: 'text/plain' }),
                'text/html': new Blob([htmlContent], { type: 'text/html' }),
            });
            await navigator.clipboard.write([clipboardItem]);
        } catch {
            // Fallback to plain text copy
            try {
                await navigator.clipboard.writeText(text);
            } catch (fallbackErr) {
                console.error('Failed to copy text:', fallbackErr);
            }
        }
    };

    // Toast notification state
    const [toastMessage, setToastMessage] = useState<string | null>(null);

    const showToast = (message: string) => {
        setToastMessage(message);
        setTimeout(() => setToastMessage(null), 3000);
    };

    // Helper to convert typical image links to correct markdown image syntax
    const processMarkdownContent = (text: string) => {
        if (!text) return text;
        const imageUrlRegex = /\.(jpeg|jpg|gif|png|webp|svg)(\?.*)?(\s+.*)?$/i;
        return text.replace(/(^|[^!])\[([^\]]+)\]\(([^)]+)\)/g, (match, prefix, alt, url) => {
            if (imageUrlRegex.test(url) || url.includes('cloudinary.com') || url.includes('/charts/') || url.includes('generated_charts')) {
                return `${prefix}![${alt}](${url})`;
            }
            return match;
        });
    };

    const insertLink = () => {
        const url = window.prompt('Link URL');
        if (url) editor?.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
    };

    const insertTable = () => {
        editor?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run();
    };

    const insertToReport = (text: string, html?: string, imageUrl?: string) => {
        let contentToInsert = '';

        if (imageUrl) {
            contentToInsert += `<img src="${imageUrl}" alt="Report Image" />`;
        }

        if (html) {
            contentToInsert += html;
        } else if (text && text !== 'Comparison Chart') {
            const processedText = processMarkdownContent(text);
            contentToInsert += marked.parse(processedText) as string;
        }

        if (editor && contentToInsert) {
            // Block elements already carry paragraph spacing; a <br/><br/> separator
            // only injected stray blank lines (saved into the report and its PDF).
            editor.chain().focus('end').insertContent(sanitizeHtml(contentToInsert)).run();
            showToast('✓ Item added to report successfully');
        }
    };


    const clipPreviewHtml = (item: DraftItem): string => {
        if (item.html) return sanitizeHtml(item.html);
        if (item.content) return sanitizeHtml(marked.parse(processMarkdownContent(item.content)) as string);
        return '';
    };

    const renderClipboardItems = () => {
        if (isDraftLoading) {
            return <div className="creation__empty-state"><div className="spinner" /><p className="creation__empty-title">Loading clipboard…</p></div>;
        }
        const filteredClips = draftItems.filter(item =>
            activeTab === 'secondary'
                ? item.source === 'rag' || item.source === 'summary'
                : item.source === 'quant'
        );
        if (filteredClips.length === 0) {
            return (
                <EmptyState
                    size="sm"
                    icon={
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                            <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                    }
                    title="No clips saved"
                    description="Highlight text or clip charts in the research tabs."
                />
            );
        }
        return (
            <div className="creation__clips">
                {filteredClips.map(item => (
                    <div key={item.id} className={`creation__clip-card ${item.source === 'quant' ? 'creation__clip-card--quant' : ''}`}>
                        <div className="creation__clip-header">
                            <span className="creation__clip-label">{item.label || item.item_type.toUpperCase()}</span>
                        </div>
                        {item.image_url && (
                            <div className="creation__clip-image">
                                <img src={item.image_url} alt={item.label || 'Chart'} />
                            </div>
                        )}
                        {(item.html || item.content) && (
                            <div
                                className="creation__clip-content creation__clip-content--html"
                                dangerouslySetInnerHTML={{ __html: clipPreviewHtml(item) }}
                            />
                        )}
                        <div className="creation__clip-actions">
                            <button
                                className="creation__clip-action creation__clip-action--add"
                                onClick={() => insertToReport(item.content || '', item.html || undefined, item.image_url || undefined)}
                            >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <circle cx="12" cy="12" r="10" /><path d="M12 8v8m-4-4h8" />
                                </svg>
                                Add to Report
                            </button>
                            <button
                                className="creation__clip-action creation__clip-action--delete"
                                onClick={() => clipRemoveItem(item.id)}
                                title="Remove"
                            >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <polyline points="3 6 5 6 21 6" />
                                    <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
                                </svg>
                            </button>
                        </div>
                    </div>
                ))}
            </div>
        );
    };

    const renderDraftReports = () => {
        if (isDraftReportsLoading) {
            return (
                <div className="creation__empty-state">
                    <div className="spinner" />
                    <p className="creation__empty-title">Loading drafts…</p>
                </div>
            );
        }
        if (draftReports.length === 0) {
            return (
                <EmptyState
                    size="sm"
                    icon={
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                            <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
                            <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
                        </svg>
                    }
                    title="No saved drafts"
                    description="Save a report from the editor to see it here."
                />
            );
        }
        return (
            <div className="creation__clips">
                {draftReports.map(report => (
                    <div key={report.id} className={`creation__draft-card ${editingReportId === report.id ? 'creation__draft-card--active' : ''}`}>
                        <div className="creation__draft-header">
                            <div className="creation__draft-avatar">
                                {(report.company_name?.[0] ?? 'R').toUpperCase()}
                            </div>
                            <div className="creation__draft-meta">
                                <span className="creation__draft-company">{report.company_name}</span>
                                {report.ticker && <span className="creation__draft-ticker">{report.ticker}</span>}
                            </div>
                            <span className={`creation__draft-status ${editingReportId === report.id ? 'creation__draft-status--editing' : ''}`}>
                                {editingReportId === report.id ? 'Editing' : 'Draft'}
                            </span>
                        </div>
                        <div className="creation__draft-date">
                            Updated {new Date(report.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                        </div>
                        <button
                            className="creation__draft-load-btn"
                            onClick={() => loadDraftIntoEditor(report)}
                            disabled={editingReportId === report.id}
                        >
                            {editingReportId === report.id ? (
                                'Currently Editing'
                            ) : (
                                <>
                                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                                    Continue Editing
                                </>
                            )}
                        </button>
                    </div>
                ))}
            </div>
        );
    };

    const renderSummaryItems = () => {
        if (isLoadingSummaries) {
            return (
                <div className="creation__empty-state">
                    <div className="spinner" />
                    <p className="creation__empty-title">Loading summaries...</p>
                </div>
            );
        }

        const currentSummaries = activeTab === 'secondary' ? summaries.rag : summaries.quant;

        if (currentSummaries.length === 0) {
            return (
                <EmptyState
                    size="sm"
                    icon={
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                            <polyline points="14 2 14 8 20 8" />
                            <line x1="16" y1="13" x2="8" y2="13" />
                            <line x1="16" y1="17" x2="8" y2="17" />
                        </svg>
                    }
                    title="No summaries yet"
                    description='Use "Summarize" in research tabs to generate summaries.'
                />
            );
        }

        const deleteSummary = async (sessionId: string) => {
            try {
                await chatApi.deleteSessionSummary(sessionId);
                setSummaries(prev => ({
                    rag: prev.rag.filter(s => s.session_id !== sessionId),
                    quant: prev.quant.filter(s => s.session_id !== sessionId),
                }));
            } catch (err) {
                console.error('Failed to delete summary:', err);
            }
        };

        return (
            <>
                <div className="creation__clips">
                    {currentSummaries.map((item) => (
                        <div key={item.session_id} className="creation__summary-card">
                            <div
                                className="creation__summary-header"
                                onClick={() => setExpandedSummaryId(
                                    expandedSummaryId === item.session_id ? null : item.session_id
                                )}
                            >
                                <div className="creation__summary-title-row">
                                    <span className="creation__summary-label">📌</span>
                                    <span className="creation__summary-title">{item.title || 'Untitled Session'}</span>
                                </div>
                                <div className="creation__summary-meta">
                                    <span>{item.message_count} msgs</span>
                                    {item.summary_updated_at && (
                                        <span>
                                            {new Date(item.summary_updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                        </span>
                                    )}
                                    <button
                                        className="creation__summary-delete-btn"
                                        onClick={(e) => { e.stopPropagation(); deleteSummary(item.session_id); }}
                                        title="Delete summary"
                                    >
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                                            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                                        </svg>
                                    </button>
                                </div>
                            </div>
                            {expandedSummaryId === item.session_id && (
                                <div className="creation__summary-content">
                                    <ReactMarkdown
                                        components={{
                                            img: ({node, ...props}) => (
                                                <div className="report-image-container">
                                                    <img {...props} className="report-image" />
                                                </div>
                                            )
                                        }}
                                    >
                                        {processMarkdownContent(item.summary)}
                                    </ReactMarkdown>
                                    <div className="creation__summary-actions">
                                        <button
                                            className="creation__clip-action"
                                            onClick={() => copyToClipboard(item.summary)}
                                            title="Copy summary"
                                        >
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                                <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                                            </svg>
                                            Copy
                                        </button>
                                        <button
                                            className="creation__clip-action"
                                            onClick={() => insertToReport(item.summary)}
                                            title="Add to report"
                                        >
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                <circle cx="12" cy="12" r="10" />
                                                <path d="M12 8v8m-4-4h8" />
                                            </svg>
                                            Add to Report
                                        </button>
                                    </div>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            </>
        );
    };

    return (
        <div className="creation">
            {/* Left Sidebar - Clipboard Panel */}
            <ResizableSidebar
                collapsed={sidebarCollapsed}
                width={sidebarWidth}
                onExpand={() => setSidebarCollapsed(false)}
                onCollapse={() => setSidebarCollapsed(true)}
                resizeHandleRef={resizeHandleRef}
                onResizeMouseDown={handleResizeMouseDown}
                className="creation__sidebar"
            >
                {/* Primary view nav: Clips | Summaries | My Drafts */}
                <div className="creation__viewnav">
                    <button
                        className={`creation__viewnav-btn ${viewMode === 'clipboard' ? 'creation__viewnav-btn--active' : ''}`}
                        onClick={() => handleViewModeChange('clipboard')}
                    >
                        Clipboard
                        {clipItems.length > 0 && (
                            <span className={`creation__viewnav-badge ${viewMode === 'clipboard' ? 'creation__viewnav-badge--active' : ''}`}>{clipItems.length}</span>
                        )}
                    </button>
                    <button
                        className={`creation__viewnav-btn ${viewMode === 'summaries' ? 'creation__viewnav-btn--active' : ''}`}
                        onClick={() => handleViewModeChange('summaries')}
                    >
                        Summaries
                        {(summaries.rag.length + summaries.quant.length) > 0 && (
                            <span className={`creation__viewnav-badge ${viewMode === 'summaries' ? 'creation__viewnav-badge--active' : ''}`}>{summaries.rag.length + summaries.quant.length}</span>
                        )}
                    </button>
                    <button
                        className={`creation__viewnav-btn ${viewMode === 'drafts' ? 'creation__viewnav-btn--active' : ''}`}
                        onClick={() => handleViewModeChange('drafts')}
                    >
                        My Drafts
                        {draftReports.length > 0 && (
                            <span className={`creation__viewnav-badge ${viewMode === 'drafts' ? 'creation__viewnav-badge--active' : ''}`}>{draftReports.length}</span>
                        )}
                    </button>
                </div>

                {/* Secondary / Quant sub-tabs — only for Clips and Summaries */}
                {viewMode !== 'drafts' && (() => {
                    const secCount = viewMode === 'summaries'
                        ? summaries.rag.length
                        : clipItems.filter(i => i.source === 'rag' || i.source === 'summary').length;
                    const quantCount = viewMode === 'summaries'
                        ? summaries.quant.length
                        : clipItems.filter(i => i.source === 'quant').length;
                    const hasItems = (secCount + quantCount) > 0;
                    const handleClear = viewMode === 'summaries'
                        ? clearAllSummaries
                        : () => { if (window.confirm('Clear all clipboard items?')) clipClearAll(); };
                    return (
                        <div className="creation__subtabs">
                            <button
                                className={`creation__subtab ${activeTab === 'secondary' ? 'creation__subtab--active' : ''}`}
                                onClick={() => setActiveTab('secondary')}
                            >
                                Secondary
                                {secCount > 0 && (
                                    <span className="creation__subtab-badge">{secCount}</span>
                                )}
                            </button>
                            <button
                                className={`creation__subtab ${activeTab === 'quant' ? 'creation__subtab--active' : ''}`}
                                onClick={() => setActiveTab('quant')}
                            >
                                Quant
                                {quantCount > 0 && (
                                    <span className="creation__subtab-badge">{quantCount}</span>
                                )}
                            </button>
                            {hasItems && (
                                <button className="creation__subtabs-clear" onClick={handleClear}>
                                    Clear all
                                </button>
                            )}
                        </div>
                    );
                })()}

                {viewMode === 'summaries' && (
                    <div className="creation__generate-bar">
                        <button className="creation__generate-bar-btn" onClick={openGenerateDialog}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                            </svg>
                            Generate Summary
                        </button>
                    </div>
                )}

                {/* Sidebar Content */}
                <div className="creation__sidebar-content">
                    {viewMode === 'clipboard' ? (
                        renderClipboardItems()
                    ) : viewMode === 'drafts' ? (
                        renderDraftReports()
                    ) : (
                        renderSummaryItems()
                    )}
                </div>
            </ResizableSidebar>

            {/* Main Editor Panel */}
            <main className="creation__main">
                {/* Editor Toolbar */}
                <div className="creation__toolbar">
                    <div className="creation__toolbar-left">
                        <ToolbarButton
                            label="Undo"
                            disabled={!editor?.can().undo()}
                            onClick={() => editor?.chain().focus().undo().run()}
                        >
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                            </svg>
                        </ToolbarButton>
                        <ToolbarButton
                            label="Redo"
                            disabled={!editor?.can().redo()}
                            onClick={() => editor?.chain().focus().redo().run()}
                        >
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.13-9.36L23 10" />
                            </svg>
                        </ToolbarButton>
                        <div className="creation__toolbar-divider" />
                        <ToolbarButton label="Bold" onClick={() => editor?.chain().focus().toggleBold().run()}>
                            <strong>B</strong>
                        </ToolbarButton>
                        <ToolbarButton label="Italic" onClick={() => editor?.chain().focus().toggleItalic().run()}>
                            <em>I</em>
                        </ToolbarButton>
                        <ToolbarButton label="Underline" onClick={() => editor?.chain().focus().toggleUnderline().run()}>
                            <span style={{ textDecoration: 'underline' }}>U</span>
                        </ToolbarButton>
                        <ToolbarButton label="Strikethrough" onClick={() => editor?.chain().focus().toggleStrike().run()}>
                            <span style={{ textDecoration: 'line-through' }}>S</span>
                        </ToolbarButton>
                        <select
                            className="creation__toolbar-select"
                            aria-label="Font Size"
                            value={editor?.getAttributes('textStyle').fontSize || ''}
                            onChange={(e) => {
                                const size = e.target.value;
                                if (size) editor?.chain().focus().setFontSize(size).run();
                                else editor?.chain().focus().unsetFontSize().run();
                            }}
                        >
                            <option value="">Default</option>
                            {FONT_SIZES.map(size => (
                                <option key={size} value={size}>{parseInt(size, 10)}</option>
                            ))}
                        </select>
                        <div className="creation__toolbar-divider" />
                        <ColorPopover
                            label="Font Color"
                            colors={docColors('--color-doc-ink-', 8)}
                            onPick={(c) => editor?.chain().focus().setColor(c).run()}
                            onClear={() => editor?.chain().focus().unsetColor().run()}
                            icon={<span style={{ fontWeight: 700 }}>A</span>}
                        />
                        <ColorPopover
                            label="Highlight"
                            colors={docColors('--color-doc-mark-', 6)}
                            onPick={(c) => editor?.chain().focus().toggleHighlight({ color: c }).run()}
                            onClear={() => editor?.chain().focus().unsetHighlight().run()}
                            icon={
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M9 11l6-6 4 4-6 6H9v-4z" />
                                    <path d="M3 21l4-1 10-10-3-3L4 17l-1 4z" />
                                </svg>
                            }
                        />
                        <div className="creation__toolbar-divider" />
                        <ToolbarButton label="Align Left" onClick={() => editor?.chain().focus().setTextAlign('left').run()}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="15" y2="12" /><line x1="3" y1="18" x2="18" y2="18" />
                            </svg>
                        </ToolbarButton>
                        <ToolbarButton label="Align Center" onClick={() => editor?.chain().focus().setTextAlign('center').run()}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <line x1="3" y1="6" x2="21" y2="6" /><line x1="6" y1="12" x2="18" y2="12" /><line x1="4" y1="18" x2="20" y2="18" />
                            </svg>
                        </ToolbarButton>
                        <ToolbarButton label="Align Right" onClick={() => editor?.chain().focus().setTextAlign('right').run()}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <line x1="3" y1="6" x2="21" y2="6" /><line x1="9" y1="12" x2="21" y2="12" /><line x1="6" y1="18" x2="21" y2="18" />
                            </svg>
                        </ToolbarButton>
                        <div className="creation__toolbar-divider" />
                        <ToolbarButton label="Bulleted List" onClick={() => editor?.chain().focus().toggleBulletList().run()}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <line x1="8" y1="6" x2="21" y2="6" />
                                <line x1="8" y1="12" x2="21" y2="12" />
                                <line x1="8" y1="18" x2="21" y2="18" />
                                <line x1="3" y1="6" x2="3.01" y2="6" />
                                <line x1="3" y1="12" x2="3.01" y2="12" />
                                <line x1="3" y1="18" x2="3.01" y2="18" />
                            </svg>
                        </ToolbarButton>
                        <ToolbarButton label="Numbered List" onClick={() => editor?.chain().focus().toggleOrderedList().run()}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <line x1="10" y1="6" x2="21" y2="6" />
                                <line x1="10" y1="12" x2="21" y2="12" />
                                <line x1="10" y1="18" x2="21" y2="18" />
                                <path d="M4 6h1v4" />
                                <path d="M4 10h2" />
                                <path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1" />
                            </svg>
                        </ToolbarButton>
                        <div className="creation__toolbar-divider" />
                        <ToolbarButton label="Insert Link" onClick={insertLink}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71" />
                                <path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" />
                            </svg>
                        </ToolbarButton>
                        <ToolbarButton label="Insert Table" onClick={insertTable}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                                <line x1="3" y1="9" x2="21" y2="9" />
                                <line x1="3" y1="15" x2="21" y2="15" />
                                <line x1="9" y1="3" x2="9" y2="21" />
                                <line x1="15" y1="3" x2="15" y2="21" />
                            </svg>
                        </ToolbarButton>
                        {editor?.isActive('table') && (
                            <>
                                <div className="creation__toolbar-divider" />
                                <ToolbarButton wide label="Add Row" onClick={() => editor?.chain().focus().addRowAfter().run()}>+Row</ToolbarButton>
                                <ToolbarButton wide label="Add Column" onClick={() => editor?.chain().focus().addColumnAfter().run()}>+Col</ToolbarButton>
                                <ToolbarButton wide label="Delete Row" onClick={() => editor?.chain().focus().deleteRow().run()}>-Row</ToolbarButton>
                                <ToolbarButton wide label="Delete Column" onClick={() => editor?.chain().focus().deleteColumn().run()}>-Col</ToolbarButton>
                                <ToolbarButton wide label="Delete Table" onClick={() => editor?.chain().focus().deleteTable().run()}>✕</ToolbarButton>
                            </>
                        )}
                    </div>
                    <div className="creation__toolbar-right">
                        {/* Inline company selector — only shown when portfolio has multiple companies */}
                        {companies.length > 1 && (
                            <select
                                className="creation__company-select"
                                value={selectedCompanyIdx}
                                onChange={e => setSelectedCompanyIdx(Number(e.target.value))}
                                title="Select company for this report"
                            >
                                {companies.map((c, i) => (
                                    <option key={c.id} value={i}>{c.ticker || c.name}</option>
                                ))}
                            </select>
                        )}
                        {editingReportId && (
                            <button className="creation__new-btn" onClick={startNewReport} title="Start a new report">
                                + New
                            </button>
                        )}
                        <button
                            className="creation__export-btn"
                            onClick={handleSaveReport}
                            disabled={isSaving || isDownloading}
                            title={editingReportId ? 'Update this draft' : `Save report for ${selectedCompany?.name || projectName}`}
                        >
                            {isSaving ? (
                                <div className="spinner spinner--small" />
                            ) : (
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
                                    <polyline points="17 21 17 13 7 13 7 21" />
                                    <polyline points="7 3 7 8 15 8" />
                                </svg>
                            )}
                            {isSaving ? 'Saving…' : editingReportId ? 'Update' : 'Save Report'}
                        </button>
                        <button
                            className="creation__download-btn"
                            onClick={handleDownloadPDF}
                            disabled={isSaving || isDownloading}
                            title={editingReportId ? 'Update draft and download PDF' : 'Save report and download PDF'}
                        >
                            {isDownloading ? (
                                <div className="spinner spinner--small" />
                            ) : (
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                    <polyline points="7 10 12 15 17 10"/>
                                    <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                            )}
                            {isDownloading ? 'Downloading…' : 'Download PDF'}
                        </button>
                    </div>
                </div>

                {/* Editing banner */}
                {editingReportId && (
                    <div className="creation__editing-banner">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
                            <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
                        </svg>
                        Editing: <strong>{editingReportLabel}</strong>
                        <button className="creation__editing-discard" onClick={startNewReport}>Start new instead</button>
                    </div>
                )}

                {/* Editor Content */}
                <div className="creation__editor-container">
                    <div className="creation__editor" ref={reportEditorRef}>
                        <h1 className="creation__editor-title">Executive Summary</h1>
                        <p className="creation__editor-subtitle">RESEARCH REPORT</p>

                        <EditorContent editor={editor} className="creation__editor-content creation__editor-content--html" />
                    </div>
                </div>
            </main>

            {/* Toast Notification */}
            {toastMessage && (
                <div className="creation__toast">
                    {toastMessage}
                </div>
            )}

            {/* Generate Consolidated Summary Dialog */}
            {showGenerateDialog && (
                <div className="creation__dialog-overlay" onClick={() => !isGenerating && setShowGenerateDialog(false)}>
                    <div className="creation__dialog" onClick={e => e.stopPropagation()}>
                        <div className="creation__dialog-header">
                            <h3 className="creation__dialog-title">Generate Consolidated Summary</h3>
                            <button
                                className="creation__dialog-close"
                                onClick={() => !isGenerating && setShowGenerateDialog(false)}
                                disabled={isGenerating}
                            >
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <line x1="18" y1="6" x2="6" y2="18" />
                                    <line x1="6" y1="6" x2="18" y2="18" />
                                </svg>
                            </button>
                        </div>
                        <div className="creation__dialog-body">
                            {isLoadingChats ? (
                                <div className="creation__dialog-loading">
                                    <div className="spinner" />
                                    <p>Loading chat sessions...</p>
                                </div>
                            ) : (
                                <div className="creation__dialog-sections">
                                    {([['rag', 'RAG', '📄'], ['compare', 'Stock Comparison', '📊'], ['quant', 'Quant', '📈']] as [string, string, string][]).map(([key, label, icon]) => {
                                        const sessions = groupedSessions[key as keyof typeof groupedSessions];
                                        const isExpanded = expandedSections.has(key);
                                        const selectedCount = sessions.filter(s => selectedSessionIds.has(s.session_id)).length;
                                        return (
                                            <div key={key} className="creation__dialog-section">
                                                <button
                                                    className={`creation__dialog-section-header ${isExpanded ? 'creation__dialog-section-header--open' : ''}`}
                                                    onClick={() => toggleSection(key)}
                                                >
                                                    <span className="creation__dialog-section-icon">{icon}</span>
                                                    <span className="creation__dialog-section-label">{label}</span>
                                                    <span className="creation__dialog-section-count">{sessions.length} chats</span>
                                                    {selectedCount > 0 && (
                                                        <span className="creation__dialog-section-selected">{selectedCount} selected</span>
                                                    )}
                                                    <svg
                                                        width="14" height="14" viewBox="0 0 24 24"
                                                        fill="none" stroke="currentColor" strokeWidth="2"
                                                        className={`creation__dialog-section-chevron ${isExpanded ? 'creation__dialog-section-chevron--open' : ''}`}
                                                    >
                                                        <polyline points="6 9 12 15 18 9" />
                                                    </svg>
                                                </button>
                                                {isExpanded && (
                                                    <div className="creation__dialog-section-body">
                                                        {sessions.length === 0 ? (
                                                            <p className="creation__dialog-no-chats">No chats available</p>
                                                        ) : (
                                                            sessions.map(session => (
                                                                <label key={session.session_id} className="creation__dialog-chat-item">
                                                                    <input
                                                                        type="checkbox"
                                                                        checked={selectedSessionIds.has(session.session_id)}
                                                                        onChange={() => toggleSessionSelection(session.session_id)}
                                                                        className="creation__dialog-checkbox"
                                                                    />
                                                                    <div className="creation__dialog-chat-info">
                                                                        <span className="creation__dialog-chat-title">{session.title || 'Untitled'}</span>
                                                                        <span className="creation__dialog-chat-meta">
                                                                            {session.message_count} msgs · {new Date(session.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                                                        </span>
                                                                    </div>
                                                                </label>
                                                            ))
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                        <div className="creation__dialog-footer">
                            <button
                                className="creation__dialog-cancel"
                                onClick={() => setShowGenerateDialog(false)}
                                disabled={isGenerating}
                            >
                                Cancel
                            </button>
                            <button
                                className="creation__dialog-proceed"
                                onClick={handleProceedGenerate}
                                disabled={selectedSessionIds.size === 0 || isGenerating}
                            >
                                {isGenerating ? (
                                    <>
                                        <div className="spinner spinner--small" />
                                        Generating...
                                    </>
                                ) : (
                                    `Proceed (${selectedSessionIds.size})`
                                )}
                            </button>
                        </div>
                    </div>
                </div>
            )}

        </div>
    );
};

export default Creation;
