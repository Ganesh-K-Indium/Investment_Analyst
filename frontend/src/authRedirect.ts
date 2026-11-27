/** Where to go after signing in: the page the user was sent away from, if it is a path inside this app (never another site). */
export function returnPath(state: unknown): string {
    const from = (state as { from?: unknown } | null)?.from;
    return typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') && !from.startsWith('/login') ? from : '/home';
}
