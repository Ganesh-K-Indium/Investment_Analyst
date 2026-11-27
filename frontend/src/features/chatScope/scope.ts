// What a chat question may use, and how that becomes request fields.

export interface ChatScope {
    sources: string[] | null;          // null = the user's saved choice
    framework: string;                 // '' = standard analysis
    documentIds: string[];             // [] = all the user's documents
}

export const DEFAULT_SCOPE: ChatScope = { sources: null, framework: '', documentIds: [] };

export const SOURCE_LABEL: Record<string, string> = { filings: 'SEC filings', uploads: 'My documents', notes: 'My notes', web: 'Web', macro: 'Macro data' };

/** The per-question overrides the request carries: only what differs from the saved settings. */
export function scopeToRequest(scope: ChatScope, saved: string[]): { settings_override?: Record<string, Record<string, unknown>>; framework?: string; document_ids?: string[] } {
    const out: { settings_override?: Record<string, Record<string, unknown>>; framework?: string; document_ids?: string[] } = {};
    if (scope.sources && JSON.stringify([...scope.sources].sort()) !== JSON.stringify([...saved].sort())) out.settings_override = { retrieval: { sources: scope.sources } };
    if (scope.framework) out.framework = scope.framework;
    if (scope.documentIds.length) out.document_ids = scope.documentIds;
    return out;
}
