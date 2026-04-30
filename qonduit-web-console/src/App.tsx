import { useState } from 'react';
import { BrowserRouter as Router, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import StatusBar from './components/StatusBar';
import ChatPage from './pages/ChatPage';
import ModelsPage from './pages/ModelsPage';
import RouterPage from './pages/RouterPage';
import DiagnosticsPage from './pages/DiagnosticsPage';
import SettingsPage from './pages/SettingsPage';
import { Page, Settings } from './types';
import { getSettings } from './services/api';

// Navigation wrapper component to sync URL with state
function AppContent() {
  const navigate = useNavigate();
  const location = useLocation();
  const [settings] = useState<Settings>(getSettings());

  // Map current path to page state
  const getPageFromPath = (path: string): Page => {
    if (path === '/' || path === '/chat') return 'chat';
    if (path === '/models') return 'models';
    if (path === '/router') return 'router';
    if (path === '/diagnostics') return 'diagnostics';
    if (path === '/settings') return 'settings';
    return 'chat';
  };

  const currentPage = getPageFromPath(location.pathname);

  // Update URL when page changes
  const handleChangePage = (page: Page) => {
    const pathMap: Record<Page, string> = {
      chat: '/chat',
      models: '/models',
      router: '/router',
      diagnostics: '/diagnostics',
      settings: '/settings',
    };
    navigate(pathMap[page]);
  };

  return (
    <div className="flex h-screen bg-[var(--bg-primary)] text-[var(--text-primary)] transition-colors duration-300">
      <Sidebar currentPage={currentPage} onChangePage={handleChangePage} />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <StatusBar settings={settings} />
        <main className="flex-1 overflow-y-auto bg-[var(--bg-primary)]">
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/router" element={<RouterPage />} />
            <Route path="/diagnostics" element={<DiagnosticsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function App() {
  return (
    <Router>
      <AppContent />
    </Router>
  );
}

export default App;
