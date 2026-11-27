import React, { useEffect, useRef } from 'react';

/**
 * Cursor-reactive helpers for the landing page. All three write CSS custom
 * properties straight onto the element (no React state), so moving the pointer
 * never re-renders anything. Effects are skipped for touch devices and for
 * people who prefer reduced motion.
 */

const canHover = () =>
    typeof window !== 'undefined' &&
    window.matchMedia('(hover: hover) and (pointer: fine)').matches &&
    !window.matchMedia('(prefers-reduced-motion: reduce)').matches;

interface BoxProps extends React.HTMLAttributes<HTMLDivElement> {
    children: React.ReactNode;
}

/** A card with a soft spotlight that follows the cursor. */
export const GlowCard: React.FC<BoxProps> = ({ className = '', children, onPointerMove, ...rest }) => {
    const ref = useRef<HTMLDivElement>(null);

    const handleMove = (e: React.PointerEvent<HTMLDivElement>) => {
        onPointerMove?.(e);
        const el = ref.current;
        if (!el || !canHover()) return;
        const r = el.getBoundingClientRect();
        el.style.setProperty('--mx', `${e.clientX - r.left}px`);
        el.style.setProperty('--my', `${e.clientY - r.top}px`);
    };

    return (
        <div ref={ref} className={`lp-glow ${className}`} onPointerMove={handleMove} {...rest}>
            {children}
        </div>
    );
};

/** Gently tilts its child toward the cursor (a few degrees at most). */
export const Tilt: React.FC<BoxProps & { max?: number }> = ({ className = '', children, max = 2.5, ...rest }) => {
    const ref = useRef<HTMLDivElement>(null);
    const frame = useRef(0);

    const handleMove = (e: React.PointerEvent<HTMLDivElement>) => {
        const el = ref.current;
        if (!el || !canHover()) return;
        const { clientX, clientY } = e;
        cancelAnimationFrame(frame.current);
        frame.current = requestAnimationFrame(() => {
            const r = el.getBoundingClientRect();
            const x = (clientX - r.left) / r.width - 0.5;
            const y = (clientY - r.top) / r.height - 0.5;
            el.style.setProperty('--rx', `${(-y * max * 2).toFixed(2)}deg`);
            el.style.setProperty('--ry', `${(x * max * 2).toFixed(2)}deg`);
        });
    };

    const reset = () => {
        cancelAnimationFrame(frame.current);
        ref.current?.style.setProperty('--rx', '0deg');
        ref.current?.style.setProperty('--ry', '0deg');
    };

    return (
        <div className={`lp-tilt ${className}`} onPointerMove={handleMove} onPointerLeave={reset} {...rest}>
            <div ref={ref} className="lp-tilt__inner">{children}</div>
        </div>
    );
};

/**
 * A page-wide field of small dots that part around the cursor and settle back
 * when it leaves. One fixed canvas behind the content; the loop only runs while
 * something is moving. Touch and reduced-motion users get the still field.
 */
export const DotField: React.FC = () => {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const GAP = 30;          // spacing between dots
        const RADIUS = 130;      // cursor influence
        const PUSH = 26;         // max displacement (px)
        const interactive = canHover();

        let dots: { x: number; y: number; ox: number; oy: number }[] = [];
        let w = 0, h = 0, dpr = 1;
        let mx = -9999, my = -9999;
        let raf = 0;
        // Both colours come from the theme: the dots use the canvas's CSS colour (var(--color-text-placeholder)), the
        // highlight the accent (var(--color-primary)), read through a probe element so any colour format resolves to rgb.
        let dotRGB = '';
        let accent = '';
        const rgbOf = (css: string) => {
            const m = css.match(/\d+(\.\d+)?/g);
            return m && m.length >= 3 ? `${m[0]},${m[1]},${m[2]}` : '';
        };
        const probeEl = document.createElement('span');
        const readColour = () => {
            dotRGB = rgbOf(getComputedStyle(canvas).color) || dotRGB;
            probeEl.style.color = 'var(--color-primary)';
            canvas.parentElement?.appendChild(probeEl);
            accent = rgbOf(getComputedStyle(probeEl).color) || accent;
            probeEl.remove();
        };

        const build = () => {
            dpr = window.devicePixelRatio || 1;
            w = canvas.clientWidth;
            h = canvas.clientHeight;
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            dots = [];
            const cols = Math.ceil(w / GAP) + 1;
            const rows = Math.ceil(h / GAP) + 1;
            const offX = (w - (cols - 1) * GAP) / 2;
            const offY = (h - (rows - 1) * GAP) / 2;
            for (let r = 0; r < rows; r++) {
                for (let c = 0; c < cols; c++) {
                    dots.push({ x: offX + c * GAP, y: offY + r * GAP, ox: 0, oy: 0 });
                }
            }
            readColour();
        };

        const draw = () => {
            ctx.clearRect(0, 0, w, h);
            let moving = false;
            for (const d of dots) {
                let tx = 0, ty = 0, heat = 0;
                if (interactive) {
                    const dx = d.x - mx;
                    const dy = d.y - my;
                    const dist = Math.hypot(dx, dy);
                    if (dist < RADIUS && dist > 0.01) {
                        const f = 1 - dist / RADIUS;
                        const push = f * f * PUSH;
                        tx = (dx / dist) * push;
                        ty = (dy / dist) * push;
                        heat = f;
                    }
                }
                d.ox += (tx - d.ox) * 0.16;
                d.oy += (ty - d.oy) * 0.16;
                if (Math.abs(d.ox - tx) > 0.05 || Math.abs(d.oy - ty) > 0.05) moving = true;

                const near = Math.hypot(d.ox, d.oy) / PUSH;
                ctx.beginPath();
                ctx.arc(d.x + d.ox, d.y + d.oy, 1.1 + near * 0.9, 0, Math.PI * 2);
                ctx.fillStyle = heat > 0.02 || near > 0.05
                    ? `rgba(${accent},${Math.min(0.55, 0.22 + Math.max(heat, near) * 0.45)})`
                    : `rgba(${dotRGB},0.32)`;
                ctx.fill();
            }
            raf = moving || mx > -9000 ? requestAnimationFrame(draw) : 0;
        };

        const kick = () => { if (!raf) raf = requestAnimationFrame(draw); };

        const onMove = (e: PointerEvent) => { mx = e.clientX; my = e.clientY; kick(); };
        const onLeave = () => { mx = -9999; my = -9999; kick(); };
        const onResize = () => { build(); kick(); };

        build();
        draw();
        if (interactive) {
            window.addEventListener('pointermove', onMove, { passive: true });
            document.addEventListener('pointerleave', onLeave);
        }
        window.addEventListener('resize', onResize);
        const themeObserver = new MutationObserver(() => { readColour(); kick(); });
        themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

        return () => {
            cancelAnimationFrame(raf);
            window.removeEventListener('pointermove', onMove);
            document.removeEventListener('pointerleave', onLeave);
            window.removeEventListener('resize', onResize);
            themeObserver.disconnect();
        };
    }, []);

    return <canvas ref={canvasRef} className="lp-dots" aria-hidden="true" />;
};
