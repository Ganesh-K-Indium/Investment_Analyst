import { create } from 'zustand';
import { reportApi, type ReportResponse } from '../services/reportApi';

interface ReportEditorState {
    // Which draft is currently loaded in the editor (null = new report)
    editingReportId: number | null;
    editingReportLabel: string;  // company_name + date for display

    // Cached editor HTML so user doesn't lose typed content on tab switch
    editorHtml: string;

    // Draft reports list
    draftReports: ReportResponse[];
    isDraftReportsLoading: boolean;

    // Last saved report id (for PDF download toast)
    savedReportId: number | null;

    // Actions
    startEditing: (report: ReportResponse) => void;
    stopEditing: () => void;
    setEditorHtml: (html: string) => void;
    setSavedReportId: (id: number | null) => void;
    fetchDraftReports: (userId: string, portfolioId?: string | number) => Promise<void>;
    removeDraftFromList: (reportId: number) => void;
}

export const useReportEditorStore = create<ReportEditorState>((set, _get) => ({
    editingReportId: null,
    editingReportLabel: '',
    editorHtml: '',
    draftReports: [],
    isDraftReportsLoading: false,
    savedReportId: null,

    startEditing: (report: ReportResponse) => {
        const date = report.created_at
            ? new Date(report.created_at).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
            : '';
        set({
            editingReportId: report.id,
            editingReportLabel: `${report.company_name}${report.ticker ? ` (${report.ticker})` : ''}${date ? ` · ${date}` : ''}`,
        });
    },

    stopEditing: () => {
        set({ editingReportId: null, editingReportLabel: '', editorHtml: '' });
    },

    setEditorHtml: (html) => set({ editorHtml: html }),

    setSavedReportId: (id) => set({ savedReportId: id }),

    fetchDraftReports: async (userId: string, portfolioId?: string | number) => {
        set({ isDraftReportsLoading: true });
        try {
            const res = await reportApi.getUserReports(userId, 'draft', portfolioId);
            set({ draftReports: res.items });
        } catch {
            /* silent */
        } finally {
            set({ isDraftReportsLoading: false });
        }
    },

    removeDraftFromList: (reportId: number) => {
        set(state => ({
            draftReports: state.draftReports.filter(r => r.id !== reportId),
        }));
    },
}));
