import { useClipboardStore } from '../store/clipboardStore';

interface ClipboardItemInput {
    text: string;
    html?: string;
    imageUrl?: string;
    sessionTitle: string;
}

function parsePortfolioId(projectId: string): number | undefined {
    const n = parseInt(projectId, 10);
    return isNaN(n) ? undefined : n;
}

/** Adds an item to the shared clipboard store, lazily (re-)initializing it for the given user/portfolio first. */
export function useClipboardActions() {
    const store = useClipboardStore();

    const ensureInit = async (userId: string, portfolioId: number | undefined) => {
        if (!store.userId || store.portfolioId !== (portfolioId ?? null)) {
            await store.init(userId, portfolioId);
        }
    };

    const addToSecondaryClipboard = async (userId: string, projectId: string, item: ClipboardItemInput, sessionId?: string) => {
        const portfolioId = parsePortfolioId(projectId);
        await ensureInit(userId, portfolioId);
        await store.addItem({
            user_id: userId,
            portfolio_id: portfolioId,
            item_type: item.imageUrl ? 'image' : 'text',
            content: item.text || undefined,
            html: item.html,
            image_url: item.imageUrl,
            source: 'rag',
            session_id: sessionId,
            label: item.sessionTitle || 'Secondary Research',
        });
    };

    const addToQuantClipboard = async (userId: string, projectId: string, item: ClipboardItemInput, sessionId?: string) => {
        const portfolioId = parsePortfolioId(projectId);
        await ensureInit(userId, portfolioId);
        await store.addItem({
            user_id: userId,
            portfolio_id: portfolioId,
            item_type: item.imageUrl ? 'image' : 'text',
            content: item.text || undefined,
            html: item.html,
            image_url: item.imageUrl,
            source: 'quant',
            session_id: sessionId,
            label: item.sessionTitle || 'Quant Research',
        });
    };

    return { addToSecondaryClipboard, addToQuantClipboard };
}
