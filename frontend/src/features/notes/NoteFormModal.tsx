import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { Modal } from '../../components/ui/Modal';
import { useAuth } from '../../context/useAuth';
import { allCompanies } from '../../data/foreignCompanies';
import { notesApi, type AnalystNote, type NoteInputMethod, type NoteVisibility, type Transcription } from '../../services/notesApi';
import { TranscriptionReview } from './TranscriptionReview';
import './NoteFormModal.css';

const MAX_CONTENT_CHARS = 20000; // mirrors the backend limit
const MAX_TITLE_CHARS = 200;
const MAX_TEXT_FILE_BYTES = 1024 * 1024;
const MAX_SCAN_BYTES = 10 * 1024 * 1024; // mirrors the backend upload limit
const TICKER_RE = /^[A-Z0-9][A-Z0-9.-]{0,9}$/;
const TEXT_FILE_RE = /\.(txt|md|markdown)$/i;
const SCAN_FILE_RE = /\.(jpe?g|png|webp|pdf)$/i;
const ACCEPT_ALL = '.jpg,.jpeg,.png,.webp,.pdf,.txt,.md,.markdown';
const ACCEPT_TEXT = '.txt,.md,.markdown';

/** Today in the user's own timezone (toISOString() is UTC, a day behind for the first hours of the day east of UTC). */
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

const COMPANY_BY_TICKER = new Map(allCompanies.map(c => [c.ticker.toUpperCase(), c]));
const TICKER_OPTIONS = [...COMPANY_BY_TICKER.values()].sort((a, b) => a.ticker.localeCompare(b.ticker));

interface FormState {
    ticker: string;
    title: string;
    note_date: string;
    visibility: NoteVisibility;
    content: string;
    input_method: NoteInputMethod;
}

const FIELDS = ['ticker', 'title', 'note_date', 'visibility', 'content', 'input_method'] as const;

const emptyForm = (): FormState => ({ ticker: '', title: '', note_date: today(), visibility: 'private', content: '', input_method: 'typed' });

const fromNote = (n: AnalystNote): FormState => ({
    ticker: n.ticker, title: n.title, note_date: n.note_date, visibility: n.visibility, content: n.content, input_method: n.input_method,
});

const isBlank = (f: FormState) => !f.ticker.trim() && !f.title.trim() && !f.content.trim();
const stripExt = (name: string) => name.replace(/\.[^.]+$/, '');
const countWords = (s: string) => (s.trim() ? s.trim().split(/\s+/).length : 0);

const defaultTitle = (ticker: string, date: string) => {
    const d = new Date(`${date}T00:00:00`);
    const pretty = Number.isNaN(d.getTime()) ? date : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    return `${ticker} note, ${pretty}`;
};

// ── Draft (new notes only): kept in this browser so closing the modal never loses a long note ──────────────────
const draftKey = (userId: string) => `investmentAnalyst_noteDraft_v1_${userId}`;

function readDraft(key: string): FormState | null {
    try {
        const raw = localStorage.getItem(key);
        if (!raw) return null;
        const d = JSON.parse(raw) as Partial<Record<keyof FormState, unknown>>;
        const str = (v: unknown) => (typeof v === 'string' ? v : '');
        const draft: FormState = {
            ticker: str(d.ticker).slice(0, 10),
            title: str(d.title).slice(0, MAX_TITLE_CHARS),
            content: str(d.content).slice(0, MAX_CONTENT_CHARS),
            note_date: /^\d{4}-\d{2}-\d{2}$/.test(str(d.note_date)) && str(d.note_date) <= today() ? str(d.note_date) : today(),
            visibility: d.visibility === 'shared' ? 'shared' : 'private',
            input_method: d.input_method === 'handwritten' ? 'handwritten' : 'typed',
        };
        return isBlank(draft) ? null : draft;
    } catch {
        return null; // storage blocked or corrupt: behave as if there is no draft
    }
}

function writeDraft(key: string, form: FormState) {
    try { localStorage.setItem(key, JSON.stringify(form)); } catch { /* storage full or blocked: the draft is a convenience */ }
}

function clearDraft(key: string) {
    try { localStorage.removeItem(key); } catch { /* ignore */ }
}

type Errors = Partial<Record<'ticker' | 'date' | 'title' | 'content', string>>;

