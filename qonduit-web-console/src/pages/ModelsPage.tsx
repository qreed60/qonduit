import React, { useState, useEffect } from 'react';
import { Model, Settings } from '../types';
import { fetchModels, getSettings } from '../services/api';
import Toast from '../components/Toast';

const ModelsPage: React.FC = () => {
  const [settings] = useState<Settings>(getSettings());
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  useEffect(() => {
    loadModels();
  }, [settings]);

  const loadModels = async () => {
    setLoading(true);
    setError(null);
    setModels([]);

    try {
      const allModels: Model[] = [];

      // Fetch from gateway
      try {
        const gatewayModels = await fetchModels(settings.gatewayBaseUrl);
        allModels.push(...gatewayModels.map((m) => ({ ...m, id: `gateway:${m.id}` })));
      } catch (err) {
        console.log('Gateway models unavailable:', err);
      }

      // Fetch from direct
      try {
        const directModels = await fetchModels(settings.directBaseUrl);
        allModels.push(...directModels.map((m) => ({ ...m, id: `direct:${m.id}` })));
      } catch (err) {
        console.log('Direct models unavailable:', err);
      }

      setModels(allModels);
      if (allModels.length === 0) {
        setError('No models found from either gateway or direct endpoint');
      }
    } catch (err) {
      setError('Failed to load models. Check your network connection.');
    } finally {
      setLoading(false);
    }
  };

  const getProviderColor = (id: string) => {
    return id.startsWith('gateway:') ? 'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]' : 'bg-[var(--accent-secondary)]/10 text-[var(--accent-secondary)]';
  };

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
          Models
        </h2>
        <button
          onClick={loadModels}
          disabled={loading}
          className={`px-5 py-2.5 rounded-xl font-medium transition-all duration-200 ${
            loading
              ? 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] cursor-not-allowed'
              : 'bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] hover:from-[var(--accent-primary-hover)] hover:to-[var(--accent-tertiary)] text-white shadow-lg shadow-[var(--accent-primary)]/20 hover:shadow-[var(--accent-primary)]/30'
          }`}
        >
          {loading ? (
            <span className="flex items-center space-x-2">
              <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.825 3 7.938l3-2.647z"></path>
              </svg>
              <span>Loading...</span>
            </span>
          ) : (
            <span className="flex items-center space-x-2">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              <span>Refresh Models</span>
            </span>
          )}
        </button>
      </div>

      {/* Models Grid */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 space-y-4">
            <div className="w-16 h-16 rounded-full border-4 border-[var(--accent-primary)]/20 border-t-[var(--accent-primary)] animate-spin"></div>
            <p className="text-[var(--text-secondary)]">Loading models...</p>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center h-64 text-center">
            <div className="w-16 h-16 bg-[var(--status-error)]/10 rounded-full flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-[var(--status-error)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="text-[var(--status-error)] font-medium mb-2">Error</p>
            <p className="text-[var(--text-secondary)] max-w-md">{error}</p>
          </div>
        ) : models.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-center">
            <div className="w-16 h-16 bg-[var(--bg-tertiary)] rounded-full flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-[var(--text-secondary)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <p className="text-[var(--text-secondary)]">No models available</p>
            <button
              onClick={loadModels}
              className="mt-4 text-[var(--accent-primary)] hover:text-[var(--accent-primary-hover)] font-medium"
            >
              Try refreshing
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {models.map((model) => (
              <div
                key={model.id}
                className="bg-[var(--bg-card)] border border-[var(--border-primary)] rounded-xl p-5 hover:border-[var(--accent-primary)]/30 transition-all duration-200 hover:shadow-lg hover:shadow-[var(--accent-primary)]/10 group"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center space-x-2">
                    <div className={`px-2 py-1 rounded text-xs font-medium ${getProviderColor(model.id)}`}>
                      {model.id.startsWith('gateway:') ? 'Gateway' : 'Direct'}
                    </div>
                    <span className="text-xs text-[var(--text-tertiary)]">
                      {new Date(model.created * 1000).toLocaleDateString()}
                    </span>
                  </div>
                  <svg className="w-3 h-3 text-[var(--text-secondary)] opacity-0 group-hover:opacity-50 transition-opacity" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                
                <div className="bg-[var(--bg-secondary)]/50 rounded-lg p-3 border border-[var(--border-subtle)] mb-3">
                  <p className="text-sm font-mono text-[var(--text-primary)] truncate" title={model.id}>
                    {model.id}
                  </p>
                </div>
                
                <div className="flex justify-between text-sm">
                  <div>
                    <p className="text-[var(--text-secondary)] text-xs">Object</p>
                    <p className="text-[var(--text-primary)] font-medium">{model.object}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-[var(--text-secondary)] text-xs">Owned by</p>
                    <p className="text-[var(--text-primary)] font-medium">{model.owned_by}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {toastMessage && (
        <Toast
          message={toastMessage}
          type="info"
          onClose={() => setToastMessage(null)}
        />
      )}
    </div>
  );
};

export default ModelsPage;
