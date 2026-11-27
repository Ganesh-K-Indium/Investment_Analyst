import type { Settings } from '../../services/settingsApi';

export interface Step {
    id: string;
    section: string;       // the settings section this step opens
    title: string;
    line: string;          // what it is set to, in words
}

const get = <T,>(s: Settings, sec: string, key: string, d: T): T => (s[sec]?.[key] as T | undefined) ?? d;
/** "anthropic:claude-sonnet-5-5" -> "claude-sonnet-5-5 (anthropic)" */
export const niceSpec = (spec: string): string => {
    const i = spec.indexOf(':');
    return i > 0 && ['anthropic', 'gemini', 'ollama', 'custom'].includes(spec.slice(0, i)) ? `${spec.slice(i + 1)} (${spec.slice(0, i)})` : spec;
};
const SOURCE: Record<string, string> = { filings: 'filings', uploads: 'my documents', notes: 'notes', web: 'web', macro: 'macro' };

/** The pipeline a question goes through, with the current choice at each stage. Also used for the one-line summary under each tab. */
export function pipelineSteps(s: Settings): Step[] {
    const sources = get<string[]>(s, 'retrieval', 'sources', []).map(x => SOURCE[x] ?? x);
    const rerank = get(s, 'retrieval', 'rerank_enabled', true) && get<string>(s, 'retrieval', 'rerank_provider', 'local') !== 'off';
    const retries = get(s, 'retrieval', 'corrective_retries', 1);
    const verify = get<string>(s, 'answers', 'verify_mode', 'warn');
    return [
        { id: 'search', section: 'retrieval', title: 'Search', line: `${get(s, 'retrieval', 'top_k', 12)} passages from ${sources.length ? sources.join(', ') : 'no sources'}` },
        { id: 'rank', section: 'retrieval', title: 'Re-rank', line: rerank ? `${get(s, 'retrieval', 'rerank_provider', 'local')}, keep ${get(s, 'retrieval', 'rerank_top_n', 8)}` : 'off' },
        { id: 'check', section: 'retrieval', title: 'Check evidence', line: retries ? `search again up to ${retries}×` : 'no retry' },
        { id: 'write', section: 'models', title: 'Write', line: `${niceSpec(get(s, 'models', 'generator_model', 'gpt-4o'))}, ${get(s, 'answers', 'style', 'standard')}` },
        { id: 'verify', section: 'answers', title: 'Verify figures', line: verify === 'off' ? 'off' : verify === 'strict' ? 'strict' : 'flag unsupported' },
    ];
}

export function sectionSummary(id: string, s: Settings): string {
    switch (id) {
        case 'models': return `${niceSpec(get(s, 'models', 'generator_model', 'gpt-4o'))} writes answers`;
        case 'providers': return 'Connect your own models';
        case 'retrieval': return `${get(s, 'retrieval', 'top_k', 12)} passages, re-rank ${get(s, 'retrieval', 'rerank_enabled', true) ? 'on' : 'off'}`;
        case 'answers': return `${get(s, 'answers', 'style', 'standard')}, figures ${get(s, 'answers', 'verify_mode', 'warn')}`;
        case 'modules': return `${get<unknown[]>(s, 'modules', 'custom', []).length} custom framework${get<unknown[]>(s, 'modules', 'custom', []).length === 1 ? '' : 's'}`;
        case 'prompts': return `${Object.keys(get<Record<string, string>>(s, 'prompts', 'overrides', {})).length} customized`;
        case 'documents': return `scans: ${get(s, 'documents', 'reading_strategy', 'balanced')}`;
        case 'quant': return `${get<string[]>(s, 'quant', 'enabled_agents', []).length} agents on`;
        case 'ui': return `${get(s, 'ui', 'theme', 'system')} theme`;
        default: return '';
    }
}

/** Fields shown together under a heading inside a section; anything not listed goes under "More". */
export const GROUPS: Record<string, { title: string; hint?: string; keys: string[] }[]> = {
    models: [
        { title: 'Writing and analysis', hint: 'These produce what you read, so they get the stronger models.', keys: ['generator_model', 'analysis_model', 'macro_writer_model', 'quant_supervisor_model', 'quant_agent_model', 'temperature'] },
        { title: 'Planning and checking', hint: 'Short, structured jobs. A smaller model is usually enough.', keys: ['router_model', 'grader_model', 'verifier_model', 'rewriter_model', 'macro_planner_model', 'summarizer_model'] },
        { title: 'Limits', keys: ['request_timeout', 'max_retries', 'monthly_token_budget'] },
    ],
    retrieval: [
        { title: 'Where to look', keys: ['sources', 'filing_types', 'year_from', 'top_k', 'context_token_budget'] },
        { title: 'Ranking and retries', hint: 'Re-ranking reads each passage against the question; a retry rewrites the search when the evidence is weak.', keys: ['rerank_enabled', 'rerank_provider', 'rerank_top_n', 'corrective_retries', 'web_fallback'] },
        { title: 'Indexing', keys: ['contextual_prefix'] },
    ],
    answers: [
        { title: 'Trust', hint: 'Figures are checked against the passages the answer was written from.', keys: ['verify_mode', 'units_check', 'clarify_ambiguous'] },
        { title: 'Presentation', keys: ['style', 'citations', 'streaming'] },
    ],
    documents: [
        { title: 'Reading your files', keys: ['reading_strategy', 'local_only', 'escalate_below', 'token_cap', 'auto_detect_type'] },
        { title: 'Search and sharing', keys: ['index_policy', 'default_visibility'] },
        { title: 'Limits', keys: ['max_upload_mb', 'max_pages'] },
    ],
    quant: [
        { title: 'Agents', keys: ['enabled_agents', 'risk_profile'] },
        { title: 'Market data', keys: ['history_window', 'benchmark', 'tool_timeout', 'cache_ttl', 'recursion_limit'] },
    ],
};