function validate(f: FormState): Errors {
    const errors: Errors = {};
    if (!TICKER_RE.test(f.ticker.trim().toUpperCase())) errors.ticker = f.ticker.trim() ? 'Use letters and digits only, e.g. AAPL or BRK.B.' : 'Enter the ticker this note is about.';
    if (!f.note_date) errors.date = 'Pick the note date.';
    else if (f.note_date > today()) errors.date = 'The note date cannot be in the future.';
    if (f.title.trim().length > MAX_TITLE_CHARS) errors.title = `Keep the title under ${MAX_TITLE_CHARS} characters.`;
    if (!f.content.trim()) errors.content = 'Write the note, or add a file above.';
    else if (f.content.length > MAX_CONTENT_CHARS) errors.content = `The note is over the ${MAX_CONTENT_CHARS.toLocaleString()} character limit.`;
    return errors;
}

interface NoteFormModalProps {
    isOpen: boolean;
    editing: AnalystNote | null;
    onClose: () => void;
    /** Called after a successful save with the toast text; the caller closes the modal and refreshes. */
    onSaved: (message: string) => void;
}

export const NoteFormModal: React.FC<NoteFormModalProps> = ({ isOpen, editing, onClose, onSaved }) => {
    const { user } = useAuth();
    const storageKey = user ? draftKey(String(user.id)) : null;

    const [form, setForm] = useState<FormState>(emptyForm);
    const [baseline, setBaseline] = useState<FormState>(emptyForm); // what "no changes" means: the note being edited, or blank
    const [restored, setRestored] = useState(false);
    const [attempted, setAttempted] = useState(false);
    const [serverError, setServerError] = useState<string | null>(null);
    const [dropMessage, setDropMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null);
    const [saving, setSaving] = useState(false);
    const [transcribing, setTranscribing] = useState(false);
    const [dragging, setDragging] = useState(false);
    const [review, setReview] = useState<{ result: Transcription; previewUrl: string | null; previewKind: 'image' | 'pdf'; fileName: string } | null>(null);

    const tickerRef = useRef<HTMLInputElement>(null);
    const dateRef = useRef<HTMLInputElement>(null);
    const contentRef = useRef<HTMLTextAreaElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const dragDepth = useRef(0);
    const skipDraft = useRef(false); // set once saved, so a pending autosave cannot bring the draft back

    // Open: load the note being edited, or the saved draft (if any) for a new one.
    useEffect(() => {
        if (!isOpen) return;
        skipDraft.current = false;
        setAttempted(false);
        setServerError(null);
        setDropMessage(null);
        setReview(null);
        setDragging(false);
        dragDepth.current = 0;
        if (editing) {
            const f = fromNote(editing);
            setForm(f);
            setBaseline(f);
            setRestored(false);
        } else {
            const draft = storageKey ? readDraft(storageKey) : null;
            setForm(draft ?? emptyForm());
            setBaseline(emptyForm());
            setRestored(!!draft);
        }
        const focus = window.setTimeout(() => (editing ? contentRef : tickerRef).current?.focus(), 60);
        return () => window.clearTimeout(focus);
    }, [isOpen, editing, storageKey]);

    // Autosave the draft of a NEW note while the form is open.
    useEffect(() => {
        if (!isOpen || editing || !storageKey || review) return;
        const t = window.setTimeout(() => {
            if (skipDraft.current) return;
            if (isBlank(form)) clearDraft(storageKey);
            else writeDraft(storageKey, form);
        }, 400);
        return () => window.clearTimeout(t);
    }, [form, isOpen, editing, storageKey, review]);

    // Release the image preview when the review ends or the modal goes away.
    useEffect(() => () => { if (review?.previewUrl) URL.revokeObjectURL(review.previewUrl); }, [review]);

    const dirty = useMemo(() => FIELDS.some(k => form[k] !== baseline[k]), [form, baseline]);
    const errors = useMemo(() => validate(form), [form]);
    const shown: Errors = attempted ? errors : {};
    const company = COMPANY_BY_TICKER.get(form.ticker.trim().toUpperCase());

    const setField = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm(prev => ({ ...prev, [key]: value }));

    const requestClose = () => {
        if (saving || transcribing) return;
        if (review && !confirm('Discard this transcription?')) return;
        if (editing && dirty && !confirm('Discard your changes to this note?')) return; // a new note keeps its draft instead
        onClose();
    };

    /** Add text under what is already written (never silently replacing it). */
    const addText = (text: string, fromName: string, handwritten: boolean) => {
        const base = form.content.trim();
        const merged = base ? `${base}\n\n${text}` : text;
        setForm(prev => ({
            ...prev,
            content: merged.slice(0, MAX_CONTENT_CHARS),
            title: prev.title || stripExt(fromName),
            input_method: handwritten ? 'handwritten' : prev.input_method,
        }));
        if (merged.length > MAX_CONTENT_CHARS) {
            setDropMessage({ kind: 'info', text: `Cut to the first ${MAX_CONTENT_CHARS.toLocaleString()} characters.` });
        }
    };

    const handleFiles = async (files: FileList | File[] | null) => {
        const list = files ? Array.from(files) : [];
        const file = list[0];
        if (!file || transcribing) return;
        setDropMessage(list.length > 1 ? { kind: 'info', text: `Using only the first of ${list.length} files: ${file.name}.` } : null);

        if (TEXT_FILE_RE.test(file.name)) {
            if (file.size > MAX_TEXT_FILE_BYTES) { setDropMessage({ kind: 'error', text: 'That text file is too large (limit 1 MB).' }); return; }
            addText((await file.text()).trim(), file.name, false);
            return;
        }
        if (SCAN_FILE_RE.test(file.name)) {
            if (editing) { setDropMessage({ kind: 'error', text: 'Handwritten or scanned pages can only be added when creating a new note.' }); return; }
            if (file.size > MAX_SCAN_BYTES) { setDropMessage({ kind: 'error', text: 'That file is larger than 10 MB.' }); return; }
            setTranscribing(true);
            try {
                const result = await notesApi.transcribe(file);
                const isPdf = file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
                const previewable = isPdf || file.type.startsWith('image/');
                setReview({
                    result,
                    previewUrl: previewable ? URL.createObjectURL(file) : null,
                    previewKind: isPdf ? 'pdf' : 'image',
                    fileName: file.name,
                });
            } catch (err) {
                setDropMessage({ kind: 'error', text: err instanceof Error ? err.message : 'Transcription failed.' });
            } finally {
                setTranscribing(false);
            }
            return;
        }
        setDropMessage({ kind: 'error', text: 'Unsupported file. Use a JPG, PNG or WEBP photo, a PDF, or a .txt / .md file. HEIC photos must be exported as JPG first.' });
    };

    const hasFiles = (e: React.DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');
    const dragProps = {
        // The whole form is a drop target: an image dropped on the wrong spot would otherwise navigate the browser away.
        onDragEnter: (e: React.DragEvent) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth.current += 1; setDragging(true); },
        onDragOver: (e: React.DragEvent) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; },
        onDragLeave: (e: React.DragEvent) => { if (!hasFiles(e)) return; dragDepth.current = Math.max(0, dragDepth.current - 1); if (dragDepth.current === 0) setDragging(false); },
        onDrop: (e: React.DragEvent) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            dragDepth.current = 0;
            setDragging(false);
            void handleFiles(e.dataTransfer.files);
        },
    };

    const submit = async (e?: React.FormEvent) => {
        e?.preventDefault();
        if (saving || transcribing) return;
        setAttempted(true);
        const found = validate(form);
        if (found.ticker) { tickerRef.current?.focus(); return; }
        if (found.date) { dateRef.current?.focus(); return; }
        if (found.title || found.content) { if (found.content) contentRef.current?.focus(); return; }

        const ticker = form.ticker.trim().toUpperCase();
        const payload = {
            ticker,
            title: form.title.trim() || defaultTitle(ticker, form.note_date),
            content: form.content.trim(),
            note_date: form.note_date,
            visibility: form.visibility,
        };
        try {
            setSaving(true);
            setServerError(null);
            if (editing) await notesApi.update(editing.note_id, payload);
            else await notesApi.create({ ...payload, input_method: form.input_method });
            skipDraft.current = true;
            if (storageKey) clearDraft(storageKey);
            onSaved(editing ? '✓ Note updated' : '✓ Note saved and indexed');
        } catch (err) {
            setServerError(err instanceof Error ? err.message : 'Could not save the note.');
        } finally {
            setSaving(false);
        }
    };

    const startOver = () => {
        if (storageKey) clearDraft(storageKey);
        setForm(emptyForm());
        setRestored(false);
        setAttempted(false);
        window.setTimeout(() => tickerRef.current?.focus(), 0);
    };

    const chars = form.content.length;
    const counterClass = chars > MAX_CONTENT_CHARS ? ' nf__counter--over' : chars > MAX_CONTENT_CHARS * 0.9 ? ' nf__counter--warn' : '';
    const title = review ? 'Check the transcription' : editing ? 'Edit Analyst Note' : 'Add Analyst Note';

    return (
        <Modal isOpen={isOpen} onClose={requestClose} title={title} size={review ? 'xl' : 'lg'} contentClassName="nf-modal-content">
            {review ? (
                <div className="nf__body">
                    <TranscriptionReview
                        result={review.result}
                        previewUrl={review.previewUrl}
                        previewKind={review.previewKind}
                        onConfirm={text => {
                            addText(text, review.fileName, !review.result.pages.every(p => p.source === 'text-layer'));
                            setReview(null);
                        }}
                        onCancel={() => setReview(null)}
                    />
                </div>
            ) : (
                <form
                    className="nf"
                    onSubmit={submit}
                    noValidate
                    onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit(); } }}
                    {...dragProps}
                >
                    <div className="nf__body">
                        {serverError && <div className="nf__alert" role="alert">{serverError}</div>}

                        {restored && !editing && !isBlank(form) && (
                            <div className="nf__banner" role="status">
                                <span>Restored your unsaved draft.</span>
                                <button type="button" className="nf__link" onClick={startOver}>Start over</button>
                            </div>
                        )}

                        <div className="nf__row">
                            <div className="nf__field">
                                <label className="nf__label" htmlFor="note-ticker">Ticker</label>
                                <input
                                    id="note-ticker"
                                    ref={tickerRef}
                                    className={`nf__input${shown.ticker ? ' nf__input--error' : ''}`}
                                    placeholder="e.g. AAPL"
                                    list="note-ticker-options"
                                    value={form.ticker}
                                    onChange={e => setField('ticker', e.target.value.toUpperCase())}
                                    maxLength={10}
                                    autoComplete="off"
                                    spellCheck={false}
                                    aria-invalid={!!shown.ticker}
                                    aria-describedby="note-ticker-hint"
                                />
                                <datalist id="note-ticker-options">
                                    {TICKER_OPTIONS.map(c => <option key={c.ticker} value={c.ticker}>{c.name}</option>)}
                                </datalist>
                                <span id="note-ticker-hint" className={shown.ticker ? 'nf__error' : 'nf__hint'}>
                                    {shown.ticker
                                        || (company ? `${company.name} · ${company.sector}`
                                            : TICKER_RE.test(form.ticker.trim()) ? 'Not in the company list. It is saved, but only used when you analyse a company with this ticker.'
                                                : 'Start typing to pick from the company list.')}
                                </span>
                            </div>
                            <div className="nf__field">
                                <label className="nf__label" htmlFor="note-date">Note date</label>
                                <input
                                    id="note-date"
                                    ref={dateRef}
                                    type="date"
                                    className={`nf__input${shown.date ? ' nf__input--error' : ''}`}
                                    value={form.note_date}
                                    max={today()}
                                    onChange={e => setField('note_date', e.target.value)}
                                    aria-invalid={!!shown.date}
                                />
                                {shown.date && <span className="nf__error">{shown.date}</span>}
                            </div>
                        </div>

                        <div className="nf__field">
                            <label className="nf__label" htmlFor="note-title">Title <span className="nf__optional">optional</span></label>
                            <input
                                id="note-title"
                                className={`nf__input${shown.title ? ' nf__input--error' : ''}`}
                                placeholder={form.ticker.trim() ? defaultTitle(form.ticker.trim().toUpperCase(), form.note_date) : 'e.g. Q3 earnings call takeaways'}
                                value={form.title}
                                onChange={e => setField('title', e.target.value)}
                                maxLength={MAX_TITLE_CHARS}
                                autoComplete="off"
                            />
                            {shown.title && <span className="nf__error">{shown.title}</span>}
                        </div>

                        <div
                            className={`nf__drop${dragging ? ' nf__drop--active' : ''}${transcribing ? ' nf__drop--busy' : ''}`}
                            role="button"
                            tabIndex={transcribing ? -1 : 0}
                            aria-busy={transcribing}
                            aria-label={editing ? 'Add from a text file' : 'Add a handwritten note, scan or text file'}
                            onClick={() => { if (!transcribing) fileInputRef.current?.click(); }}
                            onKeyDown={e => { if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) { e.preventDefault(); if (!transcribing) fileInputRef.current?.click(); } }}
                        >
                            <span className="nf__drop-icon" aria-hidden="true">{transcribing ? '⏳' : '✍'}</span>
                            <span className="nf__drop-text">
                                {transcribing
                                    ? <><strong>Reading your note…</strong> two readers are checking each other, this takes a few seconds.</>
                                    : editing
                                        ? <><strong>Add from a text file</strong> (.txt or .md): drop it here or browse.</>
                                        : <><strong>Handwritten or scanned note?</strong> Drop a photo, scan or PDF here, or browse. A .txt / .md file works too.</>}
                            </span>
                            <input ref={fileInputRef} type="file" accept={editing ? ACCEPT_TEXT : ACCEPT_ALL} hidden
                                onChange={e => { const f = e.target.files; void handleFiles(f); e.target.value = ''; }} />
                        </div>
                        {dropMessage && <div className={dropMessage.kind === 'error' ? 'nf__error' : 'nf__hint'} role={dropMessage.kind === 'error' ? 'alert' : 'status'}>{dropMessage.text}</div>}

                        <div className="nf__field">
                            <label className="nf__label" htmlFor="note-content">Note</label>
                            <textarea
                                id="note-content"
                                ref={contentRef}
                                className={`nf__input nf__textarea${shown.content ? ' nf__input--error' : ''}`}
                                placeholder={'PT $215 (was 200)\nRev Q3 94.9B +6% YoY\nWatch: services margin'}
                                value={form.content}
                                onChange={e => setForm(prev => ({ ...prev, content: e.target.value, input_method: e.target.value.trim() ? prev.input_method : 'typed' }))}
                                aria-invalid={!!shown.content}
                            />
                            <div className="nf__meta">
                                <span className={shown.content ? 'nf__error' : 'nf__hint'}>
                                    {shown.content || (form.input_method === 'handwritten'
                                        ? '✍ Transcribed from a handwritten or scanned note, checked by you. You can still edit it.'
                                        : 'Plain text. Numbers and views you want your analysis to remember.')}
                                </span>
                                <span className={`nf__counter${counterClass}`}>{chars.toLocaleString()} / {MAX_CONTENT_CHARS.toLocaleString()} · {countWords(form.content).toLocaleString()} words</span>
                            </div>
                        </div>

                        <fieldset className="nf__field nf__fieldset">
                            <legend className="nf__label">Who can use it</legend>
                            <div className="nf__vis" role="radiogroup" aria-label="Who can use this note">
                                {([
                                    ['private', '🔒 Private', 'Only you. It informs your own analysis of this ticker.'],
                                    ['shared', '👥 Shared', 'Others can read it, but it only affects their analysis if they switch it on.'],
                                ] as const).map(([value, name, description]) => (
                                    <label key={value} className={`nf__vis-option${form.visibility === value ? ' nf__vis-option--on' : ''}`}>
                                        <input type="radio" name="note-visibility" value={value} checked={form.visibility === value} onChange={() => setField('visibility', value)} />
                                        <span className="nf__vis-name">{name}</span>
                                        <span className="nf__vis-desc">{description}</span>
                                    </label>
                                ))}
                            </div>
                        </fieldset>
                    </div>

                    <div className="nf__footer">
                        <span className="nf__shortcut">Ctrl/⌘ + Enter to save</span>
                        <span className="nf__spacer" />
                        <Button type="button" variant="outline" onClick={requestClose} disabled={saving || transcribing}>Cancel</Button>
                        <Button type="submit" variant="primary" loading={saving} disabled={transcribing}>{editing ? 'Save Changes' : 'Save Note'}</Button>
                    </div>
                </form>
            )}
        </Modal>
    );
};

export default NoteFormModal;

