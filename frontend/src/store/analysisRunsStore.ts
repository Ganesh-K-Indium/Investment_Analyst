import { create } from 'zustand';
import { analysisTasksApi, type AnalysisTask, type TaskDeletedEvent } from '../services/analysisTasksApi';

interface AnalysisRunsState {
    // Runs keyed by portfolio id, each a map of task id -> task.
    runsByPortfolio: Record<number, Record<string, AnalysisTask>>;
    isLoading: Record<number, boolean>;

    // These build a NEW array/number on every call. Safe for one-off
    // imperative reads (e.g. inside an event handler via getState()), but
    // NEVER use them as a React selector — `useStore(s => s.runsFor(id))`
    // returns a different reference every render, which React 18's
    // useSyncExternalStore treats as "the store changed," causing an
    // infinite re-render loop. Select the raw `runsByPortfolio[id]` map in
    // components instead and derive with useMemo (see features/analysis/PortfolioPulse.tsx).
    runsFor: (portfolioId: number) => AnalysisTask[];

    /** One-shot fetch of current runs for a portfolio (no stream — see useAnalysisStream). */
    loadInitial: (portfolioId: number) => Promise<void>;
    /** Merge one task update, from either an enqueue response or an SSE event. */
    upsertTask: (task: AnalysisTask) => void;
    /**
     * Seed the store with a batch of tasks already fetched via REST (e.g. the
     * cross-portfolio dashboard's initial load), without touching `isLoading`.
     * Existing entries for the same task id are overwritten — safe to call
     * with a snapshot that's already slightly stale, since any live SSE
     * event for that task arrives after this and simply overwrites it again.
     */
    hydrateTasks: (tasks: AnalysisTask[]) => void;
    /** Drop one task from the store — from a "task_deleted" SSE event (a chat
     * session delete cascaded into removing its AnalysisTask row). */
    removeTask: (event: TaskDeletedEvent) => void;
    /** Remove all tasks for a deleted portfolio. */
    removePortfolioTasks: (portfolioId: number) => void;
    /** Clear all tasks — called on logout to prevent data leakage to next user. */
    clearAll: () => void;
}

export const useAnalysisRunsStore = create<AnalysisRunsState>((set, get) => ({
    runsByPortfolio: {},
    isLoading: {},

    runsFor: (portfolioId) => {
        const runs = get().runsByPortfolio[portfolioId] ?? {};
        return Object.values(runs).sort((a, b) => b.created_at.localeCompare(a.created_at));
    },

    upsertTask: (task) => {
        if (task.portfolio_id == null) return;
        set((state) => ({
            runsByPortfolio: {
                ...state.runsByPortfolio,
                [task.portfolio_id as number]: {
                    ...state.runsByPortfolio[task.portfolio_id as number],
                    [task.id]: task,
                },
            },
        }));
    },

    removeTask: ({ id, portfolio_id }) => {
        if (portfolio_id == null) return;
        set((state) => {
            const runs = state.runsByPortfolio[portfolio_id];
            if (!runs || !(id in runs)) return state;
            const nextRuns = { ...runs };
            delete nextRuns[id];
            return { runsByPortfolio: { ...state.runsByPortfolio, [portfolio_id]: nextRuns } };
        });
    },

    hydrateTasks: (tasks) => {
        const byPortfolio: Record<number, Record<string, AnalysisTask>> = {};
        for (const t of tasks) {
            if (t.portfolio_id == null) continue;
            if (!byPortfolio[t.portfolio_id]) byPortfolio[t.portfolio_id] = {};
            byPortfolio[t.portfolio_id][t.id] = t;
        }
        set((state) => {
            const merged: Record<number, Record<string, AnalysisTask>> = { ...state.runsByPortfolio };
            for (const [pid, tasksById] of Object.entries(byPortfolio)) {
                const pidNum = Number(pid);
                merged[pidNum] = { ...merged[pidNum], ...tasksById };
            }
            return { runsByPortfolio: merged };
        });
    },

    loadInitial: async (portfolioId) => {
        set((s) => ({ isLoading: { ...s.isLoading, [portfolioId]: true } }));
        try {
            const tasks = await analysisTasksApi.list(portfolioId);
            const runs: Record<string, AnalysisTask> = {};
            for (const t of tasks) runs[t.id] = t;
            set((s) => ({
                runsByPortfolio: { ...s.runsByPortfolio, [portfolioId]: runs },
                isLoading: { ...s.isLoading, [portfolioId]: false },
            }));
        } catch (err) {
            console.error('Failed to load analysis tasks:', err);
            set((s) => ({ isLoading: { ...s.isLoading, [portfolioId]: false } }));
        }
    },

    removePortfolioTasks: (portfolioId) => {
        set((state) => {
            const nextRunsByPortfolio = { ...state.runsByPortfolio };
            delete nextRunsByPortfolio[portfolioId];
            return { runsByPortfolio: nextRunsByPortfolio };
        });
    },

    clearAll: () => {
        set({ runsByPortfolio: {}, isLoading: {} });
    },
}));

/**
 * Opens the live SSE stream for one portfolio and keeps the store's
 * `runsByPortfolio` updated as events arrive. Returns a close() function.
 *
 * Deliberately a plain function (not a store action) so its lifecycle is
 * owned by whichever component's useEffect calls it — each call gets back
 * its OWN close function tied to its OWN stream. The previous design stored
 * one shared `closeStream` in Zustand state; React 18 StrictMode
 * double-invokes effects in dev, so the second connect() call overwrote the
 * first's reference before its request had even resolved, leaking a
 * dangling stream and leaving state inconsistent. Scoping this to the
 * component's own effect closure (see features/analysis/PortfolioPulse.tsx) means the
 * cleanup function always closes exactly the stream it opened, no shared
 * mutable state involved.
 */
export function openAnalysisStream(portfolioId: number): () => void {
    return analysisTasksApi.streamPortfolio(
        portfolioId,
        (task) => useAnalysisRunsStore.getState().upsertTask(task),
        (err) => console.warn('Analysis run stream error:', err),
        (event) => useAnalysisRunsStore.getState().removeTask(event)
    );
}

/**
 * Same idea as openAnalysisStream, but subscribed to the user-scoped
 * channel so one connection covers every portfolio at once — used by the
 * cross-portfolio dashboard instead of opening N per-portfolio streams.
 * Updates land in the same `runsByPortfolio` map (upsertTask already keys
 * by task.portfolio_id), so `runsFor(portfolioId)` stays a valid read for
 * any portfolio card on the page.
 */
export function openUserAnalysisStream(userId: string): () => void {
    return analysisTasksApi.streamUser(
        userId,
        (task) => useAnalysisRunsStore.getState().upsertTask(task),
        (err) => console.warn('Cross-portfolio analysis run stream error:', err),
        (event) => useAnalysisRunsStore.getState().removeTask(event)
    );
}
