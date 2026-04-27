import React from 'react';

const ChatPage: React.FC = () => {
  return (
    <div className="flex flex-col h-full p-6">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
          Chat
        </h2>
        <p className="text-[var(--text-secondary)] mt-2">
          Start a conversation with your AI models through Qonduit
        </p>
      </div>

      {/* Empty State Card */}
      <div className="flex-1 flex items-center justify-center">
        <div className="max-w-md w-full bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-8 text-center shadow-lg shadow-black/20">
          <div className="w-16 h-16 mx-auto mb-6 bg-gradient-to-br from-[var(--accent-primary)]/20 to-[var(--accent-tertiary)]/20 rounded-2xl flex items-center justify-center border border-[var(--accent-primary)]/30">
            <span className="text-4xl">💬</span>
          </div>
          <h3 className="text-xl font-semibold mb-3 text-[var(--text-primary)]">
            How can Qonduit help?
          </h3>
          <p className="text-[var(--text-secondary)] leading-relaxed mb-6">
            Qonduit provides intelligent routing for your AI model requests. 
            Use the Gateway for optimized routing or connect directly to your models.
          </p>
          
          <div className="bg-[var(--bg-secondary)]/50 rounded-xl p-4 border border-[var(--border-subtle)]">
            <p className="text-sm text-[var(--text-secondary)] mb-3">
              <span className="font-medium text-[var(--accent-primary)]">Current Configuration:</span>
            </p>
            <div className="text-xs space-y-2">
              <div className="flex justify-between">
                <span className="text-[var(--text-tertiary)]">Gateway:</span>
                <span className="font-mono text-[var(--text-secondary)]">
                  {localStorage.getItem('qonduit-settings') || 'N/A'}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Input Area */}
      <div className="mt-6 bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-4 shadow-lg">
        <div className="flex space-x-4">
          <textarea
            placeholder="Type your message..."
            className="flex-1 bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl px-4 py-3 text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 resize-none h-20"
            disabled
          />
          <button
            disabled
            className="px-6 py-3 bg-[var(--accent-primary)]/50 text-[var(--accent-primary)] border border-[var(--accent-primary)]/30 rounded-xl font-medium disabled:cursor-not-allowed hover:bg-[var(--accent-primary)]/70 transition-colors min-w-[100px]"
          >
            Send
          </button>
        </div>
        <p className="text-xs text-[var(--text-tertiary)] mt-3 text-center">
          Chat functionality coming in a future milestone
        </p>
      </div>
    </div>
  );
};

export default ChatPage;
