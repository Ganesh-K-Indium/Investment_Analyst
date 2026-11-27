import React, { lazy, Suspense, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Login } from './pages/Login';
import { Home } from './pages/Home';
import { DataSources } from './pages/DataSources';
import { Repository } from './pages/Repository';
import { DocumentsPage } from './features/documents/DocumentsPage';
const InspectorPage = lazy(() => import('./features/documents/InspectorPage'));
import { NotesPage } from './features/notes/NotesPage';
import { MyDocumentsPage } from './features/userDocs/MyDocumentsPage';
import { UserDocPage } from './features/userDocs/UserDocPage';
import { SettingsPage } from './features/settings/SettingsPage';
import { CrossPortfolioDashboard } from './pages/CrossPortfolioDashboard';
import { AuthProvider } from './context/AuthContext';
import { useAuth } from './context/useAuth';
import { ChatCacheProvider } from './context/ChatCacheContext';
import { ThemeProvider } from './context/ThemeContext';
import { openUserAnalysisStream } from './store/analysisRunsStore';
import './index.css';

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { isAuthenticated, isLoading } = useAuth();
    if (isLoading) return null;
    return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />;
};

function AppRoutes() {
    const { isAuthenticated, isLoading, userId } = useAuth();

    useEffect(() => {
        if (!isAuthenticated || !userId) return;
        return openUserAnalysisStream(userId);
    }, [isAuthenticated, userId]);

    if (isLoading) return null;

    return (
        <Routes>
            <Route path="/"      element={isAuthenticated ? <Navigate to="/home" replace /> : <Navigate to="/login" replace />} />
            <Route path="/login" element={isAuthenticated ? <Navigate to="/home" replace /> : <Login />} />
            <Route path="/home"          element={<ProtectedRoute><Home /></ProtectedRoute>} />
            <Route path="/repository"    element={<ProtectedRoute><Repository /></ProtectedRoute>} />
            <Route path="/documents"         element={<ProtectedRoute><DocumentsPage /></ProtectedRoute>} />
            <Route path="/documents/:docId"  element={<ProtectedRoute><Suspense fallback={null}><InspectorPage /></Suspense></ProtectedRoute>} />
            <Route path="/documents/mine"      element={<ProtectedRoute><MyDocumentsPage /></ProtectedRoute>} />
            <Route path="/documents/mine/:id"  element={<ProtectedRoute><UserDocPage /></ProtectedRoute>} />
            <Route path="/my-documents"        element={<Navigate to="/documents/mine" replace />} />
            <Route path="/settings"          element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
            <Route path="/notes"             element={<ProtectedRoute><NotesPage /></ProtectedRoute>} />
            <Route path="/notes/:noteId"     element={<ProtectedRoute><NotesPage /></ProtectedRoute>} />
            <Route path="/portfolio"     element={<Navigate to="/home" replace />} />
            <Route path="/agent-configuration" element={<Navigate to="/data-sources" replace />} />
            <Route path="/integrations"        element={<Navigate to="/data-sources" replace />} />
            <Route path="/data-sources"        element={<ProtectedRoute><DataSources /></ProtectedRoute>} />
            <Route path="/dashboard"           element={<ProtectedRoute><CrossPortfolioDashboard /></ProtectedRoute>} />
            <Route path="/dashboard/ticker/:ticker" element={<ProtectedRoute><CrossPortfolioDashboard /></ProtectedRoute>} />
            <Route path="/terms"   element={<ProtectedRoute><Home /></ProtectedRoute>} />
            <Route path="/privacy" element={<ProtectedRoute><Home /></ProtectedRoute>} />
            <Route path="/support" element={<ProtectedRoute><Home /></ProtectedRoute>} />
            <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
    );
}

function App() {
    return (
        <ThemeProvider>
            <AuthProvider>
                <ChatCacheProvider>
                    <BrowserRouter>
                        <AppRoutes />
                    </BrowserRouter>
                </ChatCacheProvider>
            </AuthProvider>
        </ThemeProvider>
    );
}

export default App;
