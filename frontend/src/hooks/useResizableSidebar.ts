import { useCallback, useEffect, useRef, useState } from 'react';

interface UseResizableSidebarOptions {
    defaultWidth: number;
    min: number;
    max: number;
}

/**
 * Drag-to-resize + collapse state for a sidebar panel. Pointer tracking lives
 * in refs (not state) so dragging doesn't re-render on every mousemove —
 * only the committed width does.
 */
export function useResizableSidebar({ defaultWidth, min, max }: UseResizableSidebarOptions) {
    const [collapsed, setCollapsed] = useState(false);
    const [width, setWidth] = useState(defaultWidth);
    const isResizingRef = useRef(false);
    const resizeStartXRef = useRef(0);
    const resizeStartWidthRef = useRef(defaultWidth);
    const resizeHandleRef = useRef<HTMLDivElement>(null);

    const handleResizeMouseDown = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        isResizingRef.current = true;
        resizeStartXRef.current = e.clientX;
        resizeStartWidthRef.current = width;
        resizeHandleRef.current?.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    }, [width]);

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isResizingRef.current) return;
            const delta = e.clientX - resizeStartXRef.current;
            setWidth(Math.min(max, Math.max(min, resizeStartWidthRef.current + delta)));
        };
        const handleMouseUp = () => {
            if (!isResizingRef.current) return;
            isResizingRef.current = false;
            resizeHandleRef.current?.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        };
        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', handleMouseUp);
        return () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
    }, [min, max]);

    return { collapsed, setCollapsed, width, resizeHandleRef, handleResizeMouseDown };
}
