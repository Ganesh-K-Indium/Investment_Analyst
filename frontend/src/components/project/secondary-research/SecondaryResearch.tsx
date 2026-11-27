import React, { useState, useRef, useEffect, useCallback, useMemo, lazy, Suspense, useEffectEvent } from 'react';
import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { marked } from 'marked';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { jsPDF } from 'jspdf';
import html2canvas from 'html2canvas';
import { Button, AutoResizingTextarea, ChatMessage, ChatMessageStatus, RenameModal, ResizableSidebar, EmptyState } from '../../ui';
import { useResizableSidebar } from '../../../hooks/useResizableSidebar';
import type { Company } from '../../../data/fortune500Companies';
import { allCompanies } from '../../../data/foreignCompanies';
import { secondaryAgentApi } from '../../../services/secondaryAgentApi';
import { getRunProgress, type AnalysisTask, type RunProgress } from '../../../services/analysisTasksApi';
import { useAnalysisRunsStore } from '../../../store/analysisRunsStore';
import { cssVar } from '../../../lib/cssVar';
import { RunProgressPanel } from '../run-progress/RunProgressPanel';
import { Form4ImportModal } from './Form4ImportModal';

import { chatApi } from '../../../services/chatApi';
import { portfolioApi } from '../../../services/portfolioApi';
import { buildCitations, inspectorUrl, quoteFromPage } from '../../../features/documents/citations';
import { documentsApi } from '../../../features/documents/documentsApi';
import { useClipboardActions } from '../../../hooks/useClipboardActions';
import { useChatCache } from '../../../context/useChatCache';
import { ScopeBar } from '../../../features/chatScope/ScopeBar';
import { DEFAULT_SCOPE, scopeToRequest, type ChatScope } from '../../../features/chatScope/scope';
import { useSettingsStore } from '../../../store/settingsStore';
import type { SecondaryAskRequest, Verification, UsageInfo } from '../../../services/secondaryAgentApi';
import { IoChatboxOutline } from "react-icons/io5";
import alphaLogoLight from '../../../assets/alpha.png';
import alphaLogoDark from '../../../assets/alpha-dark.png';
import './SecondaryResearch.css';
import { useAuth } from '../../../context/useAuth';
const loadCitationModal = () => import('../../../features/documents/CitationModal');
const CitationModal = lazy(loadCitationModal);
import { useTheme } from '../../../context/useTheme';

interface CitationSource {
    file: string;
    pages: number[];
    company?: string;  // Company name (e.g. "sanofi") — NOT the ticker
    ticker?: string;   // Ticker symbol (e.g. "SNY") from metadata.ticker
    filingType?: string;
    periodEnd?: string;
    year?: number;
    filePath?: string;  // Backend path for PDF retrieval
    docId?: string;     // Document Inspector id (metadata.content_hash)
    sourceType?: string; // 'analyst_note': the user's own note, not a filing (nothing to open in the Inspector)
    quotes?: Record<number, string>;  // best-guess cited sentence per page, for the PDF highlight
    answer?: string;    // answer text, used to re-derive the quote when history is reloaded
}

const parseFilingInfo = (file: string, citationObj?: CitationSource) => {
    if (citationObj?.sourceType === 'analyst_note') {
        return {
            ticker: citationObj.ticker || '',
            filingType: 'Analyst Note',
            filingName: 'Analyst note (analyst\'s own view, not a filing)',
            dateStr: citationObj.year ? String(citationObj.year) : '',
        };
    }
    let ticker = citationObj?.ticker || '';
    let filingType = citationObj?.filingType || '';
    let dateStr = citationObj?.periodEnd || (citationObj?.year ? String(citationObj.year) : '');

    const secPattern = /^([A-Z0-9]+)_([1-9][0-9]*-[KQ]|20-F|8-K|FORM4|Form4|DEF14A)_?(\d{4}-\d{2}-\d{2}|\d{4})?/i;
    const match = file.match(secPattern);

    if (match) {
        if (!ticker) ticker = match[1].toUpperCase();
        if (!filingType) filingType = match[2].toUpperCase();
        if (!dateStr && match[3]) dateStr = match[3];
    } else {
        if (!filingType) {
            if (/10-?k/i.test(file)) filingType = '10-K';
            else if (/10-?q/i.test(file)) filingType = '10-Q';
            else if (/8-?k/i.test(file)) filingType = '8-K';
            else if (/20-?f/i.test(file)) filingType = '20-F';
            else if (/6-?k/i.test(file)) filingType = '6-K';
            else if (/form\s*4/i.test(file)) filingType = 'Form 4';
            else filingType = 'SEC';
        }
        if (!ticker) {
            const parts = file.split('_');
            if (parts.length > 0 && parts[0].length <= 6 && /^[A-Z0-9]+$/i.test(parts[0])) {
                ticker = parts[0].toUpperCase();
            }
        }
    }

    let filingName = 'SEC Disclosure';
    const typeUpper = filingType.toUpperCase();
    if (typeUpper === '10-K') filingName = 'Annual Report (10-K)';
    else if (typeUpper === '10-Q') filingName = 'Quarterly Report (10-Q)';
    else if (typeUpper === '8-K') filingName = 'Current Report (8-K)';
    else if (typeUpper === '20-F') filingName = 'Annual Report (20-F)';
    else if (typeUpper === '6-K') filingName = 'Interim/Current Report (6-K)';
    else if (typeUpper === 'FORM 4' || typeUpper === 'FORM4') filingName = 'Insider Activity (Form 4)';

    return {
        ticker: ticker || 'SEC',
        filingType: filingType || 'FILING',
        filingName,
        dateStr: dateStr || ''
    };
};

interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    citations?: CitationSource[];  // Source files with page numbers
    webSearched?: boolean;
    webLinks?: string[];  // Extracted URLs from the answer
    chartUrl?: string | null;  // URL to comparison chart if available
    isAlpha?: boolean;  // Marks an ALPHA Framework report — only these get value color-coding
    resultLabel?: string;  // Compact "what was run" label shown on Compare/ALPHA results — no user turn is synthesized for these
    verification?: Verification;  // figures in the answer checked against the retrieved documents
    usage?: UsageInfo;            // tokens / estimated cost of this answer
    clarification?: boolean;      // the pipeline paused to ask which company/period; the next message answers it
}

// Helper function to extract URLs from text
const STEP_LABEL: Record<string, string> = {
    route_query: 'Understanding the question', clarify: 'Checking the scope', preprocess: 'Planning the searches', retrieve: 'Searching the documents',
    grade_documents: 'Checking the evidence', rewrite_query: 'Searching again with a better query', web_search: 'Searching the web', integrate_web_search: 'Adding web results',
    generate: 'Writing the answer', verify_answer: 'Checking the figures against the sources', generate_chart: 'Drawing the chart', custom_retrieve: 'Gathering evidence for each section',
    custom_generate: 'Writing the report', alpha_retrieve: 'Gathering evidence', alpha_generate: 'Writing the ALPHA report', scenario_retrieve: 'Gathering evidence',
    scenario_generate: 'Writing the scenarios', macro_analyze: 'Planning the macro lookup', macro_calculate: 'Calculating', macro_format: 'Writing the answer',
};

const extractUrlsFromText = (text: string): string[] => {
    // First, remove markdown image syntax so we don't extract image URLs as citations
    const textWithoutImages = text.replace(/!\[([^\]]*)\]\(https?:\/\/[^)]+\)/gi, '');
    
    // Match URLs including those in markdown format [text](url)
    const urlRegex = /https?:\/\/[^\s)\]]+/gi;
    const matches = textWithoutImages.match(urlRegex) || [];
    return [...new Set(matches)]; // Return unique URLs
};

// Helper function to remove URLs from text for display
const stripUrlsFromText = (text: string): string => {
    // Remove markdown images ![alt](url) entirely (no visible text replacement)
    let cleaned = text.replace(/!\[([^\]]*)\]\(https?:\/\/[^)]+\)/gi, '');
    // Remove markdown links [text](url) and replace with just the text
    cleaned = cleaned.replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/gi, '$1');
    // Remove standalone URLs (but preserve newlines)
    cleaned = cleaned.replace(/https?:\/\/[^\s\n]+/gi, '');
    return cleaned;
};

// ----------------------------------------------------------------------------
// Value color-coding — ALPHA reports only. The backend LLM tags sentences
// that state a concrete financial fact (a trade, share count, dollar
// figure, price/percentage move) inline as it writes, using
// [[+|sentence]] / [[-|sentence]] / [[~|sentence]] markers — see
// ALPHA_SENTIMENT_TAG_INSTRUCTION in the rag repo's prompts.py. This just
// finds those markers and renders the wrapped sentence as one colored
// span, stripping the marker syntax. No keyword/number guessing on the
// frontend — untagged text (most of the report) renders as plain text.
// ----------------------------------------------------------------------------
const SENTIMENT_TAG_REGEX = /\[\[([+\-~])\|([^\]]+)\]\]/g;
const SENTIMENT_TAG_CLASS: Record<string, string> = {
    '+': 'value-positive',
    '-': 'value-negative',
    '~': 'value-neutral'
};

const colorizeValueText = (text: string, keyPrefix: string): React.ReactNode => {
    const regex = new RegExp(SENTIMENT_TAG_REGEX.source, 'g');
    const nodes: React.ReactNode[] = [];
    let cursor = 0;
    let match: RegExpExecArray | null;
    let i = 0;
    while ((match = regex.exec(text)) !== null) {
        if (match.index > cursor) {
            nodes.push(text.slice(cursor, match.index));
        }
        nodes.push(
            <span key={`${keyPrefix}-${i++}`} className={SENTIMENT_TAG_CLASS[match[1]]}>{match[2]}</span>
        );
        cursor = match.index + match[0].length;
    }
    if (nodes.length === 0) return text;
    if (cursor < text.length) {
        nodes.push(text.slice(cursor));
    }
    return nodes;
};

// Recursively walks markdown-rendered children, color-coding any plain text
// nodes while leaving already-rendered elements (links, code, etc.) untouched.
const colorizeValueChildren = (children: React.ReactNode, keyPrefix = 'val'): React.ReactNode => {
    return React.Children.map(children, (child, idx) =>
        typeof child === 'string' ? colorizeValueText(child, `${keyPrefix}-${idx}`) : child
    );
};

// Colours baked into clipped text: the fixed document tokens (--color-doc-*, index.css), so a clip reads the same in any
// theme once it leaves this view.
const SENTIMENT_TAG_TOKEN: Record<string, string> = {
    '+': '--color-doc-positive',
    '-': '--color-doc-negative',
    '~': '--color-doc-neutral',
};
const sentimentColor = (sign: string) => cssVar(SENTIMENT_TAG_TOKEN[sign]);

// Bakes [[+|...]]/[[-|...]]/[[~|...]] markers into real colored <span>s directly in the
// Markdown source, so the color survives clipping instead of disappearing (it's otherwise
// only ever applied live, in colorizeValueChildren, at render time) or leaking as literal
// bracket syntax into the report editor.
const sentimentMarkersToHtml = (markdown: string): string => {
    const regex = new RegExp(SENTIMENT_TAG_REGEX.source, 'g');
    return markdown.replace(regex, (_match, sign: string, sentence: string) =>
        `<span style="color: ${sentimentColor(sign)}; font-weight: 500">${sentence}</span>`
    );
};

// Preprocess LaTeX math notation to remark-math compatible format (display math only)
// Single $ is disabled to avoid breaking currency values like $307.4 billion
const preprocessMathContent = (text: string): string => {
    // Convert \[ ... \] (standard LaTeX display math) to $$ ... $$
    let result = text.replace(/\\\[([\s\S]*?)\\\]/g, (_m, math) => `$$${math}$$`);
    // Convert \( ... \) (LaTeX inline math) to $$ ... $$ (display, since single $ is disabled)
    result = result.replace(/\\\(([\s\S]*?)\\\)/g, (_m, math) => `$$${math}$$`);
    // Convert [ formula ] where content starts with a LaTeX backslash command
    // Negative lookahead (?!\() ensures we don't match markdown links [text](url)
    result = result.replace(/(?<!\])\[\s*(\\[^[\]]+?)\s*\](?!\()/g, (_m, math) => `$$${math}$$`);
    // Post-process all $$ blocks: fix comma spacing in numbers and upgrade \frac to \dfrac
    // so fraction numerators/denominators render at full size instead of reduced textstyle
    result = result.replace(/\$\$([\s\S]*?)\$\$/g, (_m, formula) => {
        let f = formula.replace(/(\d),(\d)/g, '$1{,}$2');
        f = f.replace(/\\frac\b/g, '\\dfrac');
        return `$$${f}$$`;
    });
    return result;
};

