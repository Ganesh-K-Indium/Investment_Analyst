// Portfolio API Service

import { getAuthHeader } from './authHeader';
import { API_BASE } from './http';

const PORTFOLIO_BASE_URL = `${API_BASE}/portfolios`;

export interface PortfolioCreate {
    user_id: string;
    name: string;
    tickers: string[];
    description?: string;
}

export interface PortfolioUpdate {
    name?: string;
    tickers?: string[];
    description?: string;
}

export interface PortfolioResponse {
    id: number;
    user_id: string;
    name: string;
    tickers: string[];
    description: string | null;
    created_at: string;
    updated_at: string;
}

export interface SessionCreateRequest {
    portfolio_id: number;
    user_id: string;
    thread_id?: string;
    agent_type?: string;
}

export interface SessionResponse {
    thread_id: string;
    portfolio_id: number;
    user_id: string;
    portfolio_name: string;
    tickers: string[];
    created_at: string;
    last_accessed: string;
}

export interface DashboardTaskSummary {
    id: string;
    portfolio_id: number | null;
    portfolio_name: string | null;
    agent_type: string;
    task_type: string;
    status: string;
    progress_message: string | null;
    session_id: string | null;
    created_at: string | null;
    updated_at: string | null;
}

export interface DashboardPortfolioCard {
    id: number;
    name: string;
    tickers: string[];
    description: string | null;
    created_at: string | null;
    updated_at: string | null;
    latest_task: DashboardTaskSummary | null;
}

export interface CrossPortfolioDashboardResponse {
    portfolios: DashboardPortfolioCard[];
    recent_activity: DashboardTaskSummary[];
}

export const portfolioApi = {
    /**
     * Get all portfolios for a user
     */
    async getAllPortfolios(userId: string): Promise<PortfolioResponse[]> {
        const response = await fetch(`${PORTFOLIO_BASE_URL}/user/${userId}`, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            const error = new Error(`Failed to fetch portfolios: ${response.status} ${response.statusText}`) as Error & { status?: number };
            error.status = response.status;
            throw error;
        }
        return response.json();
    },

    /**
     * Get a specific portfolio by ID
     */
    async getPortfolio(portfolioId: number): Promise<PortfolioResponse> {
        const response = await fetch(`${PORTFOLIO_BASE_URL}/${portfolioId}`, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to fetch portfolio: ${response.status} ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Create a new portfolio
     */
    async createPortfolio(data: PortfolioCreate): Promise<PortfolioResponse> {
        const response = await fetch(`${PORTFOLIO_BASE_URL}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `Failed to create portfolio: ${response.status}`);
        }
        return response.json();
    },

    /**
     * Update an existing portfolio
     */
    async updatePortfolio(portfolioId: number, data: PortfolioUpdate): Promise<PortfolioResponse> {
        const response = await fetch(`${PORTFOLIO_BASE_URL}/${portfolioId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `Failed to update portfolio: ${response.status}`);
        }
        return response.json();
    },

    /**
     * Delete a portfolio
     */
    async deletePortfolio(portfolioId: number): Promise<{ message: string }> {
        const response = await fetch(`${PORTFOLIO_BASE_URL}/${portfolioId}`, {
            method: 'DELETE',
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to delete portfolio: ${response.status}`);
        }
        return response.json();
    },

    /**
     * Create a new session for a portfolio
     */
    async createSession(data: SessionCreateRequest): Promise<SessionResponse> {
        const response = await fetch(`${PORTFOLIO_BASE_URL}/sessions`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `Failed to create session: ${response.status}`);
        }
        return response.json();
    },

    /**
     * Get a session by thread_id
     */
    async getSession(threadId: string): Promise<SessionResponse> {
        const response = await fetch(`${PORTFOLIO_BASE_URL}/sessions/${threadId}`, {
            headers: { ...getAuthHeader() },
        });
        if (!response.ok) {
            throw new Error(`Failed to fetch session: ${response.status}`);
        }
        return response.json();
    },

    /**
     * Aggregate view across all of a user's portfolios: one card per
     * portfolio (with its most recent run) plus a merged recent-activity
     * feed, for the cross-portfolio dashboard.
     */
    async getCrossPortfolioDashboard(userId: string, activityLimit = 50): Promise<CrossPortfolioDashboardResponse> {
        const response = await fetch(
            `${PORTFOLIO_BASE_URL}/dashboard/cross-portfolio?user_id=${encodeURIComponent(userId)}&activity_limit=${activityLimit}`,
            { headers: { ...getAuthHeader() } }
        );
        if (!response.ok) {
            throw new Error(`Failed to fetch cross-portfolio dashboard: ${response.status}`);
        }
        return response.json();
    }
};

export default portfolioApi;
