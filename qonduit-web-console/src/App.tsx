import { useState } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import StatusBar from './components/StatusBar';
import ChatPage from './pages/ChatPage';
import ModelsPage from './pages/ModelsPage';
import RouterPage from './pages/RouterPage';
import DiagnosticsPage from './pages/DiagnosticsPage';
import SettingsPage from './pages/SettingsPage';
import { Page, Settings } from './types';
import { getSettings } from './services/api';

function App() {
  const [currentPage, setCurrentPage] = useState<Page>('chat');
  const [settings] = useState<Settings>(getSettings());

  return (
    <Router>
      <div className="flex h-screen bg-[var(--bg-primary)] text-[var(--text-primary)] transition-colors duration-300">
        <Sidebar currentPage={currentPage} onChangePage={setCurrentPage} />
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
    </Router>
  );
}

export default App;