interface ChatSession {
    id: string;
    title: string;
    files?: string[];
    messages: ChatMessage[];
    createdAt: Date;
    threadId?: string; // API thread_id for conversation continuity
    isNew?: boolean; // Flag to skip history fetch for brand new sessions
    compareCompanies?: string[]; // Companies involved in compare sessions (from session_metadata)
    alphaTicker?: string; // Ticker involved in an ALPHA session (from session_metadata)
    savedResultLabel?: string; // Pre-formatted "what was run" label for Compare/ALPHA, reconstructed from session_metadata — kept in the same nice format the live run uses, instead of the raw backend-persisted query text
}

// Same label format used live in getComparison/runAlpha — kept in one place so
// history reconstruction (fetchSessions/fetchMessages) never has to fall back
// to the raw backend-persisted query text for the result label.
const formatCompareResultLabel = (companies: string[], year?: number): string => {
    const stockList = companies.length >= 3
        ? `${companies[0]}, ${companies[1]}, and ${companies[2]}`
        : companies.join(' and ');
    return year ? `${stockList} (${year})` : stockList;
};

const formatAlphaResultLabel = (ticker: string): string => `360° ALPHA Analysis — ${ticker}`;

// getFriendlyName / getIntegrationIcon moved to ImportFileModal.tsx (only used there)

interface SecondaryResearchProps {
    projectId: string;
    projectName: string;
    companies: Company[];
    /** When set (e.g. from the Overview dashboard's "View" action), focuses
     * the chat session whose threadId matches, once it's available. */
    focusSessionId?: string;
    /** Called once focusSessionId has been applied, so the parent can clear it. */
    onFocusApplied?: () => void;
    /** Called immediately after enqueueing an analysis, so the parent can
     * refetch portfolio-list run counts and show the loader right away. */
    onAnalysisEnqueued?: () => void | Promise<void>;
}

