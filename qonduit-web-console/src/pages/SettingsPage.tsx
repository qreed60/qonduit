import React, { useState, useEffect } from 'react';
import { Settings } from '../types';
import { getSettings, saveSettings } from '../services/api';
import { ENDPOINTS, getMode, setMode } from '../config/endpoints';
import Toast from '../components/Toast';

const SettingsPage: React.FC = () => {
  const [formData, setFormData] = useState<Settings>(getSettings());
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);

  useEffect(() => {
    setFormData(getSettings());
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData((prev: Settings) => ({
      ...prev,
      [name]: value,
    }));
    setIsDirty(true);
  };

  const handleModeChange = (mode: 'local' | 'public') => {
    setMode(mode);
    setIsDirty(true);
  };

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    saveSettings(formData);
    setIsDirty(false);
    setToastMessage('Settings saved successfully!');
    setTimeout(() => setToastMessage(null), 3000);
  };

  const handleReset = () => {
    const defaults = getSettings();
    setFormData(defaults);
    setIsDirty(false);
  };

  const currentMode = getMode();

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
          Settings
        </h2>
        <p className="text-[var(--text-secondary)] mt-2">
          Configure endpoint mode and default model settings
        </p>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        <form onSubmit={handleSave} className="max-w-4xl">
          <div className="space-y-6">
            {/* Endpoint Mode Card */}
            <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-[var(--text-primary)]">Endpoint Mode</h3>
                <div className="flex items-center space-x-2">
                  <span className="text-xs text-[var(--text-secondary)]">Mode</span>
                  <span className={`px-2 py-1 rounded-lg text-xs font-medium ${
                    currentMode === 'public'
                      ? 'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20'
                      : 'bg-[var(--bg-tertiary)] text-[var(--text-primary)] border border-[var(--border-primary)]'
                  }`}>
                    {currentMode === 'public' ? 'Public' : 'Local'}
                  </span>
                </div>
              </div>
              <p className="text-sm text-[var(--text-secondary)] mb-4">
                Choose whether to connect to local services or the public Qonduit endpoints.
                <span className="block text-xs mt-1 text-[var(--text-tertiary)]">
                  The Router API requires local network access and may not work in public mode.
                </span>
              </p>
              <div className="flex space-x-4">
                <button
                  type="button"
                  onClick={() => handleModeChange('local')}
                  className={`flex-1 px-6 py-4 rounded-xl border-2 text-left transition-all duration-200 ${
                    currentMode === 'local'
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/5'
                      : 'border-[var(--border-primary)] bg-[var(--bg-secondary)]/30 hover:border-[var(--border-primary)]/60'
                  }`}
                >
                  <div className="flex items-center space-x-3">
                    <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                      currentMode === 'local' ? 'border-[var(--accent-primary)]' : 'border-[var(--border-primary)]'
                    }`}>
                      {currentMode === 'local' && (
                        <div className="w-2 h-2 rounded-full bg-[var(--accent-primary)]" />
                      )}
                    </div>
                    <div>
                      <p className="font-medium text-[var(--text-primary)]">Local</p>
                      <p className="text-xs text-[var(--text-secondary)]">
                        {ENDPOINTS.router.local}
                      </p>
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => handleModeChange('public')}
                  className={`flex-1 px-6 py-4 rounded-xl border-2 text-left transition-all duration-200 ${
                    currentMode === 'public'
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/5'
                      : 'border-[var(--border-primary)] bg-[var(--bg-secondary)]/30 hover:border-[var(--border-primary)]/60'
                  }`}
                >
                  <div className="flex items-center space-x-3">
                    <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                      currentMode === 'public' ? 'border-[var(--accent-primary)]' : 'border-[var(--border-primary)]'
                    }`}>
                      {currentMode === 'public' && (
                        <div className="w-2 h-2 rounded-full bg-[var(--accent-primary)]" />
                      )}
                    </div>
                    <div>
                      <p className="font-medium text-[var(--text-primary)]">Public</p>
                      <p className="text-xs text-[var(--text-secondary)]">
                        {ENDPOINTS.router.public}
                      </p>
                    </div>
                  </div>
                </button>
              </div>
            </div>

            {/* Default Configuration Card */}
            <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-semibold text-[var(--text-primary)]">Default Configuration</h3>
                <div className="flex items-center space-x-2">
                  <span className="text-xs text-[var(--text-secondary)]">Defaults</span>
                </div>
              </div>
              <div className="space-y-5">
                <div>
                  <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                    Default Provider
                  </label>
                  <select
                    name="defaultProvider"
                    value={formData.defaultProvider}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 transition-all duration-200"
                  >
                    <option value="Direct">Direct</option>
                    <option value="Gateway">Gateway</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                    Default Model
                  </label>
                  <input
                    type="text"
                    name="defaultModel"
                    value={formData.defaultModel}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 transition-all duration-200"
                    placeholder="Qwen3-Coder-Next-IQ4_NL.gguf"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                    API Key
                  </label>
                  <input
                    type="password"
                    name="apiKey"
                    value={formData.apiKey}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 transition-all duration-200"
                    placeholder="local"
                  />
                </div>
              </div>
            </div>

            {/* Active Endpoints Card */}
            <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
              <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-4">Active Endpoints</h3>
              <div className="space-y-3">
                {Object.entries(ENDPOINTS).map(([key, urls]) => (
                  <div key={key} className="flex items-center justify-between p-3 bg-[var(--bg-secondary)]/30 rounded-xl border border-[var(--border-subtle)]">
                    <span className="text-sm font-medium text-[var(--text-primary)] capitalize">{key}</span>
                    <span className="text-xs font-mono text-[var(--text-secondary)]">{urls[currentMode]}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center space-x-4 pt-4 border-t border-[var(--border-primary)]">
              <button
                type="submit"
                disabled={!isDirty}
                className={`px-8 py-3 rounded-xl font-medium transition-all duration-200 ${
                  isDirty
                    ? 'bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] hover:from-[var(--accent-primary-hover)] hover:to-[var(--accent-tertiary)] text-white shadow-lg shadow-[var(--accent-primary)]/20 hover:shadow-[var(--accent-primary)]/30'
                    : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] cursor-not-allowed'
                }`}
              >
                Save Settings
              </button>
              <button
                type="button"
                onClick={handleReset}
                className="px-8 py-3 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
              >
                Reset to Defaults
              </button>
            </div>
          </div>
        </form>
      </div>

      {/* Toast Notification */}
      {toastMessage && (
        <Toast
          message={toastMessage}
          type="success"
          onClose={() => setToastMessage(null)}
        />
      )}
    </div>
  );
};

export default SettingsPage;
