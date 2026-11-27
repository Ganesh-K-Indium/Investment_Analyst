// Background analysis-run API — enqueue an agent invocation, list/inspect
// runs for a portfolio, and open an SSE stream of live status updates.

import { getAuthHeader } from './authHeader';
import { API_BASE } from './http';

const API_BASE_URL = API_BASE;

export type TaskStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
export type AgentType = 'rag' | 'quant';

export interface AnalysisTask {
    id: string;
    user_id: string;
    portfolio_id: number | null;
    agent_type: AgentType;
    task_type: 'ask' | 'compare' | 'alpha' | 'query';
    status: TaskStatus;
    progress_message: string | null;
    result_metadata: Record<string, unknown> | null;
    created_at: string;
    updated_at: string;
}

// ── Live step-by-step progress ───────────────────────────────────────────────────────────────────────────────────
// Written by the backend while a run is in flight (backend: app/utils/run_progress.py) into
// result_metadata.progress, and delivered by the same polling / SSE paths as everything else on the task.
// A run has one lane per ticker (ALPHA) or a single "main" lane (Compare / Ask). A *planned* lane lists all of
// its steps up front as "pending"; an unplanned one grows as the pipeline's nodes run.
export type StepStatus = 'pending' | 'running' | 'done' | 'failed';

export interface ProgressStep {
    id: string;                 // "read.alignment", "write.alignment", "combine", "node:retrieve", "node:retrieve#2", …
    label: string;
    status: StepStatus;
    started_at: number | null;  // epoch ms
    ended_at: number | null;
    detail: string | null;      // e.g. "12 sources"
}

export interface ProgressLane {
    id: string;
    label: string;
    planned: boolean;
    steps: ProgressStep[];
}

export interface RunProgress {
    v: number;
    kind: 'alpha' | 'compare' | 'ask';
    started_at: number;
    updated_at: number;
    lanes: ProgressLane[];
}

/** The task's live progress, or null (older runs, runs not started yet, other task kinds). */
export function getRunProgress(task: Pick<AnalysisTask, 'result_metadata'>): RunProgress | null {
    const p = (task.result_metadata as { progress?: RunProgress } | null)?.progress;
    return p && Array.isArray(p.lanes) ? p : null;
}

// Published when a chat session's deletion cascades into removing its
// AnalysisTask row(s) — lets a live-connected dashboard drop the run
// immediately instead of only on the next full refetch.
export interface TaskDeletedEvent {
    id: string;
    portfolio_id: number | null;
}

// A completed task's stashed job response (result_metadata.response) already
// carries the chat session id it produced — thread_id for RAG tasks,
// session_id for quant tasks. Reading it back is what lets "View"/notification
// clicks jump straight to the right conversation instead of just its tab.
export function extractSessionId(task: AnalysisTask): string | undefined {
    const response = (task.result_metadata as { response?: Record<string, unknown> } | null)?.response;
    if (!response) return undefined;
    const id = task.agent_type === 'quant' ? response.session_id : response.thread_id;
    return typeof id === 'string' ? id : undefined;
}

/**
 * Opens a fetch-based SSE connection and keeps it open, auto-reconnecting
 * if it ever drops — a backend restart (uvicorn --reload picking up any
 * .py change), a transient network blip, or the server closing the
 * connection all end the read loop with no error thrown. Without a retry
 * here, the caller's store would silently stop receiving updates until
 * something else (an unmount/remount) happened to open a fresh connection —
 * exactly the "have to leave and come back to see the update" symptom.
 *
 * Reconnects almost immediately (1s) after ever having connected
 * successfully; backs off exponentially (up to 15s) if it can't connect at
 * all, so a real outage doesn't hammer the server. Returns a close()
 * function that permanently stops retrying.
 */
function openEventStream(
    url: string,
    onTask: (task: AnalysisTask) => void,
    onError?: (err: unknown) => void,
    onDelete?: (event: TaskDeletedEvent) => void
): () => void {
    const controller = new AbortController();
    let stopped = false;
    let attempt = 0;

    (async () => {
        while (!stopped) {
            let connected = false;
            try {
                const response = await fetch(url, {
                    headers: { ...getAuthHeader() },
                    signal: controller.signal,
                });
                if (!response.ok || !response.body) {
                    throw new Error(`SSE stream failed: ${response.status}`);
                }
                connected = true;
                attempt = 0;

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });

                    // SSE frames are separated by a blank line.
                    const frames = buffer.split('\n\n');
                    buffer = frames.pop() ?? '';

                    for (const frame of frames) {
                        const dataLine = frame.split('\n').find((l) => l.startsWith('data: '));
                        const eventLine = frame.split('\n').find((l) => l.startsWith('event: '));
                        if (!dataLine) continue;
                        const eventName = eventLine?.replace('event: ', '');
                        if (eventName !== 'task_update' && eventName !== 'task_deleted') continue;
                        try {
                            const parsed = JSON.parse(dataLine.replace('data: ', ''));
                            if (eventName === 'task_deleted') onDelete?.(parsed);
                            else onTask(parsed);
                        } catch {
                            // ignore malformed event
                        }
                    }
                }
                // Stream ended (server closed it, e.g. a backend restart) — fall
                // through to the retry delay below instead of returning.
            } catch (err) {
                if (controller.signal.aborted) return;
                if (onError) onError(err);
            }

            if (stopped || controller.signal.aborted) return;
            attempt += 1;
            const delayMs = connected ? 1000 : Math.min(1000 * 2 ** (attempt - 1), 15000);
            await new Promise((r) => setTimeout(r, delayMs));
        }
    })();

    return () => {
        stopped = true;
        controller.abort();
    };
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${API_BASE_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        const detail = await response.text().catch(() => '');
        throw new Error(`Request to ${path} failed: ${response.status} ${detail}`);
    }
    return response.json();
}

