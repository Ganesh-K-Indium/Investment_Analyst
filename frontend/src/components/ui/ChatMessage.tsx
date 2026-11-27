import type { ReactNode } from 'react';
import { Avatar, AvatarFallback } from './avatar';
import './ChatMessage.css';

export type ChatMessageRole = 'user' | 'assistant';

/** Bot glyph — the shared "this came from the AI" marker across every research page. */
function DefaultAssistantIcon() {
    return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="4" y="9" width="16" height="11" rx="3" />
            <path d="M12 9V5" />
            <circle cx="12" cy="3.5" r="1.5" fill="currentColor" stroke="none" />
            <circle cx="9" cy="14.5" r="1.25" fill="currentColor" stroke="none" />
            <circle cx="15" cy="14.5" r="1.25" fill="currentColor" stroke="none" />
            <path d="M2 13h2M20 13h2" />
        </svg>
    );
}

function ClipIcon() {
    return (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
            <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
        </svg>
    );
}

interface ChatMessageProps {
    role: ChatMessageRole;
    /** Custom avatar content. For role="user" this is ignored in favor of userName's initials when userName is given. */
    avatar?: ReactNode;
    /** Display name for the current user — rendered as initials in the avatar instead of a generic person icon. */
    userName?: string;
    /** When given, a hover-revealed "Add to Clipboard" button clips this whole message (text + any embedded image) in one action. */
    onClip?: () => void;
    children: ReactNode;
    className?: string;
}

function UserAvatarIcon() {
    return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
        </svg>
    );
}

/** Shared structural shell for a chat message card (avatar + elevated content card). */
export function ChatMessage({ role, avatar, onClip, children, className }: ChatMessageProps) {
    const avatarContent = role === 'user'
        ? <UserAvatarIcon />
        : (avatar ?? <DefaultAssistantIcon />);

    return (
        <div className={['chat-message', `chat-message--${role}`, className].filter(Boolean).join(' ')}>
            <Avatar className={`chat-message__avatar chat-message__avatar--${role}`}>
                <AvatarFallback>
                    {avatarContent}
                </AvatarFallback>
            </Avatar>
            <div className="chat-message__card">
                {onClip && (
                    <button
                        type="button"
                        className="chat-message__clip-btn"
                        onClick={onClip}
                        title="Add to Clipboard"
                    >
                        <ClipIcon />
                        Add to Clipboard
                    </button>
                )}
                <div className="chat-message__content">
                    {children}
                </div>
            </div>
        </div>
    );
}

interface ChatMessageStatusProps {
    text?: string;
}

/** Animated "thinking/searching" indicator shown in place of an assistant message while a response streams in. */
export function ChatMessageStatus({ text }: ChatMessageStatusProps) {
    return (
        <div className="chat-message__status">
            {text && <span className="chat-message__status-text">{text}</span>}
            <span className="chat-message__status-dots">
                <span></span><span></span><span></span>
            </span>
        </div>
    );
}
