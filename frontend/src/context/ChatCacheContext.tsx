import React, { useState, useCallback, useRef, type ReactNode } from 'react';
import { ChatCacheContext, type ResearchChatState, type ProjectChatCache } from './useChatCache';

interface ChatCacheProviderProps {
    children: ReactNode;
}

const emptyResearchState: ResearchChatState = {
    sessions: [],
    activeSessionId: null
};

const createEmptyProjectCache = (): ProjectChatCache => ({
    secondary: { sessions: [], activeSessionId: null },
    quant: { sessions: [], activeSessionId: null }
});

export const ChatCacheProvider: React.FC<ChatCacheProviderProps> = ({ children }) => {
    // Use ref to store cache to avoid infinite loops in getters
    const cacheRef = useRef<Record<string, ProjectChatCache>>({});
    // State just for triggering updates when cache changes (not used in getters)
    const [, forceUpdate] = useState({});

    const getSecondaryChatState = useCallback((projectId: string): ResearchChatState => {
        const projectCache = cacheRef.current[projectId];
        return projectCache?.secondary || emptyResearchState;
    }, []);

    const getQuantChatState = useCallback((projectId: string): ResearchChatState => {
        const projectCache = cacheRef.current[projectId];
        return projectCache?.quant || emptyResearchState;
    }, []);

    const setSecondaryChatState = useCallback((projectId: string, state: ResearchChatState) => {
        const existingCache = cacheRef.current[projectId] || createEmptyProjectCache();
        cacheRef.current = {
            ...cacheRef.current,
            [projectId]: {
                ...existingCache,
                secondary: state
            }
        };
        // Don't trigger re-render - the component already has the state locally
    }, []);

    const setQuantChatState = useCallback((projectId: string, state: ResearchChatState) => {
        const existingCache = cacheRef.current[projectId] || createEmptyProjectCache();
        cacheRef.current = {
            ...cacheRef.current,
            [projectId]: {
                ...existingCache,
                quant: state
            }
        };
        // Don't trigger re-render - the component already has the state locally
    }, []);

    const clearProjectCache = useCallback((projectId: string) => {
        const newCache = { ...cacheRef.current };
        delete newCache[projectId];
        cacheRef.current = newCache;
        forceUpdate({}); // Trigger update after clear
    }, []);

    return (
        <ChatCacheContext.Provider value={{
            getSecondaryChatState,
            getQuantChatState,
            setSecondaryChatState,
            setQuantChatState,
            clearProjectCache
        }}>
            {children}
        </ChatCacheContext.Provider>
    );
};
