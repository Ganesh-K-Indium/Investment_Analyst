import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Modal } from '../../components/ui/Modal';
import { notesApi, type AnalystNote, type NoteVisibility } from '../../services/notesApi';
import { NoteFormModal } from './NoteFormModal';
import '../../pages/DataSources.css';
import './AnalystNotesSection.css';

interface AnalystNotesSectionProps {
    onShowToast: (message: string) => void;
    /** Open this note's read-only view once the list has loaded (deep link from /notes/:noteId). */
    openNoteId?: string;
    /** Called when that deep-linked view is closed, so the page can drop the id from the URL. */
    onViewClose?: () => void;
    /** 'manage' (default): the list of notes with edit / share / delete. 'add': only the add button, for the Data Sources
     *  page, which is for adding sources, not for browsing what was added. */
    mode?: 'manage' | 'add';
    /** Hide the section's own title and subtitle when the page already shows them. */
    hideHeading?: boolean;
    /** 'add' mode: where the 'view my notes' link goes. */
    onViewNotes?: () => void;
    /** 'add' mode: when set, a second tile and link lead to the user's uploaded documents. */
    onViewDocuments?: () => void;
    /** Increment to open the Add Note form (lets a page put the Add button in its own heading row). */
    createRequest?: number;
}

export const AnalystNotesSection: React.FC<AnalystNotesSectionProps> = ({
    onShowToast, openNoteId, onViewClose, mode = 'manage', hideHeading = false, onViewNotes, onViewDocuments, createRequest = 0,
}) => {
    const addOnly = mode === 'add';
    const [notes, setNotes] = useState<AnalystNote[]>([]);
    const [isLoading, setIsLoading] = useState(!addOnly);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [isFormOpen, setIsFormOpen] = useState(false);
    const [editing, setEditing] = useState<AnalystNote | null>(null);
    const [viewing, setViewing] = useState<AnalystNote | null>(null);
    const [busyId, setBusyId] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setNotes(await notesApi.list());
            setLoadError(null);
        } catch (err) {
            setLoadError(err instanceof Error ? err.message : 'Failed to load notes');
        } finally {
            setIsLoading(false);
        }
    }, []);

    useEffect(() => { if (!addOnly) load(); }, [load, addOnly]);

    // Deep link: open the requested note once (own note, or one shared with the user).
    const openedFor = useRef<string | null>(null);
    useEffect(() => {
        if (!openNoteId || isLoading || openedFor.current === openNoteId) return;
        openedFor.current = openNoteId;
        const found = notes.find(n => n.note_id === openNoteId);
        if (found) { setViewing(found); return; }
        notesApi.get(openNoteId).then(setViewing).catch(() => onShowToast('✗ That note was not found, or it is private to someone else'));
    }, [openNoteId, isLoading, notes, onShowToast]);

    const closeView = () => { setViewing(null); onViewClose?.(); };

    const openCreate = () => {
        setEditing(null);
        setIsFormOpen(true);
    };

    const lastCreate = useRef(createRequest);
    useEffect(() => {
        if (createRequest !== lastCreate.current) { lastCreate.current = createRequest; openCreate(); }
    }, [createRequest]);

    const openEdit = (note: AnalystNote) => {
        setEditing(note);
        setIsFormOpen(true);
    };

    const closeForm = () => {
        setIsFormOpen(false);
        setEditing(null);
    };

    const handleSaved = async (message: string) => {
        onShowToast(message);
        closeForm();
        if (!addOnly) await load();
    };

    const toggleVisibility = async (note: AnalystNote) => {
        const next: NoteVisibility = note.visibility === 'private' ? 'shared' : 'private';
        if (next === 'shared' && !confirm(`Share "${note.title}" with every user? They will be able to read it and it can inform their analysis.`)) return;
        try {
            setBusyId(note.note_id);
            await notesApi.update(note.note_id, { visibility: next });
            onShowToast(next === 'shared' ? '✓ Note shared' : '✓ Note is now private');
            await load();
        } catch (err) {
            onShowToast(`✗ ${err instanceof Error ? err.message : 'Could not change visibility'}`);
        } finally {
            setBusyId(null);
        }
    };

    /** One switch, two meanings: for my own note it pauses/resumes it ("hide for now"); for someone else's shared
     *  note it opts me in or out of using it in my analysis. */
    const toggleUse = async (note: AnalystNote) => {
        try {
            setBusyId(note.note_id);
            if (note.is_owner) {
                await notesApi.update(note.note_id, { active: !note.active });
                onShowToast(note.active ? '✓ Note hidden from analysis for now' : '✓ Note is back in your analysis');
            } else {
                await notesApi.setInclusion(note.note_id, !note.included);
                onShowToast(note.included ? '✓ Note removed from your analysis' : '✓ Note added to your analysis');
            }
            await load();
        } catch (err) {
            onShowToast(`✗ ${err instanceof Error ? err.message : 'Could not update the note'}`);
        } finally {
            setBusyId(null);
        }
    };

    const handleDelete = async (note: AnalystNote) => {
        if (!confirm(`Delete "${note.title}"? This removes it from your analysis context.`)) return;
        try {
            setBusyId(note.note_id);
            await notesApi.remove(note.note_id);
            onShowToast('✓ Note deleted');
            await load();
        } catch (err) {
            onShowToast(`✗ ${err instanceof Error ? err.message : 'Delete failed'}`);
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div className="data-sources-section analyst-notes">
            {addOnly ? (
                <div className="analyst-notes__ingest">
                    <div className="analyst-notes__ingest-head">
                        <div>
                            <h2 className="analyst-notes__title">{onViewDocuments ? 'Add Your Documents & Notes' : 'Add Your Notes'}</h2>
                            <p className="analyst-notes__subtitle">Add your own files and research as extra context for your analysis of a ticker.</p>
                        </div>
                        <div style={{ display: 'flex', gap: '16px' }}>
                            {onViewDocuments && (
                                <button type="button" className="analyst-notes__link analyst-notes__link--title" onClick={onViewDocuments}>
                                    View my documents →
                                </button>
                            )}
                            {onViewNotes && (
                                <button type="button" className="analyst-notes__link analyst-notes__link--title" onClick={onViewNotes}>
                                    View my notes →
                                </button>
                            )}
                        </div>
                    </div>
                    <Card style={{ padding: '0', overflow: 'hidden' }}>
                        <div style={{ padding: '24px' }}>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '16px' }}>
                                {onViewDocuments && (
                                    <button type="button" className="analyst-notes__source" onClick={onViewDocuments}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                                            <div style={{ fontSize: '24px' }}>📄</div>
                                            <h4 style={{ margin: 0, fontSize: '16px', color: 'var(--color-text-primary)' }}>Your Documents</h4>
                                        </div>
                                        <p style={{ fontSize: '13px', color: 'var(--color-text-secondary)', margin: 0, textAlign: 'left' }}>
                                            Upload statements, models, broker notes, decks or scans. They are read, checked and searched with the filings.
                                        </p>
                                    </button>
                                )}
                                <button type="button" className="analyst-notes__source" onClick={openCreate}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                                        <div style={{ fontSize: '24px' }}>📝</div>
                                        <h4 style={{ margin: 0, fontSize: '16px', color: 'var(--color-text-primary)' }}>Analyst Note</h4>
                                    </div>
                                    <p style={{ fontSize: '13px', color: 'var(--color-text-secondary)', margin: 0, textAlign: 'left' }}>
                                        Type a note, load a .txt / .md file, or upload a handwritten or scanned page. Private by default; only you can edit it.
                                    </p>
                                </button>
                            </div>
                        </div>
                    </Card>
                </div>
            ) : !hideHeading && (
                <div className="analyst-notes__header">
                    <div>
                        <h2 className="analyst-notes__title">My Analyst Notes</h2>
                        <p className="analyst-notes__subtitle">
                            Your own research notes, used as extra context in your analysis of the same ticker. Private by default; only you can edit them.
                        </p>
                    </div>
                    <Button variant="primary" onClick={openCreate}>Add Note</Button>
                </div>
            )}

            {addOnly ? null : isLoading ? (
                <div className="analyst-notes__state">Loading notes...</div>
            ) : loadError ? (
                <div className="analyst-notes__state analyst-notes__state--error" role="alert">
                    {loadError} <button className="analyst-notes__link" onClick={() => { setIsLoading(true); load(); }}>Retry</button>
                </div>
            ) : notes.length === 0 ? (
                <div className="analyst-notes__state">
                    No notes yet. Add one to have it considered the next time you analyse that company.
                </div>
            ) : (
                <div className="analyst-notes__table">
                    <table>
                        <colgroup>
                            <col className="analyst-notes__col-ticker" />
                            <col />
                            <col className="analyst-notes__col-date" />
                            <col className="analyst-notes__col-visibility" />
                            <col className="analyst-notes__col-author" />
                            <col className="analyst-notes__col-use" />
                            <col className="analyst-notes__col-actions" />
                        </colgroup>
                        <thead>
                            <tr>
                                <th>Ticker</th>
                                <th>Title</th>
                                <th>Date</th>
                                <th>Visibility</th>
                                <th>Author</th>
                                <th>In my analysis</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {notes.map(note => (
                                <tr key={note.note_id}>
                                    <td><span className="analyst-notes__ticker">{note.ticker}</span></td>
                                    <td>
                                        <button className="analyst-notes__link analyst-notes__link--title" onClick={() => setViewing(note)} title="Open note">
                                            {note.title}
                                        </button>
                                        {note.input_method === 'handwritten' && <span className="analyst-notes__tag" title="Transcribed from a handwritten or scanned note and checked by its author">✍ handwritten</span>}
                                    </td>
                                    <td>{note.note_date}</td>
                                    <td>
                                        <span className={`analyst-notes__badge analyst-notes__badge--${note.visibility}`}>
                                            {note.visibility === 'private' ? 'Private' : 'Shared'}
                                        </span>
                                    </td>
                                    <td>{note.is_owner ? 'You' : note.author}</td>
                                    <td>
                                        <div className="analyst-notes__use">
                                            <button
                                                type="button"
                                                role="switch"
                                                aria-checked={note.used_in_analysis}
                                                className={`analyst-notes__switch${note.used_in_analysis ? ' analyst-notes__switch--on' : ''}`}
                                                onClick={() => toggleUse(note)}
                                                disabled={busyId === note.note_id}
                                                aria-label={note.is_owner
                                                    ? (note.active ? 'Hide this note from analysis for now' : 'Bring this note back into analysis')
                                                    : (note.included ? 'Stop using this note in my analysis' : 'Use this note in my analysis')}
                                                title={note.is_owner
                                                    ? (note.active ? 'In your analysis. Click to hide it for now.' : 'Hidden for now. Click to bring it back.')
                                                    : (note.included ? 'In your analysis. Click to stop using it.' : 'Not in your analysis. Click to use it.')}
                                            />
                                            <span className="analyst-notes__use-label">
                                                {note.is_owner
                                                    ? (note.active ? 'On' : 'Hidden')
                                                    : (note.included ? 'Included' : 'Off')}
                                            </span>
                                        </div>
                                    </td>
                                    <td>
                                        {note.is_owner ? (
                                            <div className="analyst-notes__actions">
                                                <button
                                                    className="analyst-notes__action-btn"
                                                    onClick={() => openEdit(note)}
                                                    disabled={busyId === note.note_id}
                                                    title="Edit"
                                                >
                                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                                    </svg>
                                                </button>
                                                <button
                                                    className="analyst-notes__action-btn"
                                                    onClick={() => toggleVisibility(note)}
                                                    disabled={busyId === note.note_id}
                                                    title={note.visibility === 'private' ? 'Share with everyone' : 'Make private'}
                                                >
                                                    {note.visibility === 'private' ? (
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                            <circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" />
                                                            <line x1="8.6" y1="13.5" x2="15.4" y2="17.5" /><line x1="15.4" y1="6.5" x2="8.6" y2="10.5" />
                                                        </svg>
                                                    ) : (
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                            <rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
                                                        </svg>
                                                    )}
                                                </button>
                                                <button
                                                    className="analyst-notes__action-btn analyst-notes__action-btn--danger"
                                                    onClick={() => handleDelete(note)}
                                                    disabled={busyId === note.note_id}
                                                    title="Delete"
                                                >
                                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                        <polyline points="3 6 5 6 21 6" />
                                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                                    </svg>
                                                </button>
                                            </div>
                                        ) : (
                                            <span className="analyst-notes__readonly">Read only</span>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Add / edit */}
            <NoteFormModal isOpen={isFormOpen} editing={editing} onClose={closeForm} onSaved={handleSaved} />

            {/* Read-only view (also used for notes shared by others) */}
            <Modal isOpen={!!viewing} onClose={closeView} title={viewing ? `${viewing.ticker}: ${viewing.title}` : ''} size="lg">
                {viewing && (
                    <div className="analyst-notes__view">
                        <p className="analyst-notes__meta">
                            {viewing.is_owner ? 'You' : viewing.author} · {viewing.note_date} · {viewing.visibility === 'private' ? 'Private' : 'Shared'}{viewing.input_method === 'handwritten' ? ' · ✍ transcribed from handwriting' : ''}
                        </p>
                        <pre className="analyst-notes__body">{viewing.content}</pre>
                    </div>
                )}
            </Modal>
        </div>
    );
};

export default AnalystNotesSection;