export const analysisTasksApi = {
    async enqueueAsk(payload: {
        query: string; thread_id: string; scheduled_at?: string; framework?: string; document_ids?: string[];
        clarification?: string; settings_override?: Record<string, Record<string, unknown>>;
    }): Promise<AnalysisTask> {
        return postJson('/analysis-tasks/rag/ask', payload);
    },

    async enqueueCompare(payload: {
        company1: string; company2: string; company3?: string;
        user_id: string; thread_id?: string; year?: number; scheduled_at?: string;
    }): Promise<AnalysisTask> {
        return postJson('/analysis-tasks/rag/compare', payload);
    },

    async enqueueAlpha(payload: {
        tickers: string[]; user_id: string; thread_id: string; scheduled_at?: string;
    }): Promise<AnalysisTask> {
        return postJson('/analysis-tasks/rag/alpha', payload);
    },

    async enqueueQuantQuery(payload: {
        query: string; portfolio_id?: number; user_id: string;
        session_id?: string; postgres_integration_id?: number; scheduled_at?: string;
    }): Promise<AnalysisTask> {
        return postJson('/analysis-tasks/quant/query', payload);
    },

    async list(portfolioId: number): Promise<AnalysisTask[]> {
        const response = await fetch(
            `${API_BASE_URL}/analysis-tasks?portfolio_id=${portfolioId}`,
            { headers: { ...getAuthHeader() } }
        );
        if (!response.ok) throw new Error(`Failed to list analysis tasks: ${response.status}`);
        return response.json();
    },

    async get(taskId: string): Promise<AnalysisTask> {
        const response = await fetch(`${API_BASE_URL}/analysis-tasks/${taskId}`, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) throw new Error(`Failed to fetch analysis task: ${response.status}`);
        return response.json();
    },

    /**
     * Opens an SSE connection for live task updates on one portfolio.
     * Returns a close() function — call it on unmount/portfolio change.
     *
     * Uses fetch + a manual reader instead of EventSource: EventSource can't
     * send an Authorization header, and putting the bearer token in the URL
     * instead (the usual workaround) would leak it into server/proxy logs
     * and browser history.
     */
    streamPortfolio(
        portfolioId: number,
        onTask: (task: AnalysisTask) => void,
        onError?: (err: unknown) => void,
        onDelete?: (event: TaskDeletedEvent) => void
    ): () => void {
        return openEventStream(`${API_BASE_URL}/analysis-tasks/stream/${portfolioId}`, onTask, onError, onDelete);
    },

    /**
     * Opens an SSE connection for live task updates across ALL of a user's
     * portfolios — backs the cross-portfolio dashboard. Same fetch+reader
     * approach as streamPortfolio, just pointed at the user-scoped channel.
     */
    streamUser(
        userId: string,
        onTask: (task: AnalysisTask) => void,
        onError?: (err: unknown) => void,
        onDelete?: (event: TaskDeletedEvent) => void
    ): () => void {
        return openEventStream(`${API_BASE_URL}/analysis-tasks/stream/user/${userId}`, onTask, onError, onDelete);
    },

    /**
     * Polls a task until it reaches a terminal state. Used by the chat UIs
     * (interactive lane) to keep their current "await and render" feel while
     * actually running through the queue — the run still shows up in the
     * portfolio's dashboard/history, and survives the tab being closed,
     * unlike the old direct fetch-and-wait.
     *
     * Interactive-lane jobs have reserved worker concurrency, so this
     * usually resolves in well under a second of extra latency over the old
     * direct call; the timeout below is a safety net, not the expected case.
     */
    async pollUntilDone(
        taskId: string,
        { intervalMs = 500, timeoutMs = 5 * 60 * 1000, onProgress }:
            { intervalMs?: number; timeoutMs?: number; onProgress?: (task: AnalysisTask) => void } = {}
    ): Promise<AnalysisTask> {
        const start = Date.now();
        while (Date.now() - start < timeoutMs) {
            const task = await analysisTasksApi.get(taskId);
            if (task.status === 'COMPLETED' || task.status === 'FAILED') return task;
            onProgress?.(task);
            await new Promise((r) => setTimeout(r, intervalMs));
        }
        throw new Error('Analysis run timed out waiting for a result.');
    },
};

export default analysisTasksApi;
