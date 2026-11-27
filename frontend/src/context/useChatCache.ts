/** Chat state kept per project across tab switches. The provider is in ChatCacheContext.tsx; this file holds what
 * components import. */
import { createContext, useContext } from 'react';

export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    citations?: { file: string; pages: number[] }[];
    webSearched?: boolean;
    webLinks?: string[];
    chartUrl?: string | null;
    sources?: string[];
}

export interface ChatSession {
    id: string;
    title: string;
    files?: string[];
    messages: ChatMessage[];
    createdAt: Date;
    threadId?: string;
    apiSessionId?: string;
}

// State for a single research type
export interface ResearchChatState {
    sessions: ChatSession[];
    activeSessionId: string | null;
}

// State for a project - holds both Secondary and Quant research chats
export interface ProjectChatCache {
    secondary: ResearchChatState;
    quant: ResearchChatState;
}

export interface ChatCacheContextType {
    // Get cached state for a project
    getSecondaryChatState: (projectId: string) => ResearchChatState;
    getQuantChatState: (projectId: string) => ResearchChatState;

    // Update cached state for a project
    setSecondaryChatState: (projectId: string, state: ResearchChatState) => void;
    setQuantChatState: (projectId: string, state: ResearchChatState) => void;

    // Clear all cache for a project (called when returning to home)
    clearProjectCache: (projectId: string) => void;
}

export const ChatCacheContext = createContext<ChatCacheContextType | undefined>(undefined);

export const useChatCache = (): ChatCacheContextType => {
    const context = useContext(ChatCacheContext);
    if (!context) {
        throw new Error('useChatCache must be used within a ChatCacheProvider');
    }
    return context;
};
