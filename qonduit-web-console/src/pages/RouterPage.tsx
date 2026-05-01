import React from 'react';
import { getSettings } from '../services/api';

const RouterPage: React.FC = () => {
  const settings = getSettings();

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
          Router
        </h2>
        <p className="text-[var(--text-secondary)] mt-2">
          Intelligent model routing and request optimization
        </p>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        {/* Router Status Card */}
        <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 mb-6 shadow-lg shadow-black/20">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-[var(--text-primary)]">Router Status</h3>
            <div className="flex items-center space-x-2">
              <div style={{ width: '8px', height: '8px', backgroundColor: 'var(--status-success)', borderRadius: '50%' }} className="animate-pulse"></div>
              <span className="text-sm text-[var(--status-success)] font-medium">Active</span>
            </div>
          </div>
          <div className="bg-[var(--bg-secondary)]/50 rounded-xl p-4 border border-[var(--border-subtle)]">
            <p className="text-sm text-[var(--text-secondary)] mb-1">Router Endpoint</p>
            <p className="text-sm font-mono text-[var(--text-primary)] break-all">{settings.routerBaseUrl}</p>
          </div>
        </div>

        {/* Active Model Card */}
        <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 mb-6 shadow-lg shadow-black/20">
          <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-4">Active Model</h3>
          <div className="flex items-center justify-between bg-[var(--bg-secondary)]/50 rounded-xl p-4 border border-[var(--border-subtle)]">
            <div>
              <p className="text-sm text-[var(--text-secondary)] mb-1">Current Model</p>
              <p className="text-[var(--text-primary)] font-medium truncate max-w-xs" title={settings.defaultModel}>
                {settings.defaultModel}
              </p>
            </div>
            <button
              disabled
              className="px-4 py-2 bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20 rounded-lg text-sm font-medium disabled:cursor-not-allowed hover:bg-[var(--accent-primary)]/20 transition-colors"
            >
              View Details
            </button>
          </div>
        </div>

        {/* Switch Model Card */}
        <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 mb-6 shadow-lg shadow-black/20">
          <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-4">Switch Model</h3>
          <p className="text-sm text-[var(--text-secondary)] mb-4">
            Select a different model to route requests through the router
          </p>
          <div className="bg-[var(--bg-secondary)]/50 rounded-xl p-4 border border-[var(--border-subtle)]">
            <select
              disabled
              className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded-lg px-4 py-3 text-[var(--text-primary)] focus:outline-none disabled:opacity-50 cursor-not-allowed"
            >
              <option>Qwen3-Coder-Next-IQ4_NL.gguf</option>
              <option value="other">Other Models (Not Available)</option>
            </select>
            <p className="text-xs text-[var(--text-tertiary)] mt-3">
              Model switching functionality will be implemented in a future milestone
            </p>
          </div>
        </div>

        {/* Lifecycle Controls Card */}
        <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
          <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-4">Lifecycle Controls</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <button
              disabled
              className="flex items-center justify-center space-x-3 px-6 py-3 bg-[var(--bg-secondary)]/50 border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] disabled:cursor-not-allowed transition-colors"
            >
              <svg style={{ width: '20px', height: '20px' }} className="text-[var(--accent-primary)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span className="font-medium">Start Router</span>
            </button>
            <button
              disabled
              className="flex items-center justify-center space-x-3 px-6 py-3 bg-[var(--bg-secondary)]/50 border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] disabled:cursor-not-allowed transition-colors"
            >
              <svg style={{ width: '20px', height: '20px' }} className="text-[var(--status-error)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
              </svg>
              <span className="font-medium">Stop Router</span>
            </button>
          </div>
          <p className="text-xs text-[var(--text-tertiary)] mt-4 text-center">
            Router lifecycle controls will be implemented in a future milestone
          </p>
        </div>
      </div>
    </div>
  );
};

export default RouterPage;
