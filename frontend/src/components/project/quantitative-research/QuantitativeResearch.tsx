import { API_BASE } from '../../../services/http';
import React, { useState, useRef, useEffect, useCallback, useMemo, useEffectEvent } from 'react';
import { jsPDF } from 'jspdf';
import html2canvas from 'html2canvas';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { Button, AutoResizingTextarea, ChatMessage, ChatMessageStatus, RenameModal, ResizableSidebar, EmptyState } from '../../ui';
import { useResizableSidebar } from '../../../hooks/useResizableSidebar';
import { quantAgentApi } from '../../../services/quantAgentApi';
import { portfolioApi } from '../../../services/portfolioApi';
import { chatApi } from '../../../services/chatApi';
import { integrationApi } from '../../../services/integrationApi';
import { useClipboardActions } from '../../../hooks/useClipboardActions';
import { useChatCache } from '../../../context/useChatCache';
import { useAuth } from '../../../context/useAuth';
import { getDataSources, getSelectedSources, selectSource, deselectSource, type DataSource } from '../../../features/dataSources/dataSourceStore';
import './QuantitativeResearch.css';

interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    sources?: string[];
}

interface ChatSession {
    id: string;
    title: string;
    messages: ChatMessage[];
    createdAt: Date;
    apiSessionId?: string; // Session ID from the API for conversation memory
    isNew?: boolean; // Flag to skip history fetch for brand new sessions
}

interface QuantitativeResearchProps {
    projectId: string;
    projectName: string;
    /** When set (e.g. from the Overview dashboard's "View" action), focuses
     * the chat session whose apiSessionId matches, once it's available. */
    focusSessionId?: string;
    /** Called once focusSessionId has been applied, so the parent can clear it. */
    onFocusApplied?: () => void;
    /** Called immediately after enqueueing an analysis, so the parent can
     * refetch portfolio-list run counts and show the loader right away. */
    onAnalysisEnqueued?: () => void | Promise<void>;
}

