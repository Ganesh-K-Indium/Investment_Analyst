import type { ProgressLane, ProgressStep, RunProgress } from '../../../services/analysisTasksApi';

/** The five ALPHA dimensions, in the order the backend works through them. */
export const ALPHA_DIMS = [
    { key: 'alignment', letter: 'A', name: 'Alignment with Stakeholders' },
    { key: 'liquidity', letter: 'L', name: 'Liquidity & Macro Environment' },
    { key: 'performance', letter: 'P', name: 'Performance & Fundamentals' },
    { key: 'horizon', letter: 'H', name: 'Horizon & Competitive Moat' },
    { key: 'action', letter: 'A', name: 'Action & Timing' },
] as const;

/**
 * What each step is really doing, in plain words — shown under the checklist while it runs, so a long run reads as
 * work being done rather than a spinner. These describe the backend's actual behaviour (rag/graph/nodes.py).
 */
export const STEP_HELP: Record<string, string> = {
    'read.alignment': 'Pulling SEC Form 4 insider trades, plus governance and management-discussion passages from the filings.',
    'read.liquidity': 'Checking macro and rate data from government sources against the company’s own risk factors.',
    'read.performance': 'Comparing the latest annual filing with the two years before it: revenue, margins, cash flow.',
    'read.horizon': 'Researching moat, peers and market share, alongside what the company says about itself.',
    'read.action': 'Fetching RSI, the 200-day average, P/E and EBITDA from trusted market sources.',
    'write.alignment': 'Weighing insider activity and management tone to judge whether leadership is aligned with shareholders.',
    'write.liquidity': 'Deciding whether the macro environment is a tailwind or a headwind for this company.',
    'write.performance': 'Judging earnings quality: is the growth organic, and are there red flags in the numbers?',
    'write.horizon': 'Separating a good trade from a durable investment by testing the company’s competitive advantage.',
    'write.action': 'Turning the technical signals into entry zones and timing.',
    combine: 'Merging the five assessments into one report.',
    // graph nodes (Compare / Ask)
    'node:route_query': 'Working out what kind of question this is.',
    'node:clarify': 'Making sure the companies and period are unambiguous.',
    'node:preprocess': 'Breaking the question into targeted searches.',
    'node:retrieve': 'Looking through the indexed filings for the passages that answer this.',
    'node:grade_documents': 'Judging whether the passages found actually support an answer.',
    'node:rewrite_query': 'The first search was thin, so searching again with a sharper query.',
    'node:web_search': 'Looking beyond the filings for current information.',
    'node:integrate_web_search': 'Folding the web results in with the filings.',
    'node:generate': 'Writing the answer from the evidence.',
    'node:verify_answer': 'Checking every figure and unit against the pages it cites.',
    'node:generate_chart': 'Building the comparison chart.',
    'node:show_result': 'Putting the finished answer together.',
};

const base = (id: string) => id.split('#', 1)[0];

export function helpFor(step: ProgressStep | null): string | null {
    return step ? STEP_HELP[base(step.id)] ?? null : null;
}

/** The latest record for each step id, ignoring the "#n" suffix of re-entered steps. */
export function latestByBase(lane: ProgressLane): Map<string, ProgressStep> {
    const out = new Map<string, ProgressStep>();
    for (const s of lane.steps) out.set(base(s.id), s);
    return out;
}

/** reading → gathered (sources read, waiting for its turn to be analysed) → analysing → done */
export type DimPhase = 'queued' | 'reading' | 'gathered' | 'analysing' | 'done' | 'failed';

export interface DimState {
    key: string;
    letter: string;
    name: string;
    phase: DimPhase;
    /** 0..1 — how far through this dimension's two stages (read, then analyse). */
    fraction: number;
    detail: string | null;
    /** Time the dimension took (read + analyse), once finished. */
    durationMs: number | null;
}

/** Fraction credited per stage: pending 0, running half-way through its half, done its full half. */
const stageFraction = (s: ProgressStep | undefined) => (!s || s.status === 'pending' ? 0 : s.status === 'running' ? 0.5 : 1);

export function dimensionStates(lane: ProgressLane | null): DimState[] {
    const steps = lane ? latestByBase(lane) : new Map<string, ProgressStep>();
    return ALPHA_DIMS.map(d => {
        const read = steps.get(`read.${d.key}`);
        const write = steps.get(`write.${d.key}`);
        const failed = read?.status === 'failed' || write?.status === 'failed';
        let phase: DimPhase = 'queued';
        if (failed) phase = 'failed';
        else if (write?.status === 'done') phase = 'done';
        else if (write?.status === 'running') phase = 'analysing';
        else if (read?.status === 'running') phase = 'reading';
        else if (read?.status === 'done') phase = 'gathered';
        const fraction = (stageFraction(read) + stageFraction(write)) / 2;
        const took = (a?: ProgressStep) => (a?.started_at && a.ended_at ? a.ended_at - a.started_at : null);
        const total = phase === 'done' && took(read) != null && took(write) != null ? (took(read) as number) + (took(write) as number) : null;
        return {
            key: d.key, letter: d.letter, name: d.name, phase, fraction,
            detail: read?.detail ?? null,
            durationMs: total,
        };
    });
}

/** Share of a lane's steps that have finished. Only meaningful for planned lanes. */
export function laneFraction(lane: ProgressLane | null): number {
    if (!lane || lane.steps.length === 0) return 0;
    const done = lane.steps.filter(s => s.status === 'done' || s.status === 'failed').length;
    return done / lane.steps.length;
}

/** The step happening right now (the most recently started one that is still running). */
export function activeStep(lane: ProgressLane | null): ProgressStep | null {
    if (!lane) return null;
    let best: ProgressStep | null = null;
    for (const s of lane.steps) {
        if (s.status === 'running' && (!best || (s.started_at ?? 0) >= (best.started_at ?? 0))) best = s;
    }
    return best;
}

/** Which lane to show by default: the one working right now, else the first unfinished, else the first. */
export function defaultLane(progress: RunProgress | null): ProgressLane | null {
    if (!progress || progress.lanes.length === 0) return null;
    return (
        progress.lanes.find(l => l.steps.some(s => s.status === 'running')) ??
        progress.lanes.find(l => l.steps.some(s => s.status === 'pending')) ??
        progress.lanes[0]
    );
}

export function formatClock(ms: number): string {
    const total = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function formatDuration(ms: number): string {
    const s = ms / 1000;
    if (s < 1) return '<1s';
    if (s < 60) return `${Math.round(s)}s`;
    return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
}
