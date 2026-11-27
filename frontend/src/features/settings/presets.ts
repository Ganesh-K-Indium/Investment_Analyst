import type { Settings } from '../../services/settingsApi';

/** `$strong` and `$light` stand for the default provider's writing model and its smaller model; they are filled in when a preset is applied. */
export interface Preset {
    id: string;
    name: string;
    tagline: string;
    detail: string;
    values: Settings;
}

/** One-click starting points. They only fill the form: nothing changes until you save. All models stay in the gpt-4 family. */
export const PRESETS: Preset[] = [
    {
        id: 'lean', name: 'Lean', tagline: 'Fast and inexpensive', detail: 'Smaller writing model, concise answers, one search pass, local reading of scans.',
        values: {
            models: { generator_model: '$light', analysis_model: '$light', grader_model: '$light' },
            retrieval: { top_k: 8, rerank_top_n: 5, corrective_retries: 0 },
            answers: { style: 'concise', verify_mode: 'warn' },
            documents: { reading_strategy: 'economy' },
        },
    },
    {
        id: 'balanced', name: 'Balanced', tagline: 'The default', detail: 'Strong writing model, re-ranking, one corrective retry, doubtful scan text checked before use.',
        values: {
            models: { generator_model: '$strong', analysis_model: '$strong', grader_model: '$strong' },
            retrieval: { top_k: 12, rerank_top_n: 8, corrective_retries: 1 },
            answers: { style: 'standard', verify_mode: 'warn' },
            documents: { reading_strategy: 'balanced' },
        },
    },
    {
        id: 'thorough', name: 'Thorough', tagline: 'Most careful, slowest', detail: 'Wider search, two corrective retries, deeper answers, strict figure checking, two vision models on scans.',
        values: {
            models: { generator_model: '$strong', analysis_model: '$strong', grader_model: '$strong' },
            retrieval: { top_k: 20, rerank_top_n: 12, corrective_retries: 2 },
            answers: { style: 'deep', verify_mode: 'strict' },
            documents: { reading_strategy: 'accuracy' },
        },
    },
];

export interface Defaults { strong: string; light: string }

const fill = (v: unknown, d: Defaults): unknown => (v === '$strong' ? d.strong : v === '$light' ? d.light : v);

/** A preset with `$strong` / `$light` replaced by the default provider's models. */
export function resolvePreset(p: Preset, d: Defaults): Settings {
    return Object.fromEntries(Object.entries(p.values).map(([sec, vals]) => [sec, Object.fromEntries(Object.entries(vals).map(([k, v]) => [k, fill(v, d)]))]));
}

/** The preset whose values all equal the current settings, if any. */
export function matchingPreset(s: Settings, d: Defaults): Preset | undefined {
    return PRESETS.find(p => Object.entries(resolvePreset(p, d)).every(([sec, vals]) => Object.entries(vals).every(([k, v]) => JSON.stringify(s[sec]?.[k]) === JSON.stringify(v))));
}

export function applyPreset(base: Settings, p: Preset, d: Defaults): Settings {
    const next: Settings = JSON.parse(JSON.stringify(base));
    for (const [sec, vals] of Object.entries(resolvePreset(p, d))) next[sec] = { ...next[sec], ...vals };
    return next;
}
