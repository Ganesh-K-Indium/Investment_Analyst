import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merges conditional class names and resolves Tailwind class conflicts — required by shadcn/ui components. */
export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

/** "Jane Doe" -> "JD". Used for avatar fallbacks wherever we only have a display name. */
export function getInitials(name: string): string {
    return name
        .split(' ')
        .filter(Boolean)
        .map(w => w[0])
        .join('')
        .toUpperCase()
        .slice(0, 2);
}

/** "2h ago" / "3d ago" style relative time for an ISO timestamp. */
export function timeAgo(isoString: string): string {
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return mins <= 1 ? 'Just now' : `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(isoString).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
