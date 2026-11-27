import React, { useCallback, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { PageHeader } from '../../components/layout/PageHeader';
import { Button } from '../../components/ui/Button';
import { AnalystNotesSection } from './AnalystNotesSection';
import { LibraryTabs } from '../library/LibraryTabs';
import '../documents/DocumentsPage.css';
import './NotesPage.css';

/** /notes (list) and /notes/:noteId (list with that note open, as linked from a chat citation). */
export const NotesPage: React.FC = () => {
    const { noteId } = useParams<{ noteId: string }>();
    const navigate = useNavigate();
    const [toast, setToast] = useState<string | null>(null);
    const [createRequest, setCreateRequest] = useState(0);

    const showToast = useCallback((message: string) => {
        setToast(message);
        setTimeout(() => setToast(null), 3000);
    }, []);

    return (
        <PageLayout className="docs-page notes-page">
            <PageHeader
                title="Documents"
                description="Your own research notes, alongside the filings. Only you see a note unless you share it. Notes others share stay out of your analysis until you switch them on."
                actions={<Button variant="primary" onClick={() => setCreateRequest(n => n + 1)}>Add note</Button>}
            />
            <LibraryTabs />

                <AnalystNotesSection
                    hideHeading
                    createRequest={createRequest}
                    onShowToast={showToast}
                    openNoteId={noteId}
                    onViewClose={() => { if (noteId) navigate('/notes', { replace: true }); }}
                />
            {toast && <div className="notes-page__toast" role="status">{toast}</div>}
        </PageLayout>
    );
};

export default NotesPage;
