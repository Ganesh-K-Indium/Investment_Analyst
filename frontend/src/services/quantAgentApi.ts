// Quantitative Agent API Service

import { getAuthHeader } from './authHeader';
import { analysisTasksApi, type AnalysisTask } from './analysisTasksApi';
import { API_BASE } from './http';

const QUANT_AGENT_BASE_URL = API_BASE;

export interface ChatRequest {
    query: string;
    portfolio_id?: number;
    user_id: string;
    session_id?: string;
    postgres_integration_id?: number;
}

export interface ChatResponse {
    response: string;
    session_id: string;
    portfolio_id: number | null;
    timestamp: string;
    success: boolean;
    agent_used: string | null;
    metadata: Record<string, unknown> | null;
}

export const quantAgentApi = {
    /**
     * Send a query to the Quant Agent
     */
    async chat(request: ChatRequest): Promise<ChatResponse> {
        const response = await fetch(`${QUANT_AGENT_BASE_URL}/quant/query`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(request),
        });

        if (!response.ok) {
            throw new Error(`API request failed: ${response.status} ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Queue-backed equivalent of chat() — same request/response shape, but
     * runs through the background job system (interactive lane) instead of
     * blocking the request directly, and shows up in the portfolio's
     * analysis-run history alongside quant deep-dives and RAG runs.
     */
    async chatViaQueue(request: ChatRequest): Promise<ChatResponse> {
        const task: AnalysisTask = await analysisTasksApi.enqueueQuantQuery(request);
        const done = await analysisTasksApi.pollUntilDone(task.id);
        if (done.status === 'FAILED' || done.status === 'CANCELLED') {
            throw new Error(done.progress_message || 'Quant query failed');
        }
        return (done.result_metadata as { response: ChatResponse }).response;
    },
};

export default quantAgentApi;
