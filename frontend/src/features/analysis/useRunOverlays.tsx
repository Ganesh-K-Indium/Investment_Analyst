import { useCallback, useState } from 'react';
import type { AnalysisTask } from '../../services/analysisTasksApi';
import { RunDetail } from './RunDetail';
import { RunCompare } from './RunCompare';
import { StanceTimeline } from './StanceTimeline';

interface Options {
    portfolioId?: number;
    portfolioNames?: Record<number, string>;
    onOpenConversation: (task: AnalysisTask) => void;
    onChanged?: () => void;
}

type Overlay =
    | { kind: 'run'; taskId: string }
    | { kind: 'compare'; ticker: string; a?: string; b?: string }
    | { kind: 'timeline'; ticker: string }
    | null;

/** The three side panels the dashboards open (run detail, compare, thesis timeline), one at a time. */
export function useRunOverlays({ portfolioId, portfolioNames, onOpenConversation, onChanged }: Options) {
    const [overlay, setOverlay] = useState<Overlay>(null);
    const [runPortfolio, setRunPortfolio] = useState<number | null>(null);

    const openRun = useCallback((taskId: string, portfolio?: number | null) => { setRunPortfolio(portfolio ?? null); setOverlay({ kind: 'run', taskId }); }, []);
    const openCompare = useCallback((ticker: string, a?: string, b?: string) => setOverlay({ kind: 'compare', ticker, a, b }), []);
    const openTimeline = useCallback((ticker: string) => setOverlay({ kind: 'timeline', ticker }), []);
    const close = useCallback(() => setOverlay(null), []);

    const element = (
        <>
            {overlay?.kind === 'run' && (
                <RunDetail
                    key={overlay.taskId}
                    taskId={overlay.taskId}
                    portfolioName={portfolioNames && runPortfolio != null ? portfolioNames[runPortfolio] : undefined}
                    onClose={close}
                    onOpenConversation={(t) => { close(); onOpenConversation(t); }}
                    onCompare={(ticker, taskId) => openCompare(ticker, undefined, taskId)}
                    onOpenTimeline={openTimeline}
                    onChanged={onChanged}
                />
            )}
            {overlay?.kind === 'compare' && (
                <RunCompare key={`${overlay.ticker}:${overlay.a ?? ''}:${overlay.b ?? ''}`} ticker={overlay.ticker} a={overlay.a} b={overlay.b} portfolioId={portfolioId} onClose={close} onOpenRun={(id) => openRun(id)} />
            )}
            {overlay?.kind === 'timeline' && (
                <StanceTimeline key={overlay.ticker} ticker={overlay.ticker} portfolioId={portfolioId} onClose={close} onOpenRun={(id) => openRun(id)}
                    onCompare={(t, a, b) => openCompare(t, a, b)} />
            )}
        </>
    );

    return { openRun, openCompare, openTimeline, close, element };
}

export default useRunOverlays;
