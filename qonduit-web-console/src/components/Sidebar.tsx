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
    <aside className="w-64 h-screen bg-gray-900 border-r border-gray-800 flex flex-col">
      <div className="p-6">
        <h1 className="text-2xl font-bold text-purple-400">Qonduit</h1>
        <p className="text-sm text-gray-400">Web Console</p>
      </div>

      <nav className="flex-1 px-4 space-y-2">
        {pages.map((page) => (
          <button
            key={page.id}
            onClick={() => onChangePage(page.id)}
            className={`w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-colors ${
              currentPage === page.id
                ? 'bg-purple-900/30 text-purple-400'
                : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
            }`}
          >
            <span className="text-xl">{page.icon}</span>
            <span className="font-medium">{page.label}</span>
          </button>
        ))}
      </nav>

      <div className="p-4 border-t border-gray-800">
        <p className="text-xs text-gray-500">v0.1.0</p>
      </div>
    </aside>
  );
};

export default Sidebar;
