import React from 'react';
import { Page } from '../types';

interface SidebarProps {
  currentPage: Page;
  onChangePage: (page: Page) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ currentPage, onChangePage }) => {
  const pages: { id: Page; label: string; icon: string }[] = [
    { id: 'chat', label: 'Chat', icon: '💬' },
    { id: 'models', label: 'Models', icon: '🤖' },
    { id: 'router', label: 'Router', icon: '🌐' },
    { id: 'diagnostics', label: 'Diagnostics', icon: '🔧' },
    { id: 'settings', label: 'Settings', icon: '⚙️' },
  ];

  return (
    <aside className="w-64 h-screen bg-[var(--bg-secondary)] border-r border-[var(--border-subtle)] flex flex-col transition-all duration-300">
      {/* Logo Section */}
      <div className="p-6 border-b border-[var(--border-subtle)]">
        <div className="flex items-center space-x-3 mb-1">
          <div className="w-8 h-8 bg-gradient-to-br from-[var(--accent-primary)] to-[var(--accent-tertiary)] rounded-lg flex items-center justify-center shadow-lg shadow-[var(--accent-primary)]/20">
            <span className="text-white text-lg">⚡</span>
          </div>
          <h1 className="text-xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
            Qonduit
          </h1>
        </div>
        <p className="text-xs text-[var(--text-secondary)] ml-1">Web Console</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-4 py-6 space-y-2">
        {pages.map((page) => (
          <button
            key={page.id}
            onClick={() => onChangePage(page.id)}
            className={`w-full flex items-center space-x-3 px-4 py-3 rounded-xl transition-all duration-200 group ${
              currentPage === page.id
                ? 'bg-gradient-to-r from-[var(--accent-primary)]/20 to-[var(--accent-tertiary)]/20 border border-[var(--accent-primary)]/30 text-[var(--accent-primary)]'
                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] hover:border hover:border-[var(--border-subtle)]'
            }`}
          >
            <span className={`text-xl ${currentPage === page.id ? 'animate-pulse-subtle' : 'group-hover:scale-110 transition-transform'}`}>
              {page.icon}
            </span>
            <span className={`font-medium ${currentPage === page.id ? 'font-semibold' : ''}`}>
              {page.label}
            </span>
          </button>
        ))}
      </nav>

      {/* Version */}
      <div className="p-4 border-t border-[var(--border-subtle)]">
        <div className="flex items-center justify-between px-2 py-2 rounded-lg bg-[var(--bg-primary)]/50 border border-[var(--border-subtle)]/50">
          <span className="text-xs text-[var(--text-secondary)]">Version</span>
          <span className="text-xs font-mono text-[var(--text-tertiary)]">v0.1.0</span>
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
