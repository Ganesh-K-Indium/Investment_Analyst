/** The current value of a design token (a CSS custom property on :root), for the few places that need a concrete colour
 * rather than a `var(...)`: values written into saved content, canvas drawing and image export. Tokens live in index.css. */
export function cssVar(name: string): string {
    if (typeof document === 'undefined') return '';
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
