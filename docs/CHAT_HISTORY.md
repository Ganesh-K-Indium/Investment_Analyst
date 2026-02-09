# Chat History System Documentation

## Overview

Production-grade chat history persistence system for the Investment Analyst API. Supports both RAG and Quant agents with comprehensive features for managing, exporting, and clearing chat history.

## Architecture

### Database Schema

#### `chat_sessions` Table
Tracks individual chat sessions across different agents.

```sql
CREATE TABLE chat_sessions (
    id INTEGER PRIMARY KEY,
    session_id VARCHAR UNIQUE NOT NULL,
    user_id VARCHAR NOT NULL,
    portfolio_id INTEGER REFERENCES portfolios(id),
    agent_type ENUM('rag', 'quant') NOT NULL,
    title VARCHAR,
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME,
    last_message_at DATETIME
);
```

#### `chat_messages` Table
Stores individual messages within sessions.

```sql
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY,
    chat_session_id INTEGER REFERENCES chat_sessions(id),
    role ENUM('user', 'assistant', 'system') NOT NULL,
    content TEXT NOT NULL,
    message_metadata JSON,
    token_count INTEGER,
    created_at DATETIME
);
```

### Key Features

1. **Automatic Chat Persistence**: All RAG and Quant queries are automatically saved to the database
2. **Portfolio Integration**: Sessions can be linked to portfolios for organization
3. **User-Centric**: All chats are organized by user_id for easy retrieval
4. **Metadata Support**: Store additional context (sources, citations, agent info) with each message
5. **Soft Delete**: Sessions can be deactivated without losing data
6. **Export Support**: Export conversations to JSON or TXT format
7. **Production-Grade**: Proper indexing, relationships, and cascading deletes

## API Endpoints

### Get User's Chat Sessions

```http
GET /chats/user/{user_id}/sessions
```

**Query Parameters:**
- `agent_type` (optional): Filter by "rag" or "quant"
- `portfolio_id` (optional): Filter by portfolio
- `include_inactive` (optional): Include archived sessions (default: false)

**Response:**
```json
[
  {
    "session_id": "rag_user123_20260209_143022",
    "user_id": "user123",
    "agent_type": "rag",
    "portfolio_id": 1,
    "title": "RAG: Tech Portfolio",
    "is_active": true,
    "message_count": 15,
    "created_at": "2026-02-09T14:30:22",
    "last_message_at": "2026-02-09T15:45:10"
  }
]
```

### Get Session Chat History

```http
GET /chats/session/{session_id}
```

**Query Parameters:**
- `limit` (optional): Maximum number of messages
- `offset` (optional): Skip first N messages (for pagination)

**Response:**
```json
{
  "session_id": "rag_user123_20260209_143022",
  "user_id": "user123",
  "agent_type": "rag",
  "portfolio_id": 1,
  "title": "RAG: Tech Portfolio",
  "message_count": 15,
  "messages": [
    {
      "role": "user",
      "content": "What is Apple's revenue?",
      "metadata": null,
      "timestamp": "2026-02-09T14:30:22"
    },
    {
      "role": "assistant",
      "content": "Apple's revenue is...",
      "metadata": {
        "sources": ["AAPL_10K_2025.pdf"],
        "portfolio_id": 1
      },
      "timestamp": "2026-02-09T14:30:25"
    }
  ]
}
```

### Export Session

```http
GET /chats/session/{session_id}/export?format=json
```

**Query Parameters:**
- `format`: "json" or "txt"

**Response:** 
Downloads the complete chat session as a file.

### Update Session Title

```http
PUT /chats/session/{session_id}/title
```

**Request Body:**
```json
{
  "title": "My Custom Chat Title"
}
```

### Clear Session Messages

```http
DELETE /chats/session/{session_id}/messages
```

Clears all messages but keeps the session for future use.

### Delete Session

```http
DELETE /chats/session/{session_id}
```

Permanently deletes the session and all its messages.

### Deactivate Session

```http
POST /chats/session/{session_id}/deactivate
```

Soft deletes the session (can be recovered).

### Get Session Statistics

```http
GET /chats/session/{session_id}/stats
```

**Response:**
```json
{
  "session_id": "rag_user123_20260209_143022",
  "message_count": 15,
  "total_tokens": 4532,
  "agent_type": "rag",
  "created_at": "2026-02-09T14:30:22",
  "last_message_at": "2026-02-09T15:45:10"
}
```

### Get User Statistics

```http
GET /chats/user/{user_id}/stats
```

**Response:**
```json
{
  "user_id": "user123",
  "total_sessions": 25,
  "rag_sessions": 15,
  "quant_sessions": 10,
  "total_messages": 342
}
```

### Get Portfolio Chat Sessions

```http
GET /chats/portfolio/{portfolio_id}/sessions
```

**Query Parameters:**
- `agent_type` (optional): Filter by "rag" or "quant"

