import { Modal } from './Modal';
import { Button } from './Button';
import './RenameModal.css';

interface RenameModalProps {
    isOpen: boolean;
    title: string;
    value: string;
    onChange: (value: string) => void;
    onCancel: () => void;
    onConfirm: () => void;
}

/** Shared rename dialog — used for renaming chat/search sessions across research pages. */
export function RenameModal({ isOpen, title, value, onChange, onCancel, onConfirm }: RenameModalProps) {
    return (
        <Modal isOpen={isOpen} onClose={onCancel} title={title} size="sm">
            <input
                type="text"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && onConfirm()}
                autoFocus
                className="rename-modal__input"
            />
            <div className="rename-modal__actions">
                <Button variant="secondary" onClick={onCancel}>Cancel</Button>
                <Button variant="primary" onClick={onConfirm}>Rename</Button>
            </div>
        </Modal>
    );
}

export default RenameModal;
