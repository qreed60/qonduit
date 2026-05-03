import React from 'react';
import { ENDPOINTS } from '../config/endpoints';

const ChatPage: React.FC = () => {
  const mode: 'local' | 'public' = 'local';

  const quickActions = [
    { icon: '🔀', label: 'Route Request', desc: 'Send a request through the smart router' },
    { icon: '⚡', label: 'Direct Query', desc: 'Connect directly to llama.cpp' },
    { icon: '🌐', label: 'Gateway Call', desc: 'Use the memory gateway for enhanced AI' },
    { icon: '📊', label: 'View Dashboard', desc: 'Check system health and model status' },
  ];

  return (
    <div className="flex flex-col h-full p-6">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
          Chat
        </h2>
        <p className="text-[var(--text-secondary)] mt-2">
          Interact with your AI models through intelligent routing
        </p>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        {/* Chat Area Placeholder */}
        <div className="flex-1 flex items-center justify-center mb-6">
          <div className="max-w-lg w-full text-center">
            {/* Hero Icon */}
            <div className="w-20 h-20 mx-auto mb-6 bg-gradient-to-br from-[var(--accent-primary)]/20 to-[var(--accent-tertiary)]/20 rounded-3xl flex items-center justify-center border border-[var(--accent-primary)]/20 shadow-lg shadow-[var(--accent-primary)]/10">
              <span className="text-5xl">💬</span>
            </div>
            
            <h3 className="text-xl font-semibold mb-3 text-[var(--text-primary)]">
              Ready to chat
            </h3>
            <p className="text-[var(--text-secondary)] leading-relaxed mb-8">
              Qonduit provides intelligent routing for your AI model requests. 
              Choose a quick action below or wait for chat to become available.
            </p>

            {/* Endpoint Info */}
            <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-5 mb-8 shadow-lg shadow-black/20">
              <p className="text-sm font-medium text-[var(--text-secondary)] mb-4">
                Active Endpoints
              </p>
              <div className="grid grid-cols-3 gap-3">
                {[
                  { name: 'Gateway', url: ENDPOINTS.gateway[mode], icon: '🌐' },
                  { name: 'Direct', url: ENDPOINTS.llama[mode], icon: '⚡' },
                  { name: 'Router', url: ENDPOINTS.router[mode], icon: '🔀' },
                ].map((ep) => (
                  <div
                    key={ep.name}
                    className="bg-[var(--bg-secondary)]/50 rounded-xl p-3 border border-[var(--border-subtle)]"
                  >
                    <div className="text-lg mb-1">{ep.icon}</div>
                    <p className="text-xs font-medium text-[var(--text-primary)]">{ep.name}</p>
                    <p className="text-[10px] font-mono text-[var(--text-tertiary)] truncate" title={ep.url}>
                      {ep.url}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Quick Actions Grid */}
        <div className="mb-6">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">Quick Actions</h3>
          <div className="grid grid-cols-2 gap-3">
            {quickActions.map((action) => (
              <button
                key={action.label}
                disabled
                className="flex items-center space-x-3 p-4 bg-[var(--bg-card)] rounded-xl border border-[var(--border-primary)] hover:border-[var(--accent-primary)]/30 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 text-left group"
              >
                <div className="w-10 h-10 bg-[var(--bg-secondary)] rounded-lg flex items-center justify-center border border-[var(--border-subtle)] group-hover:border-[var(--accent-primary)]/30 transition-colors">
                  <span className="text-lg">{action.icon}</span>
                </div>
                <div>
                  <p className="text-sm font-medium text-[var(--text-primary)]">{action.label}</p>
                  <p className="text-xs text-[var(--text-tertiary)]">{action.desc}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Input Area */}
        <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-4 shadow-lg shadow-black/20">
          <div className="flex space-x-4">
            <textarea
              placeholder="Type your message... (coming soon)"
              className="flex-1 bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl px-4 py-3 text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 resize-none h-20 disabled:opacity-50"
              disabled
            />
            <button
              disabled
              className="px-6 py-3 bg-[var(--accent-primary)]/30 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20 rounded-xl font-medium disabled:cursor-not-allowed hover:bg-[var(--accent-primary)]/50 transition-colors min-w-[100px] flex items-center justify-center"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </button>
          </div>
          <p className="text-xs text-[var(--text-tertiary)] mt-3 text-center">
            Chat functionality coming in a future milestone
          </p>
        </div>
      </div>
    </div>
  );
};

export default ChatPage;