Returns all chat sessions associated with a specific portfolio.

## Integration with Existing Endpoints

### RAG Endpoints (`/ask`, `/compare`)
- Automatically creates/retrieves a `ChatSession` for the `thread_id`
- Saves user queries as `USER` messages
- Saves agent responses as `ASSISTANT` messages
- Includes metadata: sources, portfolio info, document count

### Quant Endpoints (`/quant/query`)
- Automatically creates/retrieves a `ChatSession` for the `session_id`
- Saves user queries as `USER` messages
- Saves agent responses as `ASSISTANT` messages
- Includes metadata: agent used, portfolio info

## ChatService API

The `ChatService` class provides business logic for chat operations:

### Creating/Getting Sessions
```python
from app.services.chat import ChatService
from app.database.models import AgentType

chat_session = ChatService.create_or_get_chat_session(
    db=db,
    session_id="rag_user123_20260209_143022",
    user_id="user123",
    agent_type=AgentType.RAG,
    portfolio_id=1,
    title="My Chat Session"
)
```

### Adding Messages
```python
from app.database.models import MessageRole

message = ChatService.add_message(
    db=db,
    session_id="rag_user123_20260209_143022",
    role=MessageRole.USER,
    content="What is Apple's revenue?",
    metadata={"query_type": "financial"}
)
```

### Retrieving Messages
```python
messages = ChatService.get_session_messages(
    db=db,
    session_id="rag_user123_20260209_143022",
    limit=50
)
```

### Getting User Sessions
```python
sessions = ChatService.get_user_sessions(
    db=db,
    user_id="user123",
    agent_type=AgentType.RAG,
    portfolio_id=1
)
```

### Exporting Sessions
```python
export_data = ChatService.export_session(
    db=db,
    session_id="rag_user123_20260209_143022"
)
```

### Clearing/Deleting
```python
# Clear messages but keep session
count = ChatService.clear_session_messages(db, session_id)

# Soft delete
ChatService.deactivate_session(db, session_id)

# Hard delete
ChatService.delete_session(db, session_id)
```

## User Login & Chat Restoration

When a user logs in, retrieve their chat history:

```python
# Get all user's sessions
sessions = ChatService.get_user_sessions(
    db=db,
    user_id="user123",
    include_inactive=False
)

# Get messages for a specific session
messages = ChatService.get_session_messages(
    db=db,
    session_id=sessions[0].session_id
)

# Restore conversation in UI
for msg in messages:
    display_message(role=msg.role, content=msg.content)
```

## Migration

The chat history system uses Alembic for database migrations:

```bash
# View current migration status
alembic current

# Upgrade to latest schema
alembic upgrade head

# Rollback chat history (if needed)
alembic downgrade 001_initial
```

## Performance Considerations

### Indexing
The system includes indexes on:
- `chat_sessions.session_id` (unique)
- `chat_sessions.user_id`
- `chat_sessions.agent_type`
- `chat_sessions.created_at`
- `chat_messages.chat_session_id`
- `chat_messages.created_at`

### Pagination
Use `limit` and `offset` parameters for large chat histories:

```http
GET /chats/session/{session_id}?limit=50&offset=0
```

### Cascading Deletes
When a `ChatSession` is deleted, all associated `ChatMessage` records are automatically deleted.

## Security Considerations

1. **User Isolation**: Always filter queries by `user_id` to prevent unauthorized access
2. **API Authentication**: Implement proper authentication middleware (not included in this base implementation)
3. **Data Privacy**: Consider encryption for sensitive message content
4. **Rate Limiting**: Implement rate limiting on export endpoints to prevent abuse

## Example Workflows

### Workflow 1: User Creates Portfolio and Chats

1. User creates portfolio: `POST /portfolios`
2. User creates session: `POST /portfolios/sessions`
3. User asks RAG question: `POST /ask`
   - System automatically creates `ChatSession`
   - System saves user message
   - System saves assistant response
4. User views history: `GET /chats/user/{user_id}/sessions`

### Workflow 2: User Exports Chat

1. User views sessions: `GET /chats/user/{user_id}/sessions`
2. User selects session to export
3. User downloads: `GET /chats/session/{session_id}/export?format=txt`

### Workflow 3: User Returns After Logout

1. User logs in
2. System fetches sessions: `GET /chats/user/{user_id}/sessions`
3. User selects previous session
4. System displays history: `GET /chats/session/{session_id}`
5. User continues conversation with same `session_id`

## Testing

See `test_chat_system.py` for comprehensive tests of all functionality.

## Future Enhancements

1. **Search**: Full-text search across chat messages
2. **Tagging**: Allow users to tag conversations for better organization
3. **Sharing**: Enable users to share chat sessions
4. **Analytics**: Track conversation metrics and patterns
5. **Retention Policies**: Automatic cleanup of old conversations
6. **Compression**: Compress old message content to save space
