// Chat History API Service

import { getAuthHeader } from './authHeader';
import type { Verification, UsageInfo } from './secondaryAgentApi';
import type { RetrievedDoc } from '../features/documents/citations';
import { API_BASE } from './http';

const CHAT_BASE_URL = `${API_BASE}/chats`;

/** What the backend saves with an assistant message (keys depend on the agent that answered). */
export interface ChatMessageMetadata {
    documents?: RetrievedDoc[];
    web_searched?: boolean;
    chart_url?: string | null;
    verification?: Verification;
    usage?: UsageInfo;
    [key: string]: unknown;
}

/** What the backend saves with a chat session: `type` says which kind of run it was. */
export interface ChatSessionMetadata {
    type?: string;               // 'ask' | 'compare' | 'alpha' | ...
    portfolio_name?: string;
    companies?: string[];
    tickers?: string[];
    year?: number;               // compare runs: the year compared (backend CompareInput.year)
    [key: string]: unknown;
}

export interface ChatMessageResponse {
    role: string;
    content: string;
    metadata?: ChatMessageMetadata;
    timestamp: string;
}

export interface ChatSessionResponse {
    session_id: string;
    user_id: string;
    agent_type: string;
    portfolio_id?: number;
    title: string;
    is_active: boolean;
    message_count: number;
    created_at: string;
    last_message_at?: string;
    session_metadata?: ChatSessionMetadata | null;
}

export interface CreateChatSessionRequest {
    session_id: string;
    user_id: string;
    agent_type: 'rag' | 'quant';
    portfolio_id?: number;
    title?: string;
    session_metadata?: ChatSessionMetadata;
}

export interface ChatHistoryResponse {
    session_id: string;
    user_id: string;
    agent_type: string;
    portfolio_id?: number;
    title: string;
    message_count: number;
    messages: ChatMessageResponse[];
}

export interface ChatSummaryResponse {
    session_id: string;
    summary: string;
    summary_updated_at: string;
    message_count: number;
}

export interface SummaryItem {
    session_id: string;
    title: string;
    summary: string;
    summary_updated_at: string | null;
    message_count: number;
    created_at: string;
    last_message_at: string | null;
}

export interface SummariesByAgentResponse {
    rag: SummaryItem[];
    quant: SummaryItem[];
}

export interface ConsolidatedSummaryResponse {
    session_ids: string[];
    detected_type: string;
    consolidated_summary: string;
    sessions_included: number;
    generated_at: string;
}

export const chatApi = {
    /**
     * Get all chat sessions for a user, optionally filtered by agent type or portfolio
     */
    async getUserSessions(
        userId: string,
        agentType?: 'rag' | 'quant',
        portfolioId?: number
    ): Promise<ChatSessionResponse[]> {
        let url = `${CHAT_BASE_URL}/user/${userId}/sessions`;
        const params = new URLSearchParams();
        if (agentType) params.append('agent_type', agentType);
        if (portfolioId) params.append('portfolio_id', portfolioId.toString());

        const queryString = params.toString();
        if (queryString) url += `?${queryString}`;

        const response = await fetch(url, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to fetch chat sessions: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Get complete chat history for a specific session
     */
    async getSessionHistory(sessionId: string): Promise<ChatHistoryResponse> {
        const response = await fetch(`${CHAT_BASE_URL}/session/${sessionId}`, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to fetch chat history: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Update the title of a chat session
     */
    async updateSessionTitle(sessionId: string, title: string): Promise<unknown> {
        const response = await fetch(`${CHAT_BASE_URL}/session/${sessionId}/title`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({ title })
        });
        if (!response.ok) {
            throw new Error(`Failed to update session title: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Permanently delete a chat session
     */
    async deleteSession(sessionId: string): Promise<unknown> {
        const response = await fetch(`${CHAT_BASE_URL}/session/${sessionId}`, {
            method: 'DELETE',
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to delete session: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Get statistics for a user's chat history
     */
    async getUserStats(userId: string): Promise<Record<string, unknown>> {
        const response = await fetch(`${CHAT_BASE_URL}/user/${userId}/stats`, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to fetch user stats: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Get cached summary for a session. Returns null if no summary exists (404).
     */
    async getSessionSummary(sessionId: string): Promise<ChatSummaryResponse | null> {
        const response = await fetch(`${CHAT_BASE_URL}/session/${sessionId}/summary`, {
            headers: { ...getAuthHeader() },
        });
        if (response.status === 404) {
            return null;
        }
        if (!response.ok) {
            throw new Error(`Failed to fetch session summary: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Generate an LLM-powered summary for a chat session.
     * Returns the summary text string.
     */
    async generateSessionSummary(
        sessionId: string,
        options?: { max_messages?: number; llm_model?: string }
    ): Promise<string> {
        const response = await fetch(`${CHAT_BASE_URL}/session/${sessionId}/summary`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({
                max_messages: options?.max_messages ?? 50,
                llm_model: options?.llm_model ?? 'gpt-4o-mini'
            })
        });
        if (!response.ok) {
            throw new Error(`Failed to generate summary: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Get all cached summaries for a user grouped by agent type (rag/quant).
     */
    async getUserSummaries(userId: string): Promise<SummariesByAgentResponse> {
        const response = await fetch(`${CHAT_BASE_URL}/user/${userId}/summaries`, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to fetch user summaries: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Register a new chat session in the backend before any messages are sent.
     * This uses the new POST /chats/session endpoint which supports session_metadata.
     */
    async createSession(data: CreateChatSessionRequest): Promise<{
        session_id: string;
        user_id: string;
        agent_type: string;
        portfolio_id?: number;
        title: string;
        created_at: string;
    }> {
        const response = await fetch(`${CHAT_BASE_URL}/session`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify(data)
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Failed to create chat session: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Delete the summary from a single session (or a consolidated summary row).
     */
    async deleteSessionSummary(sessionId: string): Promise<void> {
        const response = await fetch(`${CHAT_BASE_URL}/session/${sessionId}/summary`, {
            method: 'DELETE',
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to delete summary: ${response.statusText}`);
        }
    },

    /**
     * Clear all summaries for a user.
     */
    async clearAllUserSummaries(userId: string): Promise<void> {
        const response = await fetch(`${CHAT_BASE_URL}/user/${userId}/summaries`, {
            method: 'DELETE',
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to clear summaries: ${response.statusText}`);
        }
    },

    /**
     * Generate a consolidated summary across multiple chat sessions.
     */
    async generateConsolidatedSummary(
        sessionIds: string[],
        options?: { max_messages_per_session?: number; llm_model?: string }
    ): Promise<ConsolidatedSummaryResponse> {
        const response = await fetch(`${CHAT_BASE_URL}/sessions/consolidated-summary`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({
                session_ids: sessionIds,
                max_messages_per_session: options?.max_messages_per_session ?? 30,
                llm_model: options?.llm_model ?? 'gpt-4o-mini'
            })
        });
        if (!response.ok) {
            throw new Error(`Failed to generate consolidated summary: ${response.statusText}`);
        }
        return response.json();
    }
};
