// Your own MCP servers for the quant agent (backend: app/api/mcp_servers.py). The auth header is write-only.
import { apiRequest } from './http';

export interface McpTool { name: string; description: string; read_only: boolean }
export interface McpServer {
    id: string; name: string; url: string; description: string | null; enabled: boolean; allow_actions: boolean;
    has_auth: boolean; tools: McpTool[]; last_error: string | null;
}
export interface McpList { servers: McpServer[]; max: number; private_allowed: boolean }

export const mcpServersApi = {
    list: () => apiRequest<McpList>('/mcp-servers'),
    add: (b: { name: string; url: string; description?: string; auth_name?: string; auth_value?: string; allow_actions?: boolean }) =>
        apiRequest<McpServer>('/mcp-servers', { method: 'POST', body: JSON.stringify(b) }),
    patch: (id: string, b: Partial<{ name: string; description: string; enabled: boolean; allow_actions: boolean; auth_name: string; auth_value: string }>) =>
        apiRequest<McpServer>(`/mcp-servers/${id}`, { method: 'PATCH', body: JSON.stringify(b) }),
    remove: (id: string) => apiRequest<void>(`/mcp-servers/${id}`, { method: 'DELETE' }),
    test: (id: string) => apiRequest<McpServer>(`/mcp-servers/${id}/test`, { method: 'POST' }),
};
