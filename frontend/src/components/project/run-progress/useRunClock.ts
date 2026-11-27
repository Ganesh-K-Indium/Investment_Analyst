import { useEffect, useRef, useState } from 'react';

/** Re-renders a few times a second while `active`, returning the current time in ms. Drives the elapsed clock. */
export function useNow(active: boolean, intervalMs = 250): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!active) return;
        const id = window.setInterval(() => setNow(Date.now()), intervalMs);
        return () => window.clearInterval(id);
    }, [active, intervalMs]);
    return now;
}

const prefersReducedMotion = () =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Eases each displayed value toward its target, so shapes that can't be animated by CSS (an SVG polygon's points) grow
 * smoothly instead of jumping when a step completes. Stops its animation loop as soon as everything has arrived.
 * With reduced motion the values snap.
 */
export function useEasedValues(targets: number[]): number[] {
    const [values, setValues] = useState<number[]>(targets);
    const current = useRef<number[]>(targets);
    const key = targets.join('|');

    useEffect(() => {
        if (prefersReducedMotion()) {
            current.current = targets;
            setValues(targets);
            return;
        }
        let raf = 0;
        const tick = () => {
            const next = current.current.map((v, i) => {
                const t = targets[i] ?? 0;
                return Math.abs(t - v) < 0.002 ? t : v + (t - v) * 0.09;
            });
            current.current = next;
            setValues(next);
            if (next.some((v, i) => v !== (targets[i] ?? 0))) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf);
        // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` is the identity of `targets`
    }, [key]);

    return values;
}
