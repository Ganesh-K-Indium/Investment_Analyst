import React, { useEffect } from 'react';
import './Modal.css';

interface ModalProps {
    isOpen: boolean;
    onClose: () => void;
    title?: React.ReactNode;
    size?: 'sm' | 'md' | 'lg' | 'xl' | 'xxl';
    /** Stretches the modal to a fixed viewport height and lets content fill it (e.g. an embedded PDF/iframe) instead of the default auto-height, scrollable body. */
    fullHeight?: boolean;
    contentClassName?: string;
    hideCloseButton?: boolean;
    children: React.ReactNode;
}

export const Modal: React.FC<ModalProps> = ({ isOpen, onClose, title, size = 'md', fullHeight = false, contentClassName, hideCloseButton = false, children }) => {
    useEffect(() => {
        const handleEscape = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && !hideCloseButton) {
                onClose();
            }
        };

        if (isOpen) {
            document.addEventListener('keydown', handleEscape);
            document.body.style.overflow = 'hidden';
        }

        return () => {
            document.removeEventListener('keydown', handleEscape);
            document.body.style.overflow = 'unset';
        };
    }, [isOpen, onClose, hideCloseButton]);

    if (!isOpen) return null;

    return (
        <div className="modal-overlay" onClick={() => { if (!hideCloseButton) onClose(); }}>
            <div className={['modal', `modal--${size}`, fullHeight && 'modal--full-height'].filter(Boolean).join(' ')} onClick={(e) => e.stopPropagation()}>
                <div className="modal__header">
                    {title && <h2 className="modal__title">{title}</h2>}
                    {!hideCloseButton && (
                        <button className="modal__close" onClick={onClose}>
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <line x1="18" y1="6" x2="6" y2="18" />
                                <line x1="6" y1="6" x2="18" y2="18" />
                            </svg>
                        </button>
                    )}
                </div>
                <div className={['modal__content', fullHeight && 'modal__content--full-height', contentClassName].filter(Boolean).join(' ')}>
                    {children}
                </div>
            </div>
        </div>
    );
};

export default Modal;
