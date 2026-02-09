# Agentic RAG API Documentation & Integration Guide

## Overview
This API provides a production-grade backend for an Agentic RAG system with portfolio management. It allows users to define "portfolios" (groups of companies), filter data by those portfolios, and ask complex financial questions.

## Core Integration Flow
To integrate the UI with the backend, follow this typical user flow:

1.  **Create/Select Portfolio**: User defines which companies they are interested in.
2.  **Initialize Session**: When the user enters the chat interface with a selected portfolio, create a "session". This prepares the backend (Vector DB) for that specific context.
3.  **Chat (Ask)**: Send user queries with the `thread_id` obtained from the session. 
4.  **Compare**: Use the standalone compare endpoint for direct company comparisons.

---

## Endpoint Reference

### 1. Portfolio Management
Manage the collections of companies a user is interested in.

#### List User Portfolios
Fetch all portfolios for a specific user to display in a sidebar or dashboard.
- **GET** `/portfolios/user/{user_id}`
- **Response**: List of portfolio objects (id, name, company_names, etc).

#### Create Portfolio
- **POST** `/portfolios/`
- **Payload**:
  ```json
  {
    "user_id": "user_123",
    "name": "Tech Giants",
    "company_names": ["Apple", "Microsoft", "Google"],
    "description": "My tech portfolio"
  }
  ```

#### Get/Update/Delete Portfolio
- **GET** `/portfolios/{id}`
- **PUT** `/portfolios/{id}`
- **DELETE** `/portfolios/{id}`

---

### 2. Session Management (Critical)
**This is the bridge between Portfolios and Chat.** 
Before sending messages to `/ask`, you MUST ensure a valid session exists.

#### Create/Start Session
Call this when a user selects a portfolio to start chatting.
- **POST** `/portfolios/sessions`
- **Payload**:
  ```json
  {
    "portfolio_id": 1,
    "user_id": "user_123",
    "thread_id": "optional-custom-id" 
  }
  ```
- **Response**: Returns a `thread_id`. **Store this `thread_id` in the UI state.** It is required for all subsequent chat messages.

#### Get Session Info
- **GET** `/portfolios/sessions/{thread_id}`

---

### 3. RAG & Chat
Interaction with the AI Agent.

#### Ask Question (Chat)
- **POST** `/ask`
- **Payload**:
  ```json
  {
    "query": "How did Apple's revenue change in 2024?",
    "thread_id": "thread_abc123" // Obtained from Create Session endpoint
  }
  ```
- **Response**:
  ```json
  {
    "answer": "Apple's revenue in 2024...",
    "thread_id": "thread_abc123",
    "portfolio_name": "Tech Giants",
    "company_filter": ["Apple", "Microsoft", "Google"],
    "messages": [...], // Full conversation context
    "documents": [...] // Source documents cited
  }
  ```

#### Compare Companies (Independent)
This endpoint works independently of portfolios. It creates a temporary context for comparison.
- **POST** `/compare`
- **Payload**:
  ```json
  {
    "company1": "Apple",
    "company2": "Microsoft",
    "company3": "Google", // Optional
    "thread_id": "optional-thread-id"
  }
  ```

---

## Integration Tips for UI Developer

1.  **State Management**: 
    - Keep track of the `current_portfolio_id` and the active `thread_id`.
    - If `thread_id` is null, the user cannot chat. Prompt them to select a portfolio to "Start Session".

2.  **Streaming vs Polling**:
    - Currently, the API returns a full JSON response (not streaming). Show a loading state while waiting for the response.

3.  **Visualization**:
    - The `/ask` response contains extensive metadata (`documents`, `intermediate_message`, `sub_query_analysis`). You can use `documents` to show "Sources" or citations in the UI.

4.  **Error Handling**:
    - If `/ask` returns `404 Session not found`, automatically try to call `POST /portfolios/sessions` with the current portfolio ID to re-initialize, then retry the message.
