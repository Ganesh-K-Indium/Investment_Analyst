import React, { useCallback, useRef } from 'react';
import TiptapImage from '@tiptap/extension-image';
import { ReactNodeViewRenderer, NodeViewWrapper, type ReactNodeViewProps } from '@tiptap/react';

const MIN_WIDTH = 60;
type Align = 'left' | 'center' | 'right';

function ResizableImageView({ node, updateAttributes, selected }: ReactNodeViewProps) {
    const wrapperRef = useRef<HTMLDivElement>(null);
    const { src, alt, title, width, align } = node.attrs as {
        src: string; alt: string | null; title: string | null; width: string | null; align: Align | null;
    };

    const startResize = useCallback((e: React.PointerEvent, direction: 1 | -1) => {
        e.preventDefault();
        e.stopPropagation();
        const img = wrapperRef.current?.querySelector('img');
        if (!img) return;
        const startX = e.clientX;
        const startWidth = img.getBoundingClientRect().width;

        const onMove = (moveEvent: PointerEvent) => {
            const delta = (moveEvent.clientX - startX) * direction;
            const newWidth = Math.max(MIN_WIDTH, Math.round(startWidth + delta));
            updateAttributes({ width: `${newWidth}px` });
        };
        const onUp = () => {
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
        };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
    }, [updateAttributes]);

    const setAlign = (next: Align) => updateAttributes({ align: next });

    return (
        <NodeViewWrapper
            className={`creation__image-node creation__image-node--${align || 'center'}`}
            ref={wrapperRef}
            data-drag-handle
        >
            <div className={`creation__image-inner ${selected ? 'creation__image-inner--selected' : ''}`}>
                <img src={src} alt={alt || ''} title={title || ''} style={width ? { width } : undefined} draggable={false} />
                {selected && (
                    <>
                        <div className="creation__image-toolbar" onPointerDown={e => e.stopPropagation()}>
                            <button type="button" onClick={() => setAlign('left')} title="Align left" className={align === 'left' ? 'active' : ''}>⇤</button>
                            <button type="button" onClick={() => setAlign('center')} title="Align center" className={(!align || align === 'center') ? 'active' : ''}>⇔</button>
                            <button type="button" onClick={() => setAlign('right')} title="Align right" className={align === 'right' ? 'active' : ''}>⇥</button>
                            <button type="button" onClick={() => updateAttributes({ width: null })} title="Reset size">↺</button>
                        </div>
                        <span className="creation__image-handle creation__image-handle--l" onPointerDown={e => startResize(e, -1)} />
                        <span className="creation__image-handle creation__image-handle--r" onPointerDown={e => startResize(e, 1)} />
                    </>
                )}
            </div>
        </NodeViewWrapper>
    );
}

export const ResizableImage = TiptapImage.extend({
    addAttributes() {
        return {
            ...this.parent?.(),
            width: { default: null },
            align: { default: 'center' },
        };
    },
    addNodeView() {
        return ReactNodeViewRenderer(ResizableImageView);
    },
});