export const SecondaryResearch: React.FC<SecondaryResearchProps> = ({ projectId, projectName, companies, focusSessionId, onFocusApplied, onAnalysisEnqueued }) => {
    const { getSecondaryChatState, setSecondaryChatState } = useChatCache();
    const { userId: USER_ID, user } = useAuth();
    const { theme } = useTheme();
    const alphaLogo = theme === 'dark' ? alphaLogoDark : alphaLogoLight;
    const userDisplayName = user?.full_name || user?.username || 'You';

    // Initialize state from cache
    const cachedState = getSecondaryChatState(projectId);
    const { addToSecondaryClipboard } = useClipboardActions();
    const [selectedFiles] = useState<string[]>([]);

    const [chatSessions, setChatSessions] = useState<ChatSession[]>(cachedState.sessions);
    const [activeSession, setActiveSession] = useState<ChatSession | null>(
        cachedState.activeSessionId
            ? cachedState.sessions.find(s => s.id === cachedState.activeSessionId) || null
            : null
    );
    const [messageInput, setMessageInput] = useState('');
    const [_error, setError] = useState<string | null>(null);
    const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
    const [editingTitle, setEditingTitle] = useState('');
    const [hoveredSessionId, setHoveredSessionId] = useState<string | null>(null);
    const [selectedText, setSelectedText] = useState<string>('');
    const [selectedHtml, setSelectedHtml] = useState<string>('');
    const [selectionPosition, setSelectionPosition] = useState<{ x: number; y: number } | null>(null);

    // Tracks which sessions currently have a request in flight (chat send,
    // compare, or ALPHA), so the user can navigate to other sessions without
    // cancelling or losing it — results land back on the correct session
    // whenever the request resolves, and the loader only shows on the
    // session that's actually waiting, not on every session you switch to.
    const [busySessionIds, setBusySessionIds] = useState<Set<string>>(new Set());

    // ALPHA state
    const [isAlphaMode, setIsAlphaMode] = useState(false);
    const [alphaTicker, setAlphaTicker] = useState('');
    const [alphaDropdownOpen, setAlphaDropdownOpen] = useState(false);
    const [alphaSearch, setAlphaSearch] = useState('');

    // Stock Compare state
    const [isCompareMode, setIsCompareMode] = useState(false);
    const [compareStock1, setCompareStock1] = useState('');
    const [compareStock2, setCompareStock2] = useState('');
    const [compareStock3, setCompareStock3] = useState('');
    const [compareRunLabel, setCompareRunLabel] = useState<string | null>(null);
    // Live step-by-step progress of the ALPHA / Compare run in flight, tagged with its session so it only shows there.
    const [runProgress, setRunProgress] = useState<{ sessionId: string; kind: 'alpha' | 'compare'; progress: RunProgress | null; receivedAt: number; startedAt: number; chips?: string[] } | null>(null);
    const [dropdown1Open, setDropdown1Open] = useState(false);
    const [dropdown2Open, setDropdown2Open] = useState(false);
    const [dropdown3Open, setDropdown3Open] = useState(false);
    const [search1, setSearch1] = useState('');
    const [search2, setSearch2] = useState('');
    const [search3, setSearch3] = useState('');
    const [compareYear, setCompareYear] = useState<number>(2024);

    const [toastMessage, setToastMessage] = useState<string | null>(null);
    const [expandedCitations, setExpandedCitations] = useState<Set<string>>(new Set());

    const toggleCitationExpand = (citationKey: string) => {
        setExpandedCitations(prev => {
            const next = new Set(prev);
            if (next.has(citationKey)) {
                next.delete(citationKey);
            } else {
                next.add(citationKey);
            }
            return next;
        });
    };

    // Citation page pills open the Document Inspector at the cited page (with the cited text highlighted).
    const [citationPreview, setCitationPreview] = useState<{ docId: string; page: number; quote?: string } | null>(null);
    const citationHref = (c: CitationSource, pg: number) => {
        const opts = { quote: c.quotes?.[pg], portfolioId: projectId, sessionId: activeSession?.id };
        return c.docId
            ? inspectorUrl(c.docId, pg, opts)
            : `/documents?q=${encodeURIComponent((c.filePath || c.file).split('/').pop() || '')}`;
    };
    const warmCitation = (c: CitationSource, pg: number) => { // hover/focus: get the modal code, doc summary and highlight ready
        loadCitationModal();
        if (!c.docId) return;
        documentsApi.get(c.docId).catch(() => undefined);
        const q = c.quotes?.[pg];
        if (q) documentsApi.locate(c.docId, pg, q).catch(() => undefined);
    };
    const openCitation = async (c: CitationSource, pg: number) => {
        let docId = c.docId;
        if (!docId) { // old sessions: resolve by file name
            try {
                const r = await documentsApi.list({ q: (c.filePath || c.file).split('/').pop(), limit: 1 });
                docId = r.items[0]?.doc_id;
            } catch { /* handled below */ }
        }
        if (!docId) { showToast('❌ Document not found in the library'); return; }
        let quote = c.quotes?.[pg];
        if (!quote && c.answer) { // history reloaded from the server keeps no page text: derive the quote from the page itself
            try { quote = quoteFromPage(await documentsApi.page(docId, pg), c.answer); } catch { /* preview still opens, unhighlighted */ }
        }
        setCitationPreview({ docId, page: pg, quote });
    };

    const showToast = (message: string) => {
        setToastMessage(message);
        setTimeout(() => setToastMessage(null), 3000);
    };

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const messagesContainerRef = useRef<HTMLDivElement>(null);
    const dropdown1Ref = useRef<HTMLDivElement>(null);
    const dropdown2Ref = useRef<HTMLDivElement>(null);
    const dropdown3Ref = useRef<HTMLDivElement>(null);
    const alphaDropdownRef = useRef<HTMLDivElement>(null);
    const [isLoadingSessions, setIsLoadingSessions] = useState(false);
    const [isLoadingMessages, setIsLoadingMessages] = useState(false);
    const [intermediateMessage, setIntermediateMessage] = useState<string | null>(null);
    const [scope, setScope] = useState<ChatScope>(DEFAULT_SCOPE);
    const [streamDraft, setStreamDraft] = useState('');
    const settings = useSettingsStore(s => s.effective);
    const showCost = settings.ui?.show_cost === true;
    const loadSettings = useSettingsStore(s => s.load);
    const settingsLoaded = useSettingsStore(s => s.loaded);
    useEffect(() => { if (!settingsLoaded) void loadSettings(); }, [settingsLoaded, loadSettings]);

    // Form4 Import Dialog state
    const [isForm4ModalOpen, setIsForm4ModalOpen] = useState(false);

    // Inner sidebar collapse + resize state
    const {
        collapsed: sidebarCollapsed,
        setCollapsed: setSidebarCollapsed,
        width: sidebarWidth,
        resizeHandleRef,
        handleResizeMouseDown,
    } = useResizableSidebar({ defaultWidth: 220, min: 160, max: 380 });


    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    const handleTextSelection = () => {
        const selection = window.getSelection();
        const text = selection?.toString().trim();
        if (text && text.length > 0 && selection && selection.rangeCount > 0) {
            const range = selection.getRangeAt(0);
            const rect = range.getBoundingClientRect();

            // Capture HTML content from selection
            const container = document.createElement('div');
            container.appendChild(range.cloneContents());
            // colorizeValueChildren renders sentiment coloring as a CSS class
            // (.value-positive/negative/neutral, defined in SecondaryResearch.css)
            // rather than an inline style — meaningless once this HTML leaves this
            // page (e.g. into the Creation editor, which never loads that
            // stylesheet). Bake the actual color in as an inline style before
            // serializing, same as sentimentMarkersToHtml does for full-message clips.
            container.querySelectorAll('.value-positive, .value-negative, .value-neutral').forEach(el => {
                const sign = el.classList.contains('value-positive') ? '+'
                    : el.classList.contains('value-negative') ? '-' : '~';
                (el as HTMLElement).style.color = sentimentColor(sign);
                (el as HTMLElement).style.fontWeight = '500';
            });
            const html = container.innerHTML;

            setSelectedText(text);
            setSelectedHtml(html);
            setSelectionPosition({ x: rect.left + rect.width / 2, y: rect.top - 10 });
        } else {
            setSelectedText('');
            setSelectedHtml('');
            setSelectionPosition(null);
        }
    };

    const addSelectedToClipboard = () => {
        if (selectedText && activeSession) {
            addToSecondaryClipboard(USER_ID, projectId, {
                text: selectedText,
                html: selectedHtml || undefined,
                sessionTitle: activeSession.title
            }, activeSession.threadId);
            setSelectedText('');
            setSelectedHtml('');
            setSelectionPosition(null);
            window.getSelection()?.removeAllRanges();
            showToast('✓ Item added to clipboard successfully');
        }
    };

    const addImageToClipboard = (imageUrl: string) => {
        if (!activeSession) return;
        addToSecondaryClipboard(USER_ID, projectId, {
            text: '',
            imageUrl,
            sessionTitle: activeSession.title,
        }, activeSession.threadId);
        showToast('✓ Image added to clipboard successfully');
    };

    const addMessageToClipboard = (msg: ChatMessage) => {
        if (!activeSession) return;

        let fullText = msg.content;
        if (msg.citations && msg.citations.length > 0) {
            const citationLines = msg.citations.map((c) => {
                const parsed = parseFilingInfo(c.file, c);
                const pagesStr = c.pages && c.pages.length > 0 ? ` (Pages: ${c.pages.join(', ')})` : '';
                const dateStr = parsed.dateStr ? ` • Date: ${parsed.dateStr}` : '';
                return `• ${parsed.ticker} ${parsed.filingType} (${parsed.filingName})${dateStr}${pagesStr} [Source: ${c.file}]`;
            });
            fullText += `\n\n---\n**Citations:**\n` + citationLines.join('\n');
        }

        const html = fullText.trim()
            ? (marked.parse(sentimentMarkersToHtml(fullText)) as string)
            : undefined;

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(fullText).catch((err) => {
                console.error('Failed to copy full message to system clipboard:', err);
            });
        }

        addToSecondaryClipboard(USER_ID, projectId, {
            text: fullText,
            html,
            imageUrl: msg.chartUrl || undefined,
            sessionTitle: activeSession.title,
        }, activeSession.threadId);

        showToast(msg.content.trim() ? '✓ Message & citations added to clipboard!' : '✓ Image added to clipboard!');
    };

    const copyCitationText = (text: string, toastMsg: string = 'Citation copied to clipboard!') => {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).catch((err) => {
                console.error('Failed to copy to clipboard:', err);
            });
        }
        if (activeSession) {
            addToSecondaryClipboard(
                USER_ID,
                projectId,
                {
                    text: text,
                    sessionTitle: activeSession.title
                },
                activeSession.threadId
            );
        }
        showToast(toastMsg);
    };

    const isActiveSessionBusy = !!activeSession?.id && busySessionIds.has(activeSession.id);
    useEffect(() => {
        scrollToBottom();
    }, [activeSession?.messages, isActiveSessionBusy]);


    // Fetch chat sessions from backend on mount or projectId change
    const fetchSessions = useCallback(async () => {
        setIsLoadingSessions(true);
        try {
            const sessions = await chatApi.getUserSessions(USER_ID, 'rag', parseInt(projectId));

            // Map backend sessions to frontend ChatSession objects
            // Use session_metadata.type to distinguish compare vs alpha vs ask (regular) chats
            const mappedSessions: ChatSession[] = sessions.map(s => {
                const isCompare = s.session_metadata?.type === 'compare';
                const isAlpha = s.session_metadata?.type === 'alpha';
                const compareCompanies = isCompare ? s.session_metadata?.companies : undefined;
                const alphaTicker = isAlpha ? s.session_metadata?.tickers?.[0]?.toUpperCase() : undefined;
                return {
                    id: isCompare ? `compare-${s.session_id}` : isAlpha ? `alpha-${s.session_id}` : s.session_id,
                    title: s.title,
                    messages: [], // Messages will be fetched individually
                    createdAt: new Date(s.created_at),
                    threadId: s.session_id,
                    compareCompanies,
                    alphaTicker,
                    // Reconstruct the same nice label the live run shows, instead of
                    // ever falling back to the raw backend-persisted query text.
                    savedResultLabel: isCompare && compareCompanies?.length
                        ? formatCompareResultLabel(compareCompanies, s.session_metadata?.year)
                        : isAlpha && alphaTicker
                            ? formatAlphaResultLabel(alphaTicker)
                            : undefined
                };
            });

            setChatSessions(mappedSessions);
        } catch (err) {
            console.error('Failed to fetch chat sessions:', err);
            showToast('Failed to load chat history.');
        } finally {
            setIsLoadingSessions(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId]);

    useEffect(() => {
        fetchSessions();
    }, [fetchSessions]);

    // When the parent asks us to focus a specific session (e.g. "View" from
    // the Analysis Dashboard or notification bell), find it once it shows up
    // in chatSessions. A just-completed task can be marked COMPLETED before
    // its session is queryable via getUserSessions — if it's missing on the
    // first pass, retry a few times with a short delay instead of silently
    // giving up (which left the pane blank until the user navigated away
    // and back, forcing a fresh mount/fetch).
    const focusRetriesRef = useRef(0);
    useEffect(() => {
        if (!focusSessionId) {
            focusRetriesRef.current = 0;
            return;
        }
        const match = chatSessions.find(s => s.threadId === focusSessionId);
        if (match) {
            setActiveSession(match);
            // The copy kept in memory can be from before the run finished (the screen was closed mid-run): read it fresh.
            fetchMessages(match);
            onFocusApplied?.();
            focusRetriesRef.current = 0;
            return;
        }
        if (focusRetriesRef.current >= 5) return;
        const timer = setTimeout(() => {
            focusRetriesRef.current += 1;
            fetchSessions();
        }, 1000);
        return () => clearTimeout(timer);
    }, [focusSessionId, chatSessions, onFocusApplied, fetchSessions]);

    // Load a saved session's messages when it is opened. An effect event reads the latest activeSession without
    // re-running when it changes (it changes as soon as the loaded history is stored), so this runs once per switch.
    const fetchMessages = useEffectEvent(async (reload?: ChatSession) => {
            // `reload`: fetch this session's history again even if it has messages (a run on it just finished on the server)
            const target = reload ?? activeSession;
            if (!target || !target.threadId || target.isNew) return;
            if (busySessionIds.has(target.id)) return;       // a run this screen started is still writing into it
            // Also stale: the last thing kept is the user's own turn, i.e. the screen closed before the answer came back.
            const last = target.messages[target.messages.length - 1];
            const stale = target.messages.length === 0 || last?.role === 'user';
            if (reload || stale) {
                const isActive = activeSession?.id === target.id;
                if (isActive) setIsLoadingMessages(true);
                try {
                    const history = await chatApi.getSessionHistory(target.threadId);

                    // ALPHA/Compare aren't a back-and-forth chat — the backend still
                    // stores the request as a real "user" turn (raw text like "Compare
                    // Exxon Mobil vs Chevron") for its own context, but we don't render
                    // that turn live (see runAlpha/getComparison), so it must also be
                    // dropped here when history is fetched fresh after navigating away
                    // and back. Use the same nicely-formatted label the live run shows
                    // (target.savedResultLabel, reconstructed in fetchSessions)
                    // rather than that raw backend text.
                    const isSynthesizedTurnSession = target.id.startsWith('alpha-') || target.id.startsWith('compare-');

                    const mappedMessages: ChatMessage[] = [];
                    history.messages.forEach((m, idx) => {
                        if (isSynthesizedTurnSession && m.role === 'user') {
                            return;
                        }

                        // Extract citations if documents exist in metadata
                        const citations: CitationSource[] = buildCitations(m.metadata?.documents, m.content);

                        mappedMessages.push({
                            id: `msg-${target.id}-${idx}`,
                            role: m.role as 'user' | 'assistant',
                            content: m.content,
                            timestamp: new Date(m.timestamp),
                            citations: citations.length > 0 ? citations : undefined,
                            webSearched: m.metadata?.web_searched,
                            webLinks: extractUrlsFromText(m.content),
                            chartUrl: m.metadata?.chart_url,
                            verification: m.metadata?.verification && Object.keys(m.metadata.verification).length ? m.metadata.verification : undefined,
                            usage: m.metadata?.usage?.total_tokens ? m.metadata.usage : undefined,
                            isAlpha: target.id.startsWith('alpha-') && m.role === 'assistant',
                            resultLabel: isSynthesizedTurnSession && m.role === 'assistant' ? target.savedResultLabel : undefined
                        });
                    });

                    setActiveSession(prev => prev && prev.id === target.id ? { ...prev, messages: mappedMessages } : prev);
                    setChatSessions(prev =>
                        prev.map(s => s.id === target.id ? { ...s, messages: mappedMessages } : s)
                    );
                } catch (err) {
                    console.error('Failed to fetch session history:', err);
                } finally {
                    if (isActive) setIsLoadingMessages(false);
                }
            }
    });

    useEffect(() => {
        fetchMessages();
    }, [activeSession?.id]);

    // ALPHA and Compare keep running on the server when this screen is closed. Coming back, the run is picked up from the
    // live run feed (the portfolio's task stream, kept by the Overview tab): its step-by-step panel shows again, and the
    // chat reloads from the server once it ends. Runs this screen started itself are tracked by runAlpha/getComparison.
    const portfolioRuns = useAnalysisRunsStore(s => s.runsByPortfolio[Number(projectId)]);
    const serverRunByThread = useMemo(() => {
        const byThread = new Map<string, AnalysisTask>();
        for (const t of Object.values(portfolioRuns ?? {})) {
            if (t.status !== 'PENDING' && t.status !== 'RUNNING') continue;
            const ref = (t.result_metadata as { session_ref?: string } | null)?.session_ref;
            if (ref) byThread.set(ref, t);
        }
        return byThread;
    }, [portfolioRuns]);
    const resumedRun = activeSession?.threadId && !busySessionIds.has(activeSession.id)
        ? serverRunByThread.get(activeSession.threadId) : undefined;
    // The latest run of the open chat that ended without an answer (cancelled or failed), to say so instead of the
    // "start a run" placeholder when the chat is empty.
    const endedRun = useMemo(() => {
        if (!activeSession?.threadId) return undefined;
        let latest: AnalysisTask | undefined;
        for (const t of Object.values(portfolioRuns ?? {})) {
            if ((t.result_metadata as { session_ref?: string } | null)?.session_ref !== activeSession.threadId) continue;
            if (!latest || t.created_at > latest.created_at) latest = t;
        }
        return latest && (latest.status === 'CANCELLED' || latest.status === 'FAILED') ? latest : undefined;
    }, [portfolioRuns, activeSession?.threadId]);

    // A chat deleted together with its run (from the dashboards) leaves this tab's list at once.
    const deletedChats = useAnalysisRunsStore(s => s.deletedChats);
    const dropDeletedChats = useEffectEvent((threads: string[]) => {
        const gone = new Set(threads);
        if (!chatSessions.some(x => x.threadId && gone.has(x.threadId))) return;
        setChatSessions(prev => prev.filter(x => !(x.threadId && gone.has(x.threadId))));
        if (activeSession?.threadId && gone.has(activeSession.threadId)) setActiveSession(null);
    });
    useEffect(() => { if (deletedChats.length) dropDeletedChats(deletedChats); }, [deletedChats]);

    const onServerRunEnded = useEffectEvent((threadId: string) => {
        const session = chatSessions.find(x => x.threadId === threadId);
        if (session && !busySessionIds.has(session.id)) fetchMessages(session);
    });
    const runningThreadsRef = useRef<Map<string, string>>(new Map());   // task id -> thread id, as of the last update
    useEffect(() => {
        const now = new Map<string, string>();
        for (const [thread, t] of serverRunByThread) now.set(t.id, thread);
        for (const [taskId, thread] of runningThreadsRef.current) {
            if (!now.has(taskId)) onServerRunEnded(thread);
        }
        runningThreadsRef.current = now;
    }, [serverRunByThread]);


    // Sync state changes to cache
    useEffect(() => {
        setSecondaryChatState(projectId, {
            sessions: chatSessions,
            activeSessionId: activeSession?.id || null
        });
    }, [chatSessions, activeSession?.id, projectId, setSecondaryChatState]);

    // Get companies with same SIC as current company context
    const currentSic = companies.length > 0 ? companies[0].sic : null;
    const sameSicCompanies = currentSic
        ? allCompanies.filter(c => c.sic === currentSic)
        : [];

    // Filter companies based on search for dropdown 1
    const filteredCompanies1 = sameSicCompanies.filter(c =>
        c.name.toLowerCase().includes(search1.toLowerCase()) ||
        c.ticker.toLowerCase().includes(search1.toLowerCase())
    );

    // Filter companies based on search for dropdown 2
    const filteredCompanies2 = sameSicCompanies.filter(c =>
        c.name.toLowerCase().includes(search2.toLowerCase()) ||
        c.ticker.toLowerCase().includes(search2.toLowerCase())
    );

    // Filter companies based on search for dropdown 3
    const filteredCompanies3 = sameSicCompanies.filter(c =>
        c.name.toLowerCase().includes(search3.toLowerCase()) ||
        c.ticker.toLowerCase().includes(search3.toLowerCase())
    );

    // ALPHA ticker dropdown is scoped to the portfolio's own holdings, not the SIC universe
    const filteredAlphaCompanies = companies.filter(c =>
        c.name.toLowerCase().includes(alphaSearch.toLowerCase()) ||
        c.ticker.toLowerCase().includes(alphaSearch.toLowerCase())
    );

    // Close dropdowns when clicking outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (dropdown1Ref.current && !dropdown1Ref.current.contains(event.target as Node)) {
                setDropdown1Open(false);
            }
            if (dropdown2Ref.current && !dropdown2Ref.current.contains(event.target as Node)) {
                setDropdown2Open(false);
            }
            if (dropdown3Ref.current && !dropdown3Ref.current.contains(event.target as Node)) {
                setDropdown3Open(false);
            }
            if (alphaDropdownRef.current && !alphaDropdownRef.current.contains(event.target as Node)) {
                setAlphaDropdownOpen(false);
            }
        };

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);


    // Generate unique thread ID for API conversation continuity
    const generateThreadId = () => {
        const timestamp = new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14);
        const random = Math.random().toString(36).substring(2, 8);
        return `thread_${timestamp}_${random}`;
    };

    const startNewChat = async () => {
        // Allow chat if companies are available OR files are selected
        if (selectedFiles.length === 0 && companies.length === 0) return;

        const threadId = generateThreadId();
        const sessionTitle = `${projectName} - Chat ${chatSessions.length + 1}`;

        try {
            // Register session with backend via portfolio sessions API
            const result = await portfolioApi.createSession({
                portfolio_id: parseInt(projectId),
                user_id: USER_ID,
                thread_id: threadId
            });

            const newSession: ChatSession = {
                id: result.thread_id,
                title: sessionTitle,
                files: [...selectedFiles],
                messages: [],
                createdAt: new Date(result.created_at),
                threadId: result.thread_id,
                isNew: true
            };

            setChatSessions(prev => [newSession, ...prev]);
            setActiveSession(newSession);
            setIsCompareMode(false);
            setIsAlphaMode(false);
        } catch (err) {
            console.error('Failed to register session:', err);
            showToast('⚠️ Session registration failed. Some RAG features might be limited.');
            // Still create the session locally to allow fallback/web search
            const newSession: ChatSession = {
                id: threadId,
                title: sessionTitle,
                files: [...selectedFiles],
                messages: [],
                createdAt: new Date(),
                threadId: threadId,
                isNew: true
            };

            setChatSessions(prev => [newSession, ...prev]);
            setActiveSession(newSession);
            setIsCompareMode(false);
            setIsAlphaMode(false);
        }
    };

    const startStockCompare = async () => {
        const threadId = generateThreadId();
        const sessionTitle = `Stock Comparison - ${chatSessions.length + 1}`;

        try {
            // Register compare session with portfolio sessions API
            const result = await portfolioApi.createSession({
                portfolio_id: parseInt(projectId),
                user_id: USER_ID,
                thread_id: threadId
            });

            const newSession: ChatSession = {
                id: `compare-${result.thread_id}`,
                title: sessionTitle,
                files: [],
                messages: [],
                createdAt: new Date(result.created_at),
                threadId: result.thread_id,
                isNew: true
            };

            setChatSessions(prev => [newSession, ...prev]);
            setActiveSession(newSession);
            setIsCompareMode(true);
            setIsAlphaMode(false);
            setCompareStock1('');
            setCompareStock2('');
            setCompareStock3('');
            setCompareYear(2024);
        } catch (err) {
            console.error('Failed to register comparison session:', err);
            showToast('⚠️ Session registration failed.');

            const newSession: ChatSession = {
                id: `compare-${threadId}`,
                title: sessionTitle,
                files: [],
                messages: [],
                createdAt: new Date(),
                threadId: threadId,
                isNew: true
            };

            setChatSessions(prev => [newSession, ...prev]);
            setActiveSession(newSession);
            setIsCompareMode(true);
            setIsAlphaMode(false);
            setCompareStock1('');
            setCompareStock2('');
            setCompareStock3('');
            setCompareYear(2024);
        }
    };

    // Opens the ALPHA ticker-picker bar for a fresh session — does NOT run
    // anything yet. The user picks a ticker from the dropdown and submits.
    const openAlphaBar = async () => {
        if (companies.length === 0) return;

        const threadId = generateThreadId();
        const sessionTitle = `ALPHA Analysis - ${chatSessions.length + 1}`;

        let session: ChatSession;
        try {
            const result = await portfolioApi.createSession({
                portfolio_id: parseInt(projectId),
                user_id: USER_ID,
                thread_id: threadId
            });

            session = {
                id: `alpha-${result.thread_id}`,
                title: sessionTitle,
                files: [],
                messages: [],
                createdAt: new Date(result.created_at),
                threadId: result.thread_id,
                isNew: true
            };
        } catch (err) {
            console.error('Failed to register ALPHA session:', err);
            showToast('⚠️ Session registration failed.');

            session = {
                id: `alpha-${threadId}`,
                title: sessionTitle,
                files: [],
                messages: [],
                createdAt: new Date(),
                threadId: threadId,
                isNew: true
            };
        }

        setChatSessions(prev => [session, ...prev]);
        setActiveSession(session);
        setIsCompareMode(false);
        setIsAlphaMode(true);
        setAlphaTicker('');
        setAlphaSearch('');
    };

    // Runs ALPHA for the selected ticker on whichever session was active when
    // submitted. The session id (not the live `activeSession` reference) is
    // used for every update below, so the user is free to switch to a
    // different chat/session while this is in flight — the result still
    // lands on the right session, and the active view is only force-updated
    // if the user is still looking at that same session when it resolves.
    const runAlpha = async () => {
        if (!alphaTicker || !activeSession) return;

        const session = activeSession;
        const ticker = alphaTicker;
        const alphaLabel = `360° ALPHA Analysis — ${ticker}`;

        // Not a chat turn — no synthesized "user" message. The session is just
        // marked busy/tagged with the ticker; the request context rides on the
        // result label once the report comes back.
        const sessionMarkedBusy: ChatSession = {
            ...session,
            isNew: false,
            alphaTicker: ticker
        };

        setActiveSession(prev => prev && prev.id === session.id ? sessionMarkedBusy : prev);
        setChatSessions(prev =>
            prev.map(s => s.id === session.id ? sessionMarkedBusy : s)
        );
        setBusySessionIds(prev => new Set(prev).add(session.id));
        setRunProgress({ sessionId: session.id, kind: 'alpha', progress: null, receivedAt: Date.now(), startedAt: Date.now() });

        try {
            const response = await secondaryAgentApi.alphaViaQueue(
                {
                    tickers: [ticker],
                    user_id: USER_ID,
                    thread_id: session.threadId!
                },
                (message, task) => {
                    setIntermediateMessage(message);
                    const progress = getRunProgress(task);
                    if (progress) setRunProgress(prev => prev && prev.sessionId === session.id ? { ...prev, progress, receivedAt: Date.now() } : prev);
                }
            );
            await onAnalysisEnqueued?.();

            const report = response.results.map(r => r.report).join('\n\n---\n\n');

            const aiMessage: ChatMessage = {
                id: `msg-${Date.now()}`,
                role: 'assistant',
                content: report,
                timestamp: new Date(),
                isAlpha: true,
                resultLabel: alphaLabel
            };

            const withAiResponse: ChatSession = {
                ...sessionMarkedBusy,
                messages: [...sessionMarkedBusy.messages, aiMessage],
                threadId: response.thread_id
            };

            setChatSessions(prev =>
                prev.map(s => s.id === session.id ? withAiResponse : s)
            );
            setActiveSession(prev => prev && prev.id === session.id ? withAiResponse : prev);
        } catch (err) {
            console.error('Error running ALPHA analysis:', err);
            const errorMessage: ChatMessage = {
                id: `msg-${Date.now()}`,
                role: 'assistant',
                content: err instanceof Error && err.message === 'Cancelled'
                    ? 'This ALPHA run was cancelled before it finished. Nothing was saved.'
                    : 'Sorry, I encountered an error running the ALPHA analysis. Please try again.',
                timestamp: new Date(),
                resultLabel: alphaLabel
            };

            const withErrorResponse: ChatSession = {
                ...sessionMarkedBusy,
                messages: [...sessionMarkedBusy.messages, errorMessage]
            };

            setChatSessions(prev =>
                prev.map(s => s.id === session.id ? withErrorResponse : s)
            );
            setActiveSession(prev => prev && prev.id === session.id ? withErrorResponse : prev);
        } finally {
            setBusySessionIds(prev => {
                const next = new Set(prev);
                next.delete(session.id);
                return next;
            });
            setRunProgress(prev => prev && prev.sessionId === session.id ? null : prev);
        }
    };

    const getComparison = async () => {
        if (!compareStock1 || !compareStock2 || !activeSession) return;

        const session = activeSession;
        // Keep comparison bar visible - don't set isCompareMode to false

        // Build comparison label based on number of stocks — this isn't a chat turn,
        // so there's no synthesized "user" message; the label rides on the result instead.
        const stockList = compareStock3
            ? `${compareStock1}, ${compareStock2}, and ${compareStock3}`
            : `${compareStock1} and ${compareStock2}`;
        const compareLabel = `${stockList} (${compareYear})`;
        setCompareRunLabel(compareLabel);

        const updatedSession = {
            ...session,
            isNew: false
        };

        setBusySessionIds(prev => new Set(prev).add(session.id));
        setRunProgress({
            sessionId: session.id, kind: 'compare', progress: null, receivedAt: Date.now(), startedAt: Date.now(),
            chips: [compareStock1, compareStock2, compareStock3].filter(Boolean) as string[],
        });

        try {
            // All compareStock values already store company names
            const company1Name = compareStock1;
            const company2Name = compareStock2;
            const company3Name = compareStock3 || undefined;

            const response = await secondaryAgentApi.compareViaQueue({
                company1: company1Name,
                company2: company2Name,
                company3: company3Name,
                user_id: USER_ID,
                thread_id: session.threadId,
                year: compareYear
            }, (task) => {
                const progress = getRunProgress(task);
                if (progress) setRunProgress(prev => prev && prev.sessionId === session.id ? { ...prev, progress, receivedAt: Date.now() } : prev);
            });
            await onAnalysisEnqueued?.();

            if (response.intermediate_message) {
                setIntermediateMessage(response.intermediate_message);
            }

            // Extract source files with page numbers from documents
            const citations: CitationSource[] = buildCitations(response.documents, response.answer);

            // Extract URLs from the answer text for web sources
            const extractedUrls = extractUrlsFromText(response.answer);

            const aiMessage: ChatMessage = {
                id: `msg-${Date.now()}`,
                role: 'assistant',
                content: response.answer,
                timestamp: new Date(),
                citations: citations,
                webSearched: response.web_searched,
                webLinks: extractedUrls,
                chartUrl: response.chart_url,
                resultLabel: compareLabel
            };

            const withAiResponse = {
                ...updatedSession,
                messages: [...updatedSession.messages, aiMessage],
                threadId: response.thread_id
            };

            setChatSessions(prev =>
                prev.map(s => s.id === session.id ? withAiResponse : s)
            );
            setActiveSession(prev => prev && prev.id === session.id ? withAiResponse : prev);
        } catch (err) {
            console.error('Error comparing stocks:', err);
            const errorMessage: ChatMessage = {
                id: `msg-${Date.now()}`,
                role: 'assistant',
                content: err instanceof Error && err.message === 'Cancelled'
                    ? 'This comparison was cancelled before it finished. Nothing was saved.'
                    : 'Sorry, I encountered an error comparing these stocks. Please try again.',
                timestamp: new Date(),
                resultLabel: compareLabel
            };

            const withErrorResponse = {
                ...updatedSession,
                messages: [...updatedSession.messages, errorMessage]
            };

            setChatSessions(prev =>
                prev.map(s => s.id === session.id ? withErrorResponse : s)
            );
            setActiveSession(prev => prev && prev.id === session.id ? withErrorResponse : prev);
        } finally {
            setBusySessionIds(prev => {
                const next = new Set(prev);
                next.delete(session.id);
                return next;
            });
            setIntermediateMessage(null);
            setCompareRunLabel(null);
            setRunProgress(prev => prev && prev.sessionId === session.id ? null : prev);
        }
    };

    const sendMessage = async () => {
        if (!messageInput.trim() || !activeSession) return;

        const session = activeSession;

        const userMessage: ChatMessage = {
            id: `msg-${Date.now()}`,
            role: 'user',
            content: messageInput,
            timestamp: new Date()
        };

        const updatedSession = {
            ...session,
            messages: [...session.messages, userMessage],
            isNew: false
        };

        setActiveSession(prev => prev && prev.id === session.id ? updatedSession : prev);
        setChatSessions(prev =>
            prev.map(s => s.id === session.id ? updatedSession : s)
        );
        const currentMessage = messageInput;
        setMessageInput('');
        setBusySessionIds(prev => new Set(prev).add(session.id));
        setError(null);

        try {
            // If the last turn was the pipeline asking which company/period you mean, this message is the answer to it.
            const lastTurn = session.messages[session.messages.length - 1];
            const answering = Boolean(lastTurn?.clarification);
            const originalQuery = answering ? ([...session.messages].reverse().find(m => m.role === 'user')?.content ?? currentMessage) : currentMessage;
            const savedSources = (settings.retrieval?.sources as string[] | undefined) ?? ['filings', 'uploads', 'notes', 'web', 'macro'];
            const request: SecondaryAskRequest = {
                query: originalQuery,
                thread_id: session.threadId || '',
                ...scopeToRequest(scope, savedSources),
                ...(answering ? { clarification: currentMessage } : {}),
            };
            const streaming = settings.answers?.streaming === true;
            setStreamDraft('');
            const response = streaming
                ? await secondaryAgentApi.askStream(request, ev => {
                    if (ev.type === 'step') setIntermediateMessage(STEP_LABEL[ev.node] ?? null);
                    else if (ev.type === 'token') setStreamDraft(d => d + ev.text);
                })
                : await secondaryAgentApi.askViaQueue(request);
            if (!streaming) await onAnalysisEnqueued?.();

            if (response.clarification_needed) {
                const ask: ChatMessage = { id: `msg-${Date.now()}`, role: 'assistant', content: response.question ?? 'Can you clarify what you mean?', timestamp: new Date(), clarification: true };
                const withAsk = { ...updatedSession, messages: [...updatedSession.messages, ask], threadId: response.thread_id || updatedSession.threadId };
                setChatSessions(prev => prev.map(s => s.id === session.id ? withAsk : s));
                setActiveSession(prev => prev && prev.id === session.id ? withAsk : prev);
                return;
            }

            if (response.intermediate_message) {
                setIntermediateMessage(response.intermediate_message);
            }

            // Extract source files with page numbers from documents
            const citations: CitationSource[] = buildCitations(response.documents, response.answer);

            // Extract URLs from the answer text for web sources
            const extractedUrls = extractUrlsFromText(response.answer);

            const aiMessage: ChatMessage = {
                id: `msg-${Date.now()}`,
                role: 'assistant',
                content: response.answer,
                timestamp: new Date(),
                citations: citations,
                webSearched: response.web_searched,
                webLinks: extractedUrls,
                chartUrl: response.chart_url,
                verification: response.verification && Object.keys(response.verification).length ? response.verification : undefined,
                usage: response.usage?.total_tokens ? response.usage : undefined
            };

            const withAiResponse = {
                ...updatedSession,
                messages: [...updatedSession.messages, aiMessage],
                threadId: response.thread_id // Store thread_id for conversation continuity
            };

            setChatSessions(prev =>
                prev.map(s => s.id === session.id ? withAiResponse : s)
            );
            setActiveSession(prev => prev && prev.id === session.id ? withAiResponse : prev);
        } catch (err) {
            console.error('Error calling Secondary Agent API:', err);
            setError(err instanceof Error ? err.message : 'Failed to get response from AI agent');

            // Add error message to chat
            const errorMessage: ChatMessage = {
                id: `msg-${Date.now()}`,
                role: 'assistant',
                content: 'Sorry, I encountered an error processing your request. Please try again.',
                timestamp: new Date()
            };

            const withErrorResponse = {
                ...updatedSession,
                messages: [...updatedSession.messages, errorMessage]
            };

            setChatSessions(prev =>
                prev.map(s => s.id === session.id ? withErrorResponse : s)
            );
            setActiveSession(prev => prev && prev.id === session.id ? withErrorResponse : prev);
        } finally {
            setBusySessionIds(prev => {
                const next = new Set(prev);
                next.delete(session.id);
                return next;
            });
            setIntermediateMessage(null);
            setStreamDraft('');
        }
    };

    const removeFileFromContext = (fileId: string) => {
        if (!activeSession) return;

        const updatedFiles = (activeSession.files || []).filter(id => id !== fileId);

        // Allow removing last file if companies provide context
        if (updatedFiles.length === 0 && companies.length === 0) {
            // Can't remove last file if no companies
            return;
        }

        const updatedSession = {
            ...activeSession,
            files: updatedFiles
        };

        setActiveSession(updatedSession);
        setChatSessions(prev =>
            prev.map(s => s.id === activeSession.id ? updatedSession : s)
        );
    };

    // const addFileToContext = (fileId: string) => {
    //     if (!activeSession || activeSession.files.includes(fileId)) return;

    //     const updatedSession = {
    //         ...activeSession,
    //         files: [...activeSession.files, fileId]
    //     };

    //     setActiveSession(updatedSession);
    //     setChatSessions(prev =>
    //         prev.map(s => s.id === activeSession.id ? updatedSession : s)
    //     );
    // };

    const handleRenameSession = (sessionId: string) => {
        const session = chatSessions.find(s => s.id === sessionId);
        if (session) {
            setEditingSessionId(sessionId);
            setEditingTitle(session.title);
        }
    };

    const confirmRename = async () => {
        if (!editingSessionId || !editingTitle.trim()) return;

        try {
            await chatApi.updateSessionTitle(editingSessionId, editingTitle.trim());

            setChatSessions(prev =>
                prev.map(s => s.id === editingSessionId ? { ...s, title: editingTitle.trim() } : s)
            );

            if (activeSession?.id === editingSessionId) {
                setActiveSession({ ...activeSession, title: editingTitle.trim() });
            }

            setEditingSessionId(null);
            setEditingTitle('');
            showToast('✓ Session renamed successfully');
        } catch (err) {
            console.error('Failed to rename session:', err);
            showToast('Failed to rename session.');
        }
    };

    const cancelRename = () => {
        setEditingSessionId(null);
        setEditingTitle('');
    };

    const handleDeleteSession = async (sessionId: string) => {
        try {
            // Look up the actual backend thread ID for this session
            const session = chatSessions.find(s => s.id === sessionId);
            const backendId = session?.threadId || sessionId;
            await chatApi.deleteSession(backendId);
            setChatSessions(prev => prev.filter(s => s.id !== sessionId));
            if (activeSession?.id === sessionId) {
                setActiveSession(null);
            }
            showToast('✓ Session deleted successfully');
        } catch (err) {
            console.error('Failed to delete session:', err);
            showToast('Failed to delete session.');
        }
    };

    // Export chat to PDF with rendered markdown
    const exportChatToPDF = async () => {
        if (!activeSession || activeSession.messages.length === 0 || !messagesContainerRef.current) return;

        const messagesContainer = messagesContainerRef.current;

        // Store original styles and scroll position
        const originalStyles = {
            overflow: messagesContainer.style.overflow,
            height: messagesContainer.style.height,
            maxHeight: messagesContainer.style.maxHeight,
            position: messagesContainer.style.position,
            flex: messagesContainer.style.flex,
        };
        const originalScrollTop = messagesContainer.scrollTop;

        // Get the parent container to also modify
        const parentContainer = messagesContainer.parentElement;
        const originalParentStyles = parentContainer ? {
            overflow: parentContainer.style.overflow,
            height: parentContainer.style.height,
        } : null;

        try {
            // Reset scroll position to top
            messagesContainer.scrollTop = 0;

            // Get the full scroll height before modifying styles
            const fullHeight = messagesContainer.scrollHeight;

            // Temporarily expand container to show ALL content
            messagesContainer.style.overflow = 'visible';
            messagesContainer.style.height = `${fullHeight}px`;
            messagesContainer.style.maxHeight = 'none';
            messagesContainer.style.position = 'relative';
            messagesContainer.style.flex = 'none';

            // Also expand parent if needed
            if (parentContainer) {
                parentContainer.style.overflow = 'visible';
                parentContainer.style.height = 'auto';
            }

            // Wait for styles to apply and images to load
            await new Promise(resolve => setTimeout(resolve, 100));

            // Wait for all images in the container to load
            const images = messagesContainer.querySelectorAll('img');
            await Promise.all(
                Array.from(images).map(img => {
                    if (img.complete) return Promise.resolve();
                    return new Promise(resolve => {
                        img.onload = resolve;
                        img.onerror = resolve;
                    });
                })
            );

            // Capture the rendered messages as canvas
            const canvas = await html2canvas(messagesContainer, {
                scale: 2, // Higher resolution for better quality
                useCORS: true,
                allowTaint: true,
                logging: false,
                backgroundColor: cssVar('--color-paper'),
                width: messagesContainer.offsetWidth,
                height: fullHeight,
                scrollX: 0,
                scrollY: 0,
                x: 0,
                y: 0,
            });

            // Create PDF
            const pdf = new jsPDF('p', 'mm', 'a4');
            const pageWidth = pdf.internal.pageSize.getWidth();
            const pageHeight = pdf.internal.pageSize.getHeight();
            const margin = 15;
            const contentWidth = pageWidth - margin * 2;

            // Add header
            pdf.setFontSize(14);
            pdf.setFont('helvetica', 'bold');
            pdf.setTextColor(32, 33, 35);
            pdf.text(activeSession.title, margin, margin + 5);

            pdf.setFontSize(9);
            pdf.setFont('helvetica', 'normal');
            pdf.setTextColor(128, 128, 128);
            pdf.text(`Exported: ${new Date().toLocaleString()}`, margin, margin + 11);

            if (companies.length > 0) {
                pdf.text(`Companies: ${companies.map(c => c.ticker).join(', ')}`, margin, margin + 16);
            }

            // Draw separator line
            pdf.setDrawColor(200, 200, 200);
            pdf.line(margin, margin + 20, pageWidth - margin, margin + 20);

            // Calculate image dimensions to fit page width
            const imgWidth = contentWidth;
            const imgHeight = (canvas.height * imgWidth) / canvas.width;

            // Convert canvas to image data
            const imgData = canvas.toDataURL('image/png');

            // Starting position after header
            const headerEndY = margin + 25;
            const availableHeightFirstPage = pageHeight - headerEndY - margin;
            const availableHeightOtherPages = pageHeight - margin * 2;

            // If image fits on first page (with header)
            if (imgHeight <= availableHeightFirstPage) {
                pdf.addImage(imgData, 'PNG', margin, headerEndY, imgWidth, imgHeight);
            } else {
                // Need to split across pages
                const pixelRatio = canvas.height / imgHeight; // pixels per mm

                let currentSourceY = 0; // in canvas pixels
                let isFirstPage = true;

                while (currentSourceY < canvas.height) {
                    const availableHeight = isFirstPage ? availableHeightFirstPage : availableHeightOtherPages;
                    const sourceHeightToCapture = Math.min(
                        availableHeight * pixelRatio,
                        canvas.height - currentSourceY
                    );
                    const destHeight = sourceHeightToCapture / pixelRatio;

                    // Create a temporary canvas for this portion
                    const tempCanvas = document.createElement('canvas');
                    tempCanvas.width = canvas.width;
                    tempCanvas.height = Math.ceil(sourceHeightToCapture);
                    const tempCtx = tempCanvas.getContext('2d');

                    if (tempCtx) {
                        tempCtx.drawImage(
                            canvas,
                            0, currentSourceY, canvas.width, sourceHeightToCapture,
                            0, 0, canvas.width, sourceHeightToCapture
                        );

                        const tempImgData = tempCanvas.toDataURL('image/png');
                        const drawY = isFirstPage ? headerEndY : margin;
                        pdf.addImage(tempImgData, 'PNG', margin, drawY, imgWidth, destHeight);
                    }

                    currentSourceY += sourceHeightToCapture;

                    if (currentSourceY < canvas.height) {
                        pdf.addPage();
                    }
                    isFirstPage = false;
                }
            }

            // Save the PDF
            const fileName = `${activeSession.title.replace(/[^a-z0-9]/gi, '_')}_${new Date().toISOString().split('T')[0]}.pdf`;
            pdf.save(fileName);
        } catch (error) {
            console.error('Error exporting PDF:', error);
        } finally {
            // Restore original styles
            messagesContainer.style.overflow = originalStyles.overflow;
            messagesContainer.style.height = originalStyles.height;
            messagesContainer.style.maxHeight = originalStyles.maxHeight;
            messagesContainer.style.position = originalStyles.position;
            messagesContainer.style.flex = originalStyles.flex;
            messagesContainer.scrollTop = originalScrollTop;

            // Restore parent styles
            if (parentContainer && originalParentStyles) {
                parentContainer.style.overflow = originalParentStyles.overflow;
                parentContainer.style.height = originalParentStyles.height;
            }
        }
    };

    return (
        <div className="secondary-research">
            {/* Left Panel - File Selector */}
            <ResizableSidebar
                collapsed={sidebarCollapsed}
                width={sidebarWidth}
                onExpand={() => setSidebarCollapsed(false)}
                onCollapse={() => setSidebarCollapsed(true)}
                resizeHandleRef={resizeHandleRef}
                onResizeMouseDown={handleResizeMouseDown}
                className="secondary-research__sidebar"
            >
              <div className="secondary-research__sidebar-fixed">
                {/* Companies Context Section */}
                {companies.length > 0 && (
                    <div className="secondary-research__companies-section">
                        <div className="secondary-research__sidebar-header">
                            <h3>Company Context</h3>
                            <span className="secondary-research__file-count">
                                {companies.length} companies
                            </span>
                        </div>
                        <div className="secondary-research__companies-list">
                            {companies.map(company => (
                                <div key={company.id} className="secondary-research__company-tag">
                                    <span className="company-ticker">{company.ticker}</span>
                                    <span className="company-name">{company.name}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                <p className="secondary-research__add-files-hint">
                    To add your own files or import from SharePoint, S3 or Drive, use <Link to="/documents/mine">My documents</Link>.
                </p>

                {/* <button
                    className="secondary-research__add-files-btn"
                    onClick={() => setIsForm4ModalOpen(true)}
                >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <polyline points="14 2 14 8 20 8" />
                        <line x1="12" y1="18" x2="12" y2="12" />
                        <line x1="9" y1="15" x2="15" y2="15" />
                    </svg>
                    Import Form4 Data
                </button> */}

                {/* Feature row - the three things Secondary Research can do. Always
                    visible (not just before any history exists) so they never get
                    pushed out of view by a growing history list. */}
                <div className="secondary-research__feature-row">
                    <button
                        className="secondary-research__feature-btn secondary-research__feature-btn--chat"
                        onClick={startNewChat}
                        disabled={selectedFiles.length === 0 && companies.length === 0}
                    >
                        <IoChatboxOutline size={14} />
                        New Chat
                    </button>

                    {companies.length > 0 && (
                        <button
                            className="secondary-research__feature-btn secondary-research__feature-btn--alpha"
                            onClick={openAlphaBar}
                        >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <circle cx="12" cy="12" r="10" />
                                <polyline points="12 6 12 12 16 14" />
                            </svg>
                            ALPHA Analysis
                        </button>
                    )}

                    <button
                        className="secondary-research__feature-btn secondary-research__feature-btn--compare"
                        onClick={startStockCompare}
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="18" y1="20" x2="18" y2="10" />
                            <line x1="12" y1="20" x2="12" y2="4" />
                            <line x1="6" y1="20" x2="6" y2="14" />
                        </svg>
                        Stock Compare
                    </button>
                </div>
              </div>

                {/* History sections - scrolls independently so the feature row above never gets pushed off-screen */}
                <div className="secondary-research__sidebar-scroll">
                {(chatSessions.length > 0 || isLoadingSessions) && (
                    <>
                        {isLoadingSessions && (
                            <div className="secondary-research__history">
                                <div className="sidebar-loader">
                                    <span className="spinner"></span>
                                    <span>Loading history...</span>
                                </div>
                            </div>
                        )}

                        {/* Compare History */}
                        {!isLoadingSessions && chatSessions.filter(s => s.id.startsWith('compare-')).length > 0 && (
                            <div className="secondary-research__history">
                                <h4>Compare History</h4>
                                {chatSessions.filter(s => s.id.startsWith('compare-')).map(session => (
                                    <div
                                        key={session.id}
                                        className="history-item__wrapper"
                                        onMouseEnter={() => setHoveredSessionId(session.id)}
                                        onMouseLeave={() => setHoveredSessionId(null)}
                                    >
                                        <button
                                            className={`secondary-research__history-item ${activeSession?.id === session.id ? 'secondary-research__history-item--active' : ''}`}
                                            onClick={() => {
                                                setActiveSession(session);
                                                setIsCompareMode(true);
                                                setIsAlphaMode(false);
                                                // Auto-populate compare fields from session metadata
                                                const companies = session.compareCompanies || [];
                                                setCompareStock1(companies[0] || '');
                                                setCompareStock2(companies[1] || '');
                                                setCompareStock3(companies[2] || '');
                                            }}
                                        >
                                            <span className="history-item__title">{session.title}</span>
                                            {session.compareCompanies && session.compareCompanies.length > 0 && (
                                                <span className="history-item__companies">
                                                    {session.compareCompanies.join(' vs ')}
                                                </span>
                                            )}
                                        </button>
                                        {hoveredSessionId === session.id && (
                                            <div className="history-item__actions">
                                                <button
                                                    className="history-item__action-btn"
                                                    onClick={(e) => { e.stopPropagation(); handleRenameSession(session.id); }}
                                                    title="Rename"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                                    </svg>
                                                </button>
                                                <button
                                                    className="history-item__action-btn history-item__action-btn--danger"
                                                    onClick={(e) => { e.stopPropagation(); handleDeleteSession(session.id); }}
                                                    title="Delete"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <polyline points="3 6 5 6 21 6" />
                                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                                    </svg>
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* ALPHA History */}
                        {!isLoadingSessions && chatSessions.filter(s => s.id.startsWith('alpha-')).length > 0 && (
                            <div className="secondary-research__history">
                                <h4>ALPHA History</h4>
                                {chatSessions.filter(s => s.id.startsWith('alpha-')).map(session => (
                                    <div
                                        key={session.id}
                                        className="history-item__wrapper"
                                        onMouseEnter={() => setHoveredSessionId(session.id)}
                                        onMouseLeave={() => setHoveredSessionId(null)}
                                    >
                                        <button
                                            className={`secondary-research__history-item ${activeSession?.id === session.id ? 'secondary-research__history-item--active' : ''}`}
                                            onClick={() => {
                                                setActiveSession(session);
                                                setIsCompareMode(false);
                                                setIsAlphaMode(true);
                                                setAlphaTicker(session.alphaTicker || '');
                                                setAlphaSearch('');
                                            }}
                                        >
                                            <span className="history-item__title">{session.title}</span>
                                            {busySessionIds.has(session.id) && (
                                                <span className="history-item__companies">Analyzing…</span>
                                            )}
                                            {session.alphaTicker && !busySessionIds.has(session.id) && (
                                                <span className="history-item__companies">{session.alphaTicker}</span>
                                            )}
                                        </button>
                                        {hoveredSessionId === session.id && (
                                            <div className="history-item__actions">
                                                <button
                                                    className="history-item__action-btn"
                                                    onClick={(e) => { e.stopPropagation(); handleRenameSession(session.id); }}
                                                    title="Rename"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                                    </svg>
                                                </button>
                                                <button
                                                    className="history-item__action-btn history-item__action-btn--danger"
                                                    onClick={(e) => { e.stopPropagation(); handleDeleteSession(session.id); }}
                                                    title="Delete"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <polyline points="3 6 5 6 21 6" />
                                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                                    </svg>
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Chat History */}
                        {!isLoadingSessions && (
                            <div className="secondary-research__history">
                                <h4>Chat History</h4>
                                {chatSessions.filter(s => !s.id.startsWith('compare-') && !s.id.startsWith('alpha-')).map(session => (
                                    <div
                                        key={session.id}
                                        className="history-item__wrapper"
                                        onMouseEnter={() => setHoveredSessionId(session.id)}
                                        onMouseLeave={() => setHoveredSessionId(null)}
                                    >
                                        <button
                                            className={`secondary-research__history-item ${activeSession?.id === session.id ? 'secondary-research__history-item--active' : ''}`}
                                            onClick={() => {
                                                setActiveSession(session);
                                                setIsCompareMode(false);
                                                setIsAlphaMode(false);
                                            }}
                                        >
                                            <span className="history-item__title">{session.title}</span>
                                        </button>
                                        {hoveredSessionId === session.id && (
                                            <div className="history-item__actions">
                                                <button
                                                    className="history-item__action-btn"
                                                    onClick={(e) => { e.stopPropagation(); handleRenameSession(session.id); }}
                                                    title="Rename"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                                    </svg>
                                                </button>
                                                <button
                                                    className="history-item__action-btn history-item__action-btn--danger"
                                                    onClick={(e) => { e.stopPropagation(); handleDeleteSession(session.id); }}
                                                    title="Delete"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <polyline points="3 6 5 6 21 6" />
                                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                                    </svg>
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                ))}
                                <button
                                    className="secondary-research__new-chat-btn"
                                    onClick={startNewChat}
                                >
                                    + New Chat
                                </button>
                            </div>
                        )}
                    </>
                )}
                </div>
            </ResizableSidebar>

            {/* Right Panel - Chat Area */}
            <div className="secondary-research__chat">
                {!activeSession ? (
                    <EmptyState
                        tintClassName="secondary-research__welcome-icon"
                        icon={
                            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <rect x="3" y="3" width="7" height="7" rx="1.5" />
                                <rect x="14" y="3" width="7" height="7" rx="1.5" />
                                <rect x="14" y="14" width="7" height="7" rx="1.5" />
                                <rect x="3" y="14" width="7" height="7" rx="1.5" />
                            </svg>
                        }
                        title="Secondary Research Agent"
                        description="Chat about your selected companies, run a side-by-side Stock Compare, or launch a full ALPHA Analysis."
                    />
                ) : (
                    <>
                        {/* Context Bar - Show comparison inputs when in compare mode */}
                        {isCompareMode ? (
                            <div className="stock-compare-bar">
                                {companies.length > 0 && companies[0].sic && (
                                    <span className="stock-compare-bar__sic">
                                        SIC: {companies[0].sic}
                                    </span>
                                )}
                                {/* Stock 1 Searchable Dropdown */}
                                <div className="stock-compare-bar__dropdown" ref={dropdown1Ref}>
                                    <input
                                        type="text"
                                        className="stock-compare-bar__input"
                                        placeholder="Search company..."
                                        value={dropdown1Open ? search1 : (compareStock1 ? `${sameSicCompanies.find(c => c.name.toLowerCase() === compareStock1.toLowerCase())?.ticker || ''} - ${compareStock1}` : '')}
                                        onChange={(e) => {
                                            setSearch1(e.target.value);
                                            if (!dropdown1Open) setDropdown1Open(true);
                                        }}
                                        onFocus={() => setDropdown1Open(true)}
                                    />
                                    {dropdown1Open && (
                                        <div className="stock-compare-bar__dropdown-menu">
                                            {filteredCompanies1.length > 0 ? (
                                                filteredCompanies1.map(company => (
                                                    <div
                                                        key={company.id}
                                                        className={`stock-compare-bar__dropdown-item ${compareStock1 === company.name ? 'selected' : ''}`}
                                                        onClick={() => {
                                                            setCompareStock1(company.name);
                                                            setSearch1('');
                                                            setDropdown1Open(false);
                                                        }}
                                                    >
                                                        <span className="dropdown-item__ticker">{company.ticker}</span>
                                                        <span className="dropdown-item__name">{company.name}</span>
                                                    </div>
                                                ))
                                            ) : (
                                                <div className="stock-compare-bar__dropdown-empty">No companies found</div>
                                            )}
                                        </div>
                                    )}
                                </div>
                                <span className="stock-compare-bar__vs">VS</span>
                                {/* Stock 2 Searchable Dropdown */}
                                <div className="stock-compare-bar__dropdown" ref={dropdown2Ref}>
                                    <input
                                        type="text"
                                        className="stock-compare-bar__input"
                                        placeholder="Search company..."
                                        value={dropdown2Open ? search2 : (compareStock2 ? `${sameSicCompanies.find(c => c.name.toLowerCase() === compareStock2.toLowerCase())?.ticker || ''} - ${compareStock2}` : '')}
                                        onChange={(e) => {
                                            setSearch2(e.target.value);
                                            if (!dropdown2Open) setDropdown2Open(true);
                                        }}
                                        onFocus={() => setDropdown2Open(true)}
                                    />
                                    {dropdown2Open && (
                                        <div className="stock-compare-bar__dropdown-menu">
                                            {filteredCompanies2.length > 0 ? (
                                                filteredCompanies2.map(company => (
                                                    <div
                                                        key={company.id}
                                                        className={`stock-compare-bar__dropdown-item ${compareStock2 === company.name ? 'selected' : ''}`}
                                                        onClick={() => {
                                                            setCompareStock2(company.name);
                                                            setSearch2('');
                                                            setDropdown2Open(false);
                                                        }}
                                                    >
                                                        <span className="dropdown-item__ticker">{company.ticker}</span>
                                                        <span className="dropdown-item__name">{company.name}</span>
                                                    </div>
                                                ))
                                            ) : (
                                                <div className="stock-compare-bar__dropdown-empty">No companies found</div>
                                            )}
                                        </div>
                                    )}
                                </div>
                                <span className="stock-compare-bar__vs">VS</span>
                                {/* Stock 3 Searchable Dropdown (Optional) */}
                                <div className="stock-compare-bar__dropdown stock-compare-bar__dropdown--optional" ref={dropdown3Ref}>
                                    <input
                                        type="text"
                                        className="stock-compare-bar__input stock-compare-bar__input--optional"
                                        placeholder="+ Add 3rd (optional)"
                                        value={dropdown3Open ? search3 : (compareStock3 ? `${sameSicCompanies.find(c => c.name.toLowerCase() === compareStock3.toLowerCase())?.ticker || ''} - ${compareStock3}` : '')}
                                        onChange={(e) => {
                                            setSearch3(e.target.value);
                                            if (!dropdown3Open) setDropdown3Open(true);
                                        }}
                                        onFocus={() => setDropdown3Open(true)}
                                    />
                                    {compareStock3 && (
                                        <button
                                            className="stock-compare-bar__clear"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setCompareStock3('');
                                                setSearch3('');
                                            }}
                                            title="Remove 3rd company"
                                        >
                                            ×
                                        </button>
                                    )}
                                    {dropdown3Open && (
                                        <div className="stock-compare-bar__dropdown-menu">
                                            {filteredCompanies3.length > 0 ? (
                                                filteredCompanies3.map(company => (
                                                    <div
                                                        key={company.id}
                                                        className={`stock-compare-bar__dropdown-item ${compareStock3 === company.name ? 'selected' : ''}`}
                                                        onClick={() => {
                                                            setCompareStock3(company.name);
                                                            setSearch3('');
                                                            setDropdown3Open(false);
                                                        }}
                                                    >
                                                        <span className="dropdown-item__ticker">{company.ticker}</span>
                                                        <span className="dropdown-item__name">{company.name}</span>
                                                    </div>
                                                ))
                                            ) : (
                                                <div className="stock-compare-bar__dropdown-empty">No companies found</div>
                                            )}
                                        </div>
                                    )}
                                </div>
                                {/* Year Selector */}
                                <div className="stock-compare-bar__year-group">
                                    <label className="stock-compare-bar__year-label">Year</label>
                                    <select
                                        className="stock-compare-bar__year-select"
                                        value={compareYear}
                                        onChange={(e) => setCompareYear(Number(e.target.value))}
                                    >
                                        {Array.from({ length: 10 }, (_, i) => 2016 + i).map(y => (
                                            <option key={y} value={y}>{y}</option>
                                        ))}
                                    </select>
                                </div>
                                <button
                                    className="stock-compare-bar__submit"
                                    onClick={getComparison}
                                    disabled={!compareStock1 || !compareStock2 || busySessionIds.has(activeSession.id)}
                                >
                                    {busySessionIds.has(activeSession.id) ? 'Comparing...' : 'Compare'}
                                </button>
                                {activeSession.messages.length > 0 && (
                                    <button
                                        className="stock-compare-bar__export"
                                        onClick={exportChatToPDF}
                                        title="Export to PDF"
                                    >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                            <polyline points="7 10 12 15 17 10" />
                                            <line x1="12" y1="15" x2="12" y2="3" />
                                        </svg>
                                        Export PDF
                                    </button>
                                )}
                            </div>
                        ) : isAlphaMode ? (
                            <div className="stock-compare-bar">
                                <div className="stock-compare-bar__dropdown" ref={alphaDropdownRef}>
                                    <input
                                        type="text"
                                        className="stock-compare-bar__input"
                                        placeholder="Select ticker..."
                                        value={alphaDropdownOpen ? alphaSearch : (alphaTicker ? `${alphaTicker} - ${companies.find(c => c.ticker.toLowerCase() === alphaTicker.toLowerCase())?.name || ''}` : '')}
                                        onChange={(e) => {
                                            setAlphaSearch(e.target.value);
                                            if (!alphaDropdownOpen) setAlphaDropdownOpen(true);
                                        }}
                                        onFocus={() => setAlphaDropdownOpen(true)}
                                    />
                                    {alphaDropdownOpen && (
                                        <div className="stock-compare-bar__dropdown-menu">
                                            {filteredAlphaCompanies.length > 0 ? (
                                                filteredAlphaCompanies.map(company => (
                                                    <div
                                                        key={company.id}
                                                        className={`stock-compare-bar__dropdown-item ${alphaTicker === company.ticker ? 'selected' : ''}`}
                                                        onClick={() => {
                                                            setAlphaTicker(company.ticker);
                                                            setAlphaSearch('');
                                                            setAlphaDropdownOpen(false);
                                                        }}
                                                    >
                                                        <span className="dropdown-item__ticker">{company.ticker}</span>
                                                        <span className="dropdown-item__name">{company.name}</span>
                                                    </div>
                                                ))
                                            ) : (
                                                <div className="stock-compare-bar__dropdown-empty">No companies found</div>
                                            )}
                                        </div>
                                    )}
                                </div>
                                <button
                                    className="stock-compare-bar__submit"
                                    onClick={runAlpha}
                                    disabled={!alphaTicker || busySessionIds.has(activeSession.id)}
                                >
                                    {busySessionIds.has(activeSession.id) ? 'Analyzing...' : 'Run ALPHA Analysis'}
                                </button>
                                {activeSession.messages.length > 0 && (
                                    <button
                                        className="stock-compare-bar__export"
                                        onClick={exportChatToPDF}
                                        title="Export to PDF"
                                    >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                            <polyline points="7 10 12 15 17 10" />
                                            <line x1="12" y1="15" x2="12" y2="3" />
                                        </svg>
                                        Export PDF
                                    </button>
                                )}
                            </div>
                        ) : (
                            <div className="secondary-research__context-bar">
                                <span className="context-bar__label">Context:</span>
                                <div className="context-bar__items">
                                    {/* Company context chips */}
                                    {companies.map(company => (
                                        <span key={company.id} className="context-bar__company-chip">
                                            {company.ticker}
                                        </span>
                                    ))}
                                    {/* File context chips */}
                                    {(activeSession.files || []).map(fileId => (
                                        <span key={fileId} className="context-bar__file-chip">
                                            {fileId}
                                            {(companies.length > 0 || (activeSession.files || []).length > 1) && (
                                                <button
                                                    className="context-bar__remove"
                                                    onClick={() => removeFileFromContext(fileId)}
                                                >
                                                    ×
                                                </button>
                                            )}
                                        </span>
                                    ))}
                                </div>

                                {/* Actions */}
                                <div className="context-bar__actions">
                                    {activeSession.messages.length > 0 && (
                                        <button
                                            className="context-bar__action-btn"
                                            onClick={exportChatToPDF}
                                            title="Export Chat to PDF"
                                        >
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                                <polyline points="7 10 12 15 17 10" />
                                                <line x1="12" y1="15" x2="12" y2="3" />
                                            </svg>
                                            <span>Export PDF</span>
                                        </button>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Messages */}
                        <div
                            ref={messagesContainerRef}
                            className={`secondary-research__messages${selectedText ? ' has-text-selection' : ''}`}
                            onMouseUp={handleTextSelection}
                        >
                            {isLoadingMessages && (
                                <div className="messages-loader">
                                    <span className="spinner"></span>
                                    <span>Fetching conversation...</span>
                                </div>
                            )}

                            {/* Clipboard selection popup */}
                            {selectedText && selectionPosition && (
                                <button
                                    className="clipboard-popup"
                                    style={{
                                        position: 'fixed',
                                        left: selectionPosition.x,
                                        top: selectionPosition.y,
                                        transform: 'translate(-50%, -100%)',
                                        zIndex: 1000
                                    }}
                                    // This button lives inside the same container that has
                                    // onMouseUp={handleTextSelection} — without stopping
                                    // propagation here, clicking the button bubbles its own
                                    // mouseup into that handler, which reads the now-collapsed
                                    // selection as empty and immediately clears selectedText/
                                    // selectionPosition, making the button disappear before (or
                                    // instead of) the click ever registering.
                                    onMouseDown={(e) => e.stopPropagation()}
                                    onMouseUp={(e) => e.stopPropagation()}
                                    onClick={addSelectedToClipboard}
                                >
                                    📋 Add to Clipboard
                                </button>
                            )}
                            {activeSession.messages.length === 0 && !busySessionIds.has(activeSession.id) && !resumedRun && endedRun ? (
                                <ChatMessage role="assistant">
                                    <p className="secondary-research__run-ended">
                                        {endedRun.status === 'CANCELLED'
                                            ? `This ${endedRun.task_type === 'alpha' ? 'ALPHA run' : endedRun.task_type === 'compare' ? 'comparison' : 'run'} was cancelled before it finished. Nothing was saved.`
                                            : `This run failed${endedRun.failed_step ? ` at ${endedRun.failed_step}` : ''}: ${endedRun.progress_message || 'unknown error'}. You can retry it from the Analysis Dashboard.`}
                                    </p>
                                </ChatMessage>
                            ) : activeSession.messages.length === 0 && !busySessionIds.has(activeSession.id) && !resumedRun ? (
                                isAlphaMode ? (
                                    <div className="empty-state empty-state--lg">
                                        <img src={alphaLogo} alt="ALPHA Analysis" className="secondary-research__alpha-empty-logo" />
                                        <p className="empty-state__title">Run a 360° ALPHA Analysis</p>
                                        <p className="empty-state__description">Pick a ticker from your portfolio above and submit — I'll pull filings, insider trades, and financials into one consolidated view.</p>
                                    </div>
                                ) : isCompareMode ? (
                                    <EmptyState
                                        tintClassName="secondary-research__compare-empty-icon"
                                        icon={
                                            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                <line x1="18" y1="20" x2="18" y2="10" />
                                                <line x1="12" y1="20" x2="12" y2="4" />
                                                <line x1="6" y1="20" x2="6" y2="14" />
                                            </svg>
                                        }
                                        title="Compare Companies"
                                        description="Pick two or three companies and a year above, then hit Compare — I'll pull their financials side by side."
                                    />
                                ) : (
                                    <EmptyState
                                        tintClassName="secondary-research__welcome-icon"
                                        icon={
                                            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                <path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h1a7 7 0 0 1 7 7h1a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1h-1v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1H2a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h1a7 7 0 0 1 7-7h1V5.73c-.6-.34-1-.99-1-1.73a2 2 0 0 1 2-2z" />
                                                <circle cx="7.5" cy="14.5" r="1" />
                                                <circle cx="16.5" cy="14.5" r="1" />
                                            </svg>
                                        }
                                        title="Research Assistant"
                                        description="I can help you analyze company data, extract insights, compare financials, and answer questions about your selected companies."
                                    />
                                )
                            ) : (
                                activeSession.messages.map(msg => (
                                    <ChatMessage
                                        key={msg.id}
                                        role={msg.role}
                                        userName={userDisplayName}
                                        onClip={() => addMessageToClipboard(msg)}
                                    >
                                        {msg.role === 'assistant' ? (
                                            <>
                                                {msg.resultLabel && (
                                                    <div className="chat-message__result-label">{msg.resultLabel}</div>
                                                )}
                                                <ReactMarkdown
                                                    remarkPlugins={[[remarkGfm, { singleTilde: false }], [remarkMath, { singleDollarTextMath: false }]]}
                                                    rehypePlugins={[rehypeKatex]}
                                                    components={{
                                                        table: ({ node, children, ...props }) => {
                                                            const handleClipTable = (e: React.MouseEvent<HTMLButtonElement>) => {
                                                                const button = e.currentTarget;
                                                                const wrapper = button.closest('.chat-message__table-wrapper');
                                                                const tableEl = wrapper?.querySelector('table');
                                                                if (tableEl && activeSession) {
                                                                    addToSecondaryClipboard(USER_ID, projectId, {
                                                                        text: tableEl.innerText || '[Data Table]',
                                                                        html: tableEl.outerHTML,
                                                                        sessionTitle: activeSession.title
                                                                    }, activeSession.threadId);
                                                                    showToast('✓ Table added to clipboard successfully');
                                                                }
                                                            };
                                                            return (
                                                                <div className="chat-message__table-wrapper">
                                                                    <button 
                                                                        className="chat-message__clip-table-btn" 
                                                                        onClick={handleClipTable}
                                                                        title="Clip this table to report"
                                                                    >
                                                                        📋 Clip Table
                                                                    </button>
                                                                    <div className="chat-message__table-scroll">
                                                                        <table className="chat-message__table" {...props}>
                                                                            {children}
                                                                        </table>
                                                                    </div>
                                                                </div>
                                                            );
                                                        },
                                                        ...(msg.isAlpha ? {
                                                            p: ({ children }) => <p>{colorizeValueChildren(children)}</p>,
                                                            li: ({ children }) => <li>{colorizeValueChildren(children)}</li>,
                                                            td: ({ node, children, ...props }) => <td {...props}>{colorizeValueChildren(children)}</td>,
                                                            th: ({ node, children, ...props }) => <th {...props}>{colorizeValueChildren(children)}</th>,
                                                            strong: ({ children }) => <strong>{colorizeValueChildren(children)}</strong>,
                                                            em: ({ children }) => <em>{colorizeValueChildren(children)}</em>,
                                                        } : {})
                                                    }}
                                                >
                                                    {preprocessMathContent(stripUrlsFromText(msg.content))}
                                                </ReactMarkdown>
                                                {msg.role === 'assistant' && (msg.verification || msg.usage) && (
                                                    <div className="answer-meta">
                                                        {msg.verification?.status === 'verified' && <span className="answer-meta__badge answer-meta__badge--ok" title={`${msg.verification.supported} of ${msg.verification.checked} figures found in the documents`}>✓ Figures checked against sources</span>}
                                                        {msg.verification?.status === 'issues' && <span className="answer-meta__badge answer-meta__badge--warn" title={msg.verification.issues.map(i => `${i.text}: ${i.detail}`).join('\n')}>{msg.verification.issues.length} figure{msg.verification.issues.length === 1 ? '' : 's'} to double-check</span>}
                                                        {showCost && msg.usage && <span>{msg.usage.total_tokens.toLocaleString()} tokens{msg.usage.cost ? ` · ≈ $${msg.usage.cost.estimated_cost_usd.toFixed(3)}` : ''}</span>}
                                                    </div>
                                                )}
                                                {msg.clarification && <div className="chat-clarify">Reply below to continue.</div>}
                                                {msg.chartUrl && (
                                                    <div className="chat-message__chart">
                                                        <img
                                                            src={msg.chartUrl}
                                                            alt="Comparison Chart"
                                                            className="chat-message__chart-img"
                                                            title="Click to add to clipboard"
                                                            onClick={() => msg.chartUrl && addImageToClipboard(msg.chartUrl)}
                                                        />
                                                    </div>
                                                )}
                                            </>
                                        ) : (
                                            msg.content
                                        )}

                                        {/* Citation and Source Info for assistant messages - inside content for proper flow */}
                                        {msg.role === 'assistant' && ((msg.citations && msg.citations.length > 0) || (msg.webLinks && msg.webLinks.length > 0)) && (
                                            <div className="chat-message__sources" style={{ display: 'block', textAlign: 'left' }}>
                                                {/* SEC Filing Citations with rich document cards & page badges */}
                                                {msg.citations && msg.citations.length > 0 && (
                                                    <div className="sec-citations-container">
                                                        <div className="sec-citations-title">
                                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                                                <polyline points="14 2 14 8 20 8" />
                                                                <line x1="16" y1="13" x2="8" y2="13" />
                                                                <line x1="16" y1="17" x2="8" y2="17" />
                                                            </svg>
                                                            <span>Citations</span>
                                                        </div>
                                                        <div className="sec-citations-grid">
                                                            {msg.citations.map((citation, idx) => {
                                                                const parsed = parseFilingInfo(citation.file, citation);
                                                                const cleanBadge = parsed.filingType.toLowerCase().replace(/[^a-z0-9]/g, '');
                                                                const citationKey = `${msg.id}-${idx}`;
                                                                const isExpanded = expandedCitations.has(citationKey);

                                                                return (
                                                                    <div key={idx} className={`sec-citation-card ${isExpanded ? 'sec-citation-card--expanded' : 'sec-citation-card--compact'}`}>
                                                                        {/* One-Liner Compact Bar (Acts as Expand/Collapse Header) */}
                                                                        <div
                                                                            className="sec-citation-card__bar"
                                                                            onClick={() => toggleCitationExpand(citationKey)}
                                                                        >
                                                                            <div className="sec-citation-card__bar-left">
                                                                                <span className={`sec-badge sec-badge--${cleanBadge}`}>
                                                                                    {parsed.filingType}
                                                                                </span>
                                                                                <span className="sec-citation-card__company">
                                                                                    {parsed.ticker}
                                                                                </span>
                                                                                <span className="sec-citation-card__title" title={citation.file}>
                                                                                    {parsed.filingName}
                                                                                </span>
                                                                                {parsed.dateStr && (
                                                                                    <span className="sec-citation-card__meta-item">
                                                                                        • {parsed.dateStr}
                                                                                    </span>
                                                                                )}
                                                                            </div>
                                                                            <div className="sec-citation-card__bar-right">
                                                                                <button
                                                                                    className="sec-citation-card__icon-btn"
                                                                                    title="Copy Full Citation Info"
                                                                                    onClick={(e) => {
                                                                                        e.stopPropagation();
                                                                                        const pagesText = citation.pages && citation.pages.length > 0 ? citation.pages.join(', ') : 'N/A';
                                                                                        const dateText = parsed.dateStr ? ` • Date: ${parsed.dateStr}` : '';
                                                                                        const fullCitationText = `[SEC Citation] ${parsed.ticker} ${parsed.filingType} (${parsed.filingName})${dateText} • Pages: ${pagesText} • Source File: ${citation.file}`;
                                                                                        copyCitationText(fullCitationText, 'Full citation copied to clipboard!');
                                                                                    }}
                                                                                >
                                                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                                                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                                                                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                                                                    </svg>
                                                                                </button>
                                                                                <button
                                                                                    className="sec-citation-card__icon-btn"
                                                                                    title={isExpanded ? "Collapse Details" : "Expand Details"}
                                                                                    onClick={(e) => {
                                                                                        e.stopPropagation();
                                                                                        toggleCitationExpand(citationKey);
                                                                                    }}
                                                                                >
                                                                                    <svg
                                                                                        width="13"
                                                                                        height="13"
                                                                                        viewBox="0 0 24 24"
                                                                                        fill="none"
                                                                                        stroke="currentColor"
                                                                                        strokeWidth="2"
                                                                                        style={{
                                                                                            transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                                                                                            transition: 'transform 0.2s ease'
                                                                                        }}
                                                                                    >
                                                                                        <polyline points="6 9 12 15 18 9" />
                                                                                    </svg>
                                                                                </button>
                                                                            </div>
                                                                        </div>

                                                                        {/* Expanded Details Panel */}
                                                                        {isExpanded && (
                                                                            <div className="sec-citation-card__details">
                                                                                <div className="sec-citation-details__grid">
                                                                                    <div className="sec-citation-details__item">
                                                                                        <span className="sec-citation-details__label">Company Ticker</span>
                                                                                        <span className="sec-citation-details__value">{parsed.ticker}</span>
                                                                                    </div>
                                                                                    <div className="sec-citation-details__item">
                                                                                        <span className="sec-citation-details__label">Filing Type</span>
                                                                                        <span className="sec-citation-details__value">{parsed.filingType} ({parsed.filingName})</span>
                                                                                    </div>
                                                                                    <div className="sec-citation-details__item">
                                                                                        <span className="sec-citation-details__label">Filing / Period Date</span>
                                                                                        <span className="sec-citation-details__value">{parsed.dateStr || 'N/A'}</span>
                                                                                    </div>
                                                                                    <div className="sec-citation-details__item">
                                                                                        <span className="sec-citation-details__label">Full Source File</span>
                                                                                        <span className="sec-citation-details__value sec-citation-details__value--mono">{citation.file}</span>
                                                                                    </div>
                                                                                </div>

                                                                                {citation.sourceType === 'analyst_note' && citation.docId && (
                                                                                    <div className="sec-citation-details__pages-section">
                                                                                        <a className="sec-page-pill sec-page-pill--expanded" href={`/notes/${encodeURIComponent(citation.docId)}`} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} style={{ textDecoration: 'none' }}>
                                                                                            📝 Open note
                                                                                        </a>
                                                                                    </div>
                                                                                )}
                                                                                {citation.pages && citation.pages.length > 0 && citation.sourceType !== 'analyst_note' && (
                                                                                    <div className="sec-citation-details__pages-section">
                                                                                        <span className="sec-citation-details__label">All Cited Pages ({citation.pages.length}):</span>
                                                                                        <div className="sec-citation-card__pills" style={{ marginTop: '6px' }}>
                                                                                            {citation.pages.map((pg, pIdx) => (
                                                                                                <a
                                                                                                    key={pIdx}
                                                                                                    className="sec-page-pill sec-page-pill--expanded"
                                                                                                    href={citationHref(citation, pg)}
                                                                                                    onMouseEnter={() => warmCitation(citation, pg)}
                                                                                                    onFocus={() => warmCitation(citation, pg)}
                                                                                                    title={`Preview page ${pg} (Cmd/Ctrl-click opens the Document Inspector in a new tab)`}
                                                                                                    onClick={(e) => {
                                                                                                        e.stopPropagation();
                                                                                                        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
                                                                                                        e.preventDefault();
                                                                                                        openCitation(citation, pg);
                                                                                                    }}
                                                                                                    style={{ background: 'inherit', border: 'inherit', color: 'inherit', cursor: 'pointer', padding: 'inherit', font: 'inherit', textDecoration: 'none' }}
                                                                                                >
                                                                                                    📄 Page {pg}
                                                                                                </a>
                                                                                            ))}
                                                                                        </div>
                                                                                    </div>
                                                                                )}

                                                                                <div className="sec-citation-details__actions">
                                                                                    <button
                                                                                        className="sec-citation-action-btn"
                                                                                        onClick={(e) => {
                                                                                            e.stopPropagation();
                                                                                            const pagesText = citation.pages && citation.pages.length > 0 ? citation.pages.join(', ') : 'N/A';
                                                                                            const fullMultiLine = [
                                                                                                `Company / Ticker: ${parsed.ticker}`,
                                                                                                `Filing Type: ${parsed.filingType} (${parsed.filingName})`,
                                                                                                parsed.dateStr ? `Period / Date: ${parsed.dateStr}` : null,
                                                                                                `Cited Pages: ${pagesText}`,
                                                                                                `Source File: ${citation.file}`
                                                                                            ].filter(Boolean).join('\n');
                                                                                            copyCitationText(fullMultiLine, 'Copied formatted citation!');
                                                                                        }}
                                                                                    >
                                                                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                                                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                                                                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                                                                        </svg>
                                                                                        Copy Formatted Citation
                                                                                    </button>
                                                                                    <button
                                                                                        className="sec-citation-action-btn"
                                                                                        onClick={(e) => {
                                                                                            e.stopPropagation();
                                                                                            if (citation.pages && citation.pages.length > 0) {
                                                                                                copyCitationText(`Pages: ${citation.pages.join(', ')}`, 'Copied page list!');
                                                                                            }
                                                                                        }}
                                                                                    >
                                                                                        Copy Pages List
                                                                                    </button>
                                                                                </div>
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                );
                                                            })}
                                                        </div>
                                                    </div>
                                                )}

                                                {/* Web sources with clickable links */}
                                                {msg.webLinks && msg.webLinks.length > 0 && (
                                                    <div style={{ display: 'block', marginTop: msg.citations && msg.citations.length > 0 ? '12px' : '0' }}>
                                                        <div style={{ fontWeight: 600, color: 'var(--color-info-dark)', marginBottom: '4px' }}>🌐 Web Sources:</div>
                                                        <div className="web-links">
                                                            {msg.webLinks.map((url, idx) => {
                                                                // Extract domain name for display
                                                                const domain = new URL(url).hostname.replace('www.', '');
                                                                return (
                                                                    <a
                                                                        key={idx}
                                                                        href={url}
                                                                        target="_blank"
                                                                        rel="noopener noreferrer"
                                                                        className="web-link"
                                                                    >
                                                                        {domain}
                                                                    </a>
                                                                );
                                                            })}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </ChatMessage>
                                ))
                            )}
                            {!busySessionIds.has(activeSession.id) && resumedRun && (
                                <ChatMessage role="assistant">
                                    <RunProgressPanel
                                        kind={resumedRun.task_type === 'alpha' ? 'alpha' : resumedRun.task_type === 'compare' ? 'compare' : 'ask'}
                                        progress={getRunProgress(resumedRun)}
                                        receivedAt={getRunProgress(resumedRun)?.updated_at ?? Date.parse(resumedRun.updated_at)}
                                        startedAt={Date.parse(resumedRun.started_at ?? resumedRun.created_at)}
                                        chips={resumedRun.task_type === 'compare' ? resumedRun.tickers : undefined}
                                    />
                                </ChatMessage>
                            )}
                            {busySessionIds.has(activeSession.id) && (
                                <ChatMessage role="assistant">
                                    {streamDraft && <div className="chat-stream-draft">{streamDraft}</div>}
                                    {runProgress && runProgress.sessionId === activeSession.id ? (
                                        <RunProgressPanel
                                            kind={runProgress.kind}
                                            progress={runProgress.progress}
                                            receivedAt={runProgress.receivedAt}
                                            startedAt={runProgress.startedAt}
                                            chips={runProgress.chips}
                                        />
                                    ) : (
                                        <ChatMessageStatus
                                            text={
                                                isAlphaMode
                                                    ? `Running 360° ALPHA analysis for ${activeSession.alphaTicker || alphaTicker} stock…`
                                                    : compareRunLabel
                                                        ? `Comparing ${compareRunLabel}…`
                                                        : intermediateMessage || undefined
                                            }
                                        />
                                    )}
                                </ChatMessage>
                            )}
                            <div ref={messagesEndRef} />
                        </div>

                        {/* Input Area - ALPHA and Compare run off their own controls above, no free-text query */}
                        {!isAlphaMode && !isCompareMode && (
                            <ScopeBar scope={scope} onChange={setScope} disabled={busySessionIds.has(activeSession.id)} />
                        )}
                        {!isAlphaMode && !isCompareMode && (
                            <div className="secondary-research__input-area">
                                <AutoResizingTextarea
                                    className="secondary-research__input"
                                    placeholder={messageInput || !activeSession.messages.length || !activeSession.messages[activeSession.messages.length - 1].clarification ? 'Ask a question about the companies or your files...' : 'Answer the question above...'}
                                    value={messageInput}
                                    onChange={(e) => setMessageInput(e.target.value)}
                                    onSend={sendMessage}
                                />
                                <Button
                                    variant="primary"
                                    onClick={sendMessage}
                                    disabled={!messageInput.trim()}
                                >
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <line x1="22" y1="2" x2="11" y2="13" />
                                        <polygon points="22 2 15 22 11 13 2 9 22 2" />
                                    </svg>
                                </Button>
                            </div>
                        )}
                    </>
                )}
            </div>

            {citationPreview && (
                <Suspense fallback={null}>
                    <CitationModal
                        key={`${citationPreview.docId}:${citationPreview.page}`} {...citationPreview} portfolioId={projectId} sessionId={activeSession?.id}
                        onClose={() => setCitationPreview(null)}
                    />
                </Suspense>
            )}

            {/* Rename Modal */}
            <RenameModal
                isOpen={!!editingSessionId}
                title="Rename Chat"
                value={editingTitle}
                onChange={setEditingTitle}
                onCancel={cancelRename}
                onConfirm={confirmRename}
            />

            {/* Form4 Import Dialog */}
            <Form4ImportModal
                isOpen={isForm4ModalOpen}
                onClose={() => setIsForm4ModalOpen(false)}
                companies={companies}
                onShowToast={showToast}
            />

            {/* Toast Notification */}
            {toastMessage && (
                <div className="secondary-research__toast">
                    {toastMessage}
                </div>
            )}
        </div>
    );
};

export default SecondaryResearch;
