import React, { useState, useEffect, useRef } from 'react';
import { Page } from '../types';
import { VERSION } from '../constants/version';
import {
  LayoutDashboard,
  MessageSquare,
  Cpu,
  Router,
  Activity,
  Settings,
  X,
  Menu,
} from 'lucide-react';

interface SidebarProps {
  currentPage: Page;
  onChangePage: (page: Page) => void;
}

const NAV_ITEMS: { id: Page; label: string; icon: React.ReactNode }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard className="w-5 h-5" /> },
  { id: 'chat', label: 'Chat', icon: <MessageSquare className="w-5 h-5" /> },
  { id: 'models', label: 'Models', icon: <Cpu className="w-5 h-5" /> },
  { id: 'router', label: 'Router', icon: <Router className="w-5 h-5" /> },
  { id: 'diagnostics', label: 'Diagnostics', icon: <Activity className="w-5 h-5" /> },
  { id: 'settings', label: 'Settings', icon: <Settings className="w-5 h-5" /> },
];

const Sidebar: React.FC<SidebarProps> = ({ currentPage, onChangePage }) => {
  const [mobileOpen, setMobileOpen] = useState(false);
  const overlayRef = useRef<HTMLDivElement>(null);

  // Close on escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMobileOpen(false);
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Prevent body scroll when mobile menu is open
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [mobileOpen]);

  const handleNavClick = (page: Page) => {
    onChangePage(page);
    setMobileOpen(false);
  };

  return (
    <>
      {/* Mobile Hamburger Button */}
      <button
        onClick={() => setMobileOpen(true)}
        className="lg:hidden fixed top-3 left-3 z-50 p-2 rounded-lg bg-bg-secondary border border-border-subtle text-text-secondary hover:text-text-primary hover:bg-bg-tertiary transition-all duration-200"
        title="Open menu"
      >
        <Menu className="w-5 h-5" />
      </button>

      {/* Mobile Overlay */}
      {mobileOpen && (
        <div
          ref={overlayRef}
          className="fixed inset-0 bg-black/50 z-40 lg:hidden animate-fade-in"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed lg:static inset-y-0 left-0 z-50 lg:z-auto w-64 h-screen bg-bg-secondary border-r border-border-subtle flex flex-col transition-transform duration-300 lg:translate-x-0 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Logo Section */}
        <div className="p-5 border-b border-border-subtle">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-gradient-to-br from-accent-primary to-accent-tertiary rounded-lg flex items-center justify-center shadow-lg shadow-accent-primary/20">
                <span className="text-white text-sm font-bold">Q</span>
              </div>
              <div>
                <h1 className="text-base font-bold bg-gradient-to-r from-accent-primary to-accent-tertiary bg-clip-text text-transparent">
                  Qonduit
                </h1>
                <p className="text-[10px] text-text-tertiary">Web Console</p>
              </div>
            </div>
            {/* Close button (mobile only) */}
            <button
              onClick={() => setMobileOpen(false)}
              className="lg:hidden p-1 rounded hover:bg-bg-tertiary text-text-tertiary hover:text-text-primary transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              onClick={() => handleNavClick(item.id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group ${
                currentPage === item.id
                  ? 'bg-accent-primary/10 text-accent-primary border border-accent-primary/20'
                  : 'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
              }`}
            >
              <span className={`flex-shrink-0 ${currentPage === item.id ? 'text-accent-primary' : 'text-text-tertiary group-hover:text-text-primary'}`}>
                {item.icon}
              </span>
              <span className="text-sm font-medium">{item.label}</span>
            </button>
          ))}
        </nav>

        {/* Version */}
        <div className="p-4 border-t border-border-subtle">
          <div className="flex items-center justify-between px-2 py-2 rounded-lg bg-bg-primary/50 border border-border-subtle/50">
            <span className="text-xs text-text-tertiary">Version</span>
            <span className="text-xs font-mono text-text-tertiary">v{VERSION}</span>
          </div>
        </div>
      </aside>
    </>
  );
};

export default Sidebar;