export const QuantitativeResearch: React.FC<QuantitativeResearchProps> = ({ projectId, projectName, focusSessionId, onFocusApplied, onAnalysisEnqueued }) => {
    const { getQuantChatState, setQuantChatState } = useChatCache();

    // Initialize state from cache - sessions list only, no auto-restore of active session
    // so the empty/welcome state always shows when switching to the tab
    const cachedState = getQuantChatState(projectId);
    const [chatSessions, setChatSessions] = useState<ChatSession[]>(cachedState.sessions);
    const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
    const [messageInput, setMessageInput] = useState('');
    // Tracks which sessions currently have a request in flight, so the loader
    // only shows on the session that's actually waiting — switching to a
    // different session while one is running no longer shows its spinner too.
    const [busySessionIds, setBusySessionIds] = useState<Set<string>>(new Set());
    const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
    const [editingTitle, setEditingTitle] = useState('');
    const [hoveredSessionId, setHoveredSessionId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [selectedText, setSelectedText] = useState<string>('');
    const [selectedHtml, setSelectedHtml] = useState<string>('');
    const [selectionPosition, setSelectionPosition] = useState<{ x: number; y: number } | null>(null);
    const [agentUsed, setAgentUsed] = useState<string | null>(null);
    void agentUsed;
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const messagesContainerRef = useRef<HTMLDivElement>(null);
    const scrollTimeoutRef = useRef<number | null>(null);
    const prevMessageCountRef = useRef<number>(0);
    const { addToQuantClipboard } = useClipboardActions();
    const { userId: USER_ID, user } = useAuth();
    const userDisplayName = user?.full_name || user?.username || 'You';


    // Data sources state
    const [availableDataSources, setAvailableDataSources] = useState<DataSource[]>(getDataSources);
    const [selectedDataSources, setSelectedDataSources] = useState<string[]>(getSelectedSources);

    // Refresh data sources when component mounts or becomes visible
    useEffect(() => {
        const loadAllSources = async () => {
            const localSources = getDataSources();
            let remoteSources: DataSource[] = [];
            
            if (user) {
                try {
                    const integrations = await integrationApi.getUserIntegrations(USER_ID);
                    const postgresIntegrations = integrations.filter(i => i.vendor === 'postgresql');
                    remoteSources = postgresIntegrations.map(i => ({
                        id: i.id.toString(),
                        name: i.name,
                        provider: 'PostgreSQL',
                        baseUrl: i.url || '',
                        apiKey: '', // Hidden by API
                        description: i.description || '',
                        status: i.status === 'active' ? 'active' : 'inactive',
                        isDefault: false,
                        credentials: i.credentials_summary || {},
                    }));
                } catch (err) {
                    console.error('Failed to load remote data sources', err);
                }
            }
            
            setAvailableDataSources([...localSources, ...remoteSources]);
            setSelectedDataSources(getSelectedSources());
        };
        
        loadAllSources();
    }, [user, USER_ID]);

    const handleToggleDataSource = (id: string) => {
        const ds = availableDataSources.find(d => d.id === id);
        if (ds?.isDefault) return; // Cannot toggle default source
        
        if (selectedDataSources.includes(id)) {
            try {
                deselectSource(id);
                setSelectedDataSources(getSelectedSources());
            } catch {
                showToast('At least one data source must remain selected.');
            }
        } else {
            selectSource(id);
            setSelectedDataSources(getSelectedSources());
        }
    };

    // Toast notification state
    const [toastMessage, setToastMessage] = useState<string | null>(null);

    const showToast = (message: string) => {
        setToastMessage(message);
        setTimeout(() => setToastMessage(null), 3000);
    };

    // Inner sidebar collapse + resize state
    const {
        collapsed: sidebarCollapsed,
        setCollapsed: setSidebarCollapsed,
        width: sidebarWidth,
        resizeHandleRef,
        handleResizeMouseDown,
    } = useResizableSidebar({ defaultWidth: 220, min: 160, max: 360 });

    const handleTextSelection = () => {
        const selection = window.getSelection();
        const text = selection?.toString().trim();
        if (text && text.length > 0 && selection && selection.rangeCount > 0) {
            const range = selection.getRangeAt(0);
            const rect = range.getBoundingClientRect();

            // Capture HTML content from selection
            const container = document.createElement('div');
            container.appendChild(range.cloneContents());
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
            addToQuantClipboard(USER_ID, projectId, {
                text: selectedText,
                html: selectedHtml || undefined,
                sessionTitle: activeSession.title
            }, activeSession.apiSessionId);
            setSelectedText('');
            setSelectedHtml('');
            setSelectionPosition(null);
            window.getSelection()?.removeAllRanges();
            showToast('✓ Item added to clipboard successfully');
        }
    };

    const addImageToClipboard = (imageUrl: string) => {
        if (activeSession) {
            addToQuantClipboard(USER_ID, projectId, {
                text: '',
                imageUrl: imageUrl,
                sessionTitle: activeSession.title
            }, activeSession.apiSessionId);
            showToast('✓ Image added to clipboard successfully');
        }
    };

    const addMessageToClipboard = (msg: ChatMessage) => {
        if (!activeSession) return;
        // Run through the same pipeline used to render the message so links the
        // backend sends as plain markdown links (not ![]() image syntax) still
        // resolve to an actual embedded image here.
        const rendered = transformSandboxUrl(preprocessContent(msg.content));
        const embeddedImage = rendered.match(/!\[[^\]]*\]\((.*?)\)/)?.[1];
        const textOnly = rendered.replace(/!\[[^\]]*\]\([^)]*\)/g, '').trim();
        addToQuantClipboard(USER_ID, projectId, {
            text: textOnly,
            imageUrl: embeddedImage || undefined,
            sessionTitle: activeSession.title,
        }, activeSession.apiSessionId);
        showToast(textOnly ? '✓ Message added to clipboard successfully' : '✓ Image added to clipboard successfully');
    };

    const scrollToBottom = useCallback((immediate = false) => {
        if (scrollTimeoutRef.current) {
            clearTimeout(scrollTimeoutRef.current);
        }

        scrollTimeoutRef.current = window.setTimeout(() => {
            if (messagesEndRef.current) {
                messagesEndRef.current.scrollIntoView({
                    behavior: immediate ? 'auto' : 'smooth',
                    block: 'end'
                });
            }
        }, immediate ? 0 : 100);
    }, []);

    useEffect(() => {
        // Only scroll when a new message is actually added
        const currentMessageCount = activeSession?.messages.length || 0;
        if (currentMessageCount > prevMessageCountRef.current) {
            scrollToBottom(false);
        }
        prevMessageCountRef.current = currentMessageCount;
    }, [activeSession?.messages.length, scrollToBottom]);

    useEffect(() => {
        // Scroll when searching starts
        if (activeSession?.id && busySessionIds.has(activeSession.id)) {
            scrollToBottom(false);
        }
    }, [activeSession?.id, busySessionIds, scrollToBottom]);

    useEffect(() => {
        // Cleanup timeout on unmount
        return () => {
            if (scrollTimeoutRef.current) {
                clearTimeout(scrollTimeoutRef.current);
            }
        };
    }, []);

    // Fetch chat sessions from backend on mount or projectId change
    const fetchSessions = useCallback(async () => {
        try {
            const sessions = await chatApi.getUserSessions(USER_ID, 'quant', parseInt(projectId));

            // Map backend sessions to frontend ChatSession objects
            const mappedSessions: ChatSession[] = sessions.map(s => ({
                id: s.session_id,
                title: s.title,
                messages: [], // Messages will be fetched individually
                createdAt: new Date(s.created_at),
                apiSessionId: s.session_id
            }));

            setChatSessions(mappedSessions);
        } catch (err) {
            console.error('Failed to fetch quant sessions:', err);
            showToast('Failed to load quant history.');
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
        const match = chatSessions.find(s => s.apiSessionId === focusSessionId);
        if (match) {
            setActiveSession(match);
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
    const fetchMessages = useEffectEvent(async () => {
            if (activeSession && activeSession.messages.length === 0 && activeSession.apiSessionId && !activeSession.isNew) {
                try {
                    const history = await chatApi.getSessionHistory(activeSession.apiSessionId);
                    const mappedMessages: ChatMessage[] = history.messages.map((m, idx) => ({
                        id: `msg-${activeSession.id}-${idx}`,
                        role: m.role as 'user' | 'assistant',
                        content: m.content,
                        timestamp: new Date(m.timestamp)
                    }));

                    const updatedSession = { ...activeSession, messages: mappedMessages };
                    setActiveSession(updatedSession);
                    setChatSessions(prev =>
                        prev.map(s => s.id === activeSession.id ? updatedSession : s)
                    );
                } catch (err) {
                    console.error('Failed to fetch quant session history:', err);
                }
            }
    });

    useEffect(() => {
        fetchMessages();
    }, [activeSession?.id]);


    // Sync state changes to cache
    useEffect(() => {
        setQuantChatState(projectId, {
            sessions: chatSessions,
            activeSessionId: activeSession?.id || null
        });
    }, [chatSessions, activeSession?.id, projectId, setQuantChatState]);

    // Generate a unique session ID for API calls
    const generateSessionId = () => {
        const timestamp = new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14);
        const random = Math.random().toString(36).substring(2, 8);
        return `session_${timestamp}_${random}`;
    };

    // Preprocess content to extract image URLs and render them as images
    const preprocessMathContent = useCallback((text: string): string => {
        let result = text.replace(/\\\[([\s\S]*?)\\\]/g, (_m, math) => `$$${math}$$`);
        result = result.replace(/\\\(([\s\S]*?)\\\)/g, (_m, math) => `$$${math}$$`);
        result = result.replace(/(?<!\])\[\s*(\\[^[\]]+?)\s*\](?!\()/g, (_m, math) => `$$${math}$$`);
        result = result.replace(/\$\$([\s\S]*?)\$\$/g, (_m, formula) => {
            let f = formula.replace(/(\d),(\d)/g, '$1{,}$2');
            f = f.replace(/\\frac\b/g, '\\dfrac');
            return `$$${f}$$`;
        });
        return result;
    }, []);

    const preprocessContent = useCallback((content: string): string => {
        // Common image file extensions
        const imageExtensions = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'];
        const extensionPattern = imageExtensions.join('|');

        let processedContent = content;

        // Pattern 1: Convert markdown links containing image URLs to markdown images
        // Negative lookbehind (?<!!) prevents re-processing already-converted ![text](url)
        const markdownLinkWithImagePattern = new RegExp(
            `(?<!!)\\[([^\\]]+)\\]\\((https?:\\/\\/[^)]+\\.(?:${extensionPattern})(?:\\?[^)]*)?)\\)`,
            'gi'
        );

        processedContent = processedContent.replace(markdownLinkWithImagePattern, (_match, text, url) => {
            return `![${text}](${url})`;
        });

        // Pattern 2: Detect image URLs from cloudinary or similar image hosting services
        // Negative lookbehind (?<!!) prevents double-converting already-converted ![text](url)
        const cloudinaryPattern = /(?<!!)\[([^\]]+)\]\((https?:\/\/res\.cloudinary\.com\/[^)]+)\)/gi;
        processedContent = processedContent.replace(cloudinaryPattern, (_match, text, url) => {
            return `![${text}](${url})`;
        });

        // Pattern 3: Convert standalone image URLs (not in any markdown format)
        const standaloneImageUrlPattern = new RegExp(
            `(?<!\\]\\()(?<!\\!\\[[^\\]]*\\]\\()\\b(https?:\\/\\/[^\\s)]+\\.(?:${extensionPattern})(?:\\?[^\\s)]*)?)(?=\\s|$)`,
            'gi'
        );
        processedContent = processedContent.replace(standaloneImageUrlPattern, '![Image]($1)');

        // Strip stray "!!" that the LLM sometimes adds to headings or image lines
        processedContent = processedContent.replace(/!!/g, '');

        return processedContent;
    }, []);

    // Transform sandbox: sandbox:/path/to/file.png to http://localhost:8000/charts/file.png
    const transformSandboxUrl = useCallback((content: string): string => {
        // Pattern: [Download Chart](sandbox:/D:/Investment%20Analyst/investment-analyst-backend/quant/Stock_Analysis/stock_candlestick_NFLX_20260212_182102.png)
        // We want to extract the filename and convert to a markdown image
        const sandboxPattern = /\[Download Chart\]\(sandbox:.*?\/quant\/Stock_Analysis\/(.*?)\)/gi;

        return content.replace(sandboxPattern, (_match, filename) => {
            const decodedFilename = decodeURIComponent(filename);
            const serverUrl = `${API_BASE}/charts/${decodedFilename}`;
            return `![Generated Chart](${serverUrl})\n\n[Download Chart](${serverUrl})`;
        });
    }, []);

    // Memoize the ReactMarkdown components to prevent re-rendering
    const markdownComponents = useMemo<Components>(() => ({
        img: ({ src, alt }: { src?: string; alt?: string }) => (
            <div className="chat-message__image-container">
                <img
                    src={src}
                    alt={alt || 'Chart'}
                    className="chat-message__image"
                    loading="lazy"
                    title="Click to add to clipboard"
                    onClick={() => src && addImageToClipboard(src)}
                    onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.style.display = 'none';
                    }}
                />
            </div>
        ),
        table: ({ node, children, ...props }) => {
            const handleClipTable = (e: React.MouseEvent<HTMLButtonElement>) => {
                const button = e.currentTarget;
                const wrapper = button.closest('.chat-message__table-wrapper');
                const tableEl = wrapper?.querySelector('table');
                if (tableEl && activeSession) {
                    addToQuantClipboard(USER_ID, projectId, {
                        text: tableEl.innerText || '[Data Table]',
                        html: tableEl.outerHTML,
                        sessionTitle: activeSession.title
                    }, activeSession.apiSessionId);
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
        th: ({ node, children, ...props }) => <th className="chat-message__th" {...props}>{children}</th>,
        td: ({ node, children, ...props }) => <td className="chat-message__td" {...props}>{children}</td>,
        tr: ({ node, children, ...props }) => <tr className="chat-message__tr" {...props}>{children}</tr>
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }), [activeSession, projectId]);

    const startNewChat = async () => {
        const apiSessionId = generateSessionId();

        try {
            // Register session with backend portfolio
            await portfolioApi.createSession({
                portfolio_id: parseInt(projectId),
                user_id: USER_ID,
                thread_id: apiSessionId,
                agent_type: 'quant'
            });

            const newSession: ChatSession = {
                id: `session-${Date.now()}`,
                title: `${projectName} - Search ${chatSessions.length + 1}`,
                messages: [],
                createdAt: new Date(),
                apiSessionId: apiSessionId,
                isNew: true
            };

            setChatSessions(prev => [newSession, ...prev]);
            setActiveSession(newSession);
            setError(null);
        } catch (err) {
            console.error('Failed to register quant session:', err);
            // Still allow it locally
            const newSession: ChatSession = {
                id: `session-${Date.now()}`,
                title: `${projectName} - Search ${chatSessions.length + 1}`,
                messages: [],
                createdAt: new Date(),
                apiSessionId: apiSessionId,
                isNew: true
            };

            setChatSessions(prev => [newSession, ...prev]);
            setActiveSession(newSession);
            setError(null);
        }
    };

    const sendMessage = async () => {
        if (!messageInput.trim()) return;

        setError(null);

        // Auto-create session if none exists
        let session = activeSession;
        if (!session) {
            const apiSessionId = generateSessionId();
            try {
                await portfolioApi.createSession({
                    portfolio_id: parseInt(projectId),
                    user_id: USER_ID,
                    thread_id: apiSessionId,
                    agent_type: 'quant'
                });
            } catch (err) {
                console.error('Failed to register quant session (auto-create):', err);
            }

            const newSession: ChatSession = {
                id: `session-${Date.now()}`,
                title: messageInput.slice(0, 30) + (messageInput.length > 30 ? '...' : ''),
                messages: [],
                createdAt: new Date(),
                apiSessionId: apiSessionId,
                isNew: true
            };
            setChatSessions(prev => [newSession, ...prev]);
            setActiveSession(newSession);
            session = newSession;
        }

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

        const sessionId = session.id;

        setActiveSession(updatedSession);
        setChatSessions(prev =>
            prev.map(s => s.id === sessionId ? updatedSession : s)
        );
        const currentMessage = messageInput;
        setMessageInput('');
        setBusySessionIds(prev => new Set(prev).add(sessionId));

        try {
            // Find the selected Postgres integration if any
            const postgresSource = availableDataSources.find(
                ds => ds.provider === 'PostgreSQL' && selectedDataSources.includes(ds.id)
            );
            const postgresIntegrationId = postgresSource ? parseInt(postgresSource.id) : undefined;

            // Call the real Quant Agent API
            console.log('Sending query to Quant Agent API:', {
                query: currentMessage,
                user_id: USER_ID,
                portfolio_id: parseInt(projectId),
                session_id: session.apiSessionId,
                postgres_integration_id: postgresIntegrationId
            });

            const response = await quantAgentApi.chatViaQueue({
                query: currentMessage,
                user_id: USER_ID,
                portfolio_id: parseInt(projectId),
                session_id: session.apiSessionId,
                postgres_integration_id: postgresIntegrationId
            });
            await onAnalysisEnqueued?.();

            console.log('Quant Agent API response:', response);

            if (response.success) {
                const aiMessage: ChatMessage = {
                    id: `msg-${Date.now()}`,
                    role: 'assistant',
                    content: response.response,
                    timestamp: new Date(response.timestamp)
                };

                const withAiResponse: ChatSession = {
                    ...updatedSession,
                    messages: [...updatedSession.messages, aiMessage],
                    apiSessionId: response.session_id
                };

                if (response.agent_used) {
                    setAgentUsed(response.agent_used);
                }

                setChatSessions(prev =>
                    prev.map(s => s.id === sessionId ? withAiResponse : s)
                );
                setActiveSession(prev => prev && prev.id === sessionId ? withAiResponse : prev);

                if (response.agent_used) {
                    console.log(`Quant agent used: ${response.agent_used}`);
                }

                // Scroll to bottom after message is added
                setTimeout(() => scrollToBottom(false), 150);
            } else {
                setError('The AI agent returned an unsuccessful response. Please try again.');
            }
        } catch (err) {
            console.error('Error calling Quant Agent API:', err);
            setError(err instanceof Error ? err.message : 'Failed to get response from AI agent');
        } finally {
            setBusySessionIds(prev => {
                const next = new Set(prev);
                next.delete(sessionId);
                return next;
            });
        }
    };

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
            console.error('Failed to rename quant session:', err);
            showToast('Failed to rename session.');
        }
    };

    const cancelRename = () => {
        setEditingSessionId(null);
        setEditingTitle('');
    };

    const handleDeleteSession = async (sessionId: string) => {
        try {
            // Look up the actual backend session ID (apiSessionId) for this session
            const session = chatSessions.find(s => s.id === sessionId);
            const backendId = session?.apiSessionId || sessionId;
            await chatApi.deleteSession(backendId);
            setChatSessions(prev => prev.filter(s => s.id !== sessionId));
            if (activeSession?.id === sessionId) {
                setActiveSession(null);
            }
            showToast('✓ Session deleted successfully');
        } catch (err) {
            console.error('Failed to delete quant session:', err);
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
                scale: 2,
                useCORS: true,
                allowTaint: true,
                logging: false,
                backgroundColor: '#ffffff',
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

            // Draw separator line
            pdf.setDrawColor(200, 200, 200);
            pdf.line(margin, margin + 16, pageWidth - margin, margin + 16);

            // Calculate image dimensions to fit page width
            const imgWidth = contentWidth;
            const imgHeight = (canvas.height * imgWidth) / canvas.width;

            // Convert canvas to image data
            const imgData = canvas.toDataURL('image/png');

            // Starting position after header
            const headerEndY = margin + 21;
            const availableHeightFirstPage = pageHeight - headerEndY - margin;
            const availableHeightOtherPages = pageHeight - margin * 2;

            // If image fits on first page (with header)
            if (imgHeight <= availableHeightFirstPage) {
                pdf.addImage(imgData, 'PNG', margin, headerEndY, imgWidth, imgHeight);
            } else {
                // Need to split across pages
                const pixelRatio = canvas.height / imgHeight;

                let currentSourceY = 0;
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
        <div className="quantitative-research">
            {/* Left Panel - Chat History */}
            <ResizableSidebar
                collapsed={sidebarCollapsed}
                width={sidebarWidth}
                onExpand={() => setSidebarCollapsed(false)}
                onCollapse={() => setSidebarCollapsed(true)}
                resizeHandleRef={resizeHandleRef}
                onResizeMouseDown={handleResizeMouseDown}
                className="quantitative-research__sidebar"
            >
                <div className="quantitative-research__sidebar-header">
                    <h3>Quant Agent</h3>
                    <button className="quantitative-research__new-btn" onClick={startNewChat}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="12" y1="5" x2="12" y2="19" />
                            <line x1="5" y1="12" x2="19" y2="12" />
                        </svg>
                        New Chat
                    </button>
                </div>

                {/* Data Sources Selector */}
                {availableDataSources.filter(ds => ds.status === 'active').length > 0 && (
                    <div className="quantitative-research__data-sources">
                        <h4 className="quantitative-research__data-sources-title">Data Sources</h4>
                        <div className="quantitative-research__data-sources-list">
                            {availableDataSources
                                .filter(ds => ds.status === 'active')
                                .map(ds => (
                                    <label key={ds.id} className="quantitative-research__data-source-item" title={ds.description || ds.provider}>
                                        <input
                                            type="checkbox"
                                            checked={selectedDataSources.includes(ds.id)}
                                            onChange={() => handleToggleDataSource(ds.id)}
                                            disabled={ds.isDefault}
                                            className="quantitative-research__data-source-checkbox"
                                        />
                                        <span className="quantitative-research__data-source-name">
                                            {ds.name}
                                            {ds.isDefault && <span className="quantitative-research__data-source-badge">default</span>}
                                        </span>
                                    </label>
                                ))}
                        </div>
                    </div>
                )}

                {/* Chat History */}
                <div className="quantitative-research__history">
                    {chatSessions.length === 0 ? (
                        <p className="quantitative-research__empty-history">No chat history yet</p>
                    ) : (
                        chatSessions.map(session => (
                            <div
                                key={session.id}
                                className="history-item__wrapper"
                                onMouseEnter={() => setHoveredSessionId(session.id)}
                                onMouseLeave={() => setHoveredSessionId(null)}
                            >
                                <button
                                    className={`quantitative-research__history-item ${activeSession?.id === session.id ? 'quantitative-research__history-item--active' : ''}`}
                                    onClick={() => setActiveSession(session)}
                                >
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <circle cx="11" cy="11" r="8" />
                                        <line x1="21" y1="21" x2="16.65" y2="16.65" />
                                    </svg>
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
                        ))
                    )}
                </div>
            </ResizableSidebar>

            {/* Right Panel - Chat Area */}
            <div className="quantitative-research__chat">
                {!activeSession ? (
                    <EmptyState
                        tintClassName="quantitative-research__empty-icon"
                        icon={
                            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                            </svg>
                        }
                        title="Quant Agent"
                        description="I can help you run quantitative analysis, search market data, analyze financial trends, and answer research questions using live web data."
                    />
                ) : (
                    <>
                        {/* Messages */}
                        <div
                            className={`quantitative-research__messages${selectedText ? ' has-text-selection' : ''}`}
                            ref={messagesContainerRef}
                            onMouseUp={handleTextSelection}
                        >
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
                                    onClick={addSelectedToClipboard}
                                >
                                    📋 Add to Clipboard
                                </button>
                            )}
                            {activeSession?.messages.length === 0 ? (
                                <div className="quantitative-research__welcome">
                                    <p>🔍 Ready to search! Ask me anything about markets, financial data, or investment research.</p>
                                </div>
                            ) : (
                                <>

                                    {activeSession?.messages.map(msg => (
                                        <ChatMessage
                                            key={msg.id}
                                            role={msg.role}
                                            userName={userDisplayName}
                                            onClip={() => addMessageToClipboard(msg)}
                                        >
                                            {msg.role === 'assistant' ? (
                                                <ReactMarkdown components={markdownComponents} remarkPlugins={[[remarkGfm, { singleTilde: false }], [remarkMath, { singleDollarTextMath: false }]]} rehypePlugins={[rehypeKatex]}>
                                                    {preprocessMathContent(transformSandboxUrl(preprocessContent(msg.content)))}
                                                </ReactMarkdown>
                                            ) : (
                                                msg.content
                                            )}
                                            {msg.sources && (
                                                <div className="chat-message__sources">
                                                    <span className="sources-label">Sources:</span>
                                                    {msg.sources.map((source, idx) => (
                                                        <span key={idx} className="source-tag">{source}</span>
                                                    ))}
                                                </div>
                                            )}
                                        </ChatMessage>
                                    ))}
                                </>
                            )}
                            {busySessionIds.has(activeSession.id) && (
                                <ChatMessage role="assistant">
                                    <ChatMessageStatus text="Running Quant Agent..." />
                                </ChatMessage>
                            )}
                            {error && (
                                <div className="quantitative-research__error">
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <circle cx="12" cy="12" r="10" />
                                        <line x1="12" y1="8" x2="12" y2="12" />
                                        <line x1="12" y1="16" x2="12.01" y2="16" />
                                    </svg>
                                    <span>{error}</span>
                                </div>
                            )}
                            <div ref={messagesEndRef} />
                        </div>

                        {/* Input Area */}
                        <div className="quantitative-research__input-area">
                            <AutoResizingTextarea
                                className="quantitative-research__input"
                                placeholder="Search for market data, financial news..."
                                value={messageInput}
                                onChange={(e) => setMessageInput(e.target.value)}
                                onSend={sendMessage}
                            />
                            <Button
                                variant="primary"
                                onClick={sendMessage}
                                disabled={!messageInput.trim() || busySessionIds.has(activeSession.id)}
                            >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <circle cx="11" cy="11" r="8" />
                                    <line x1="21" y1="21" x2="16.65" y2="16.65" />
                                </svg>
                            </Button>
                            {activeSession && activeSession.messages.length > 0 && (
                                <Button
                                    variant="secondary"
                                    onClick={exportChatToPDF}
                                    title="Export Chat to PDF"
                                >
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                        <polyline points="7 10 12 15 17 10" />
                                        <line x1="12" y1="15" x2="12" y2="3" />
                                    </svg>
                                </Button>
                            )}
                        </div>
                    </>
                )}
            </div>

            {/* Rename Modal */}
            <RenameModal
                isOpen={!!editingSessionId}
                title="Rename Search"
                value={editingTitle}
                onChange={setEditingTitle}
                onCancel={cancelRename}
                onConfirm={confirmRename}
            />

            {/* Toast Notification */}
            {toastMessage && (
                <div className="quantitative-research__toast">
                    {toastMessage}
                </div>
            )}
        </div>
    );
};

export default QuantitativeResearch;
