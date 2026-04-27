import React, { useState, useEffect } from 'react';
import { Settings } from '../types';
import { getSettings, saveSettings } from '../services/api';
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

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
          Settings
        </h2>
        <p className="text-[var(--text-secondary)] mt-2">
          Configure API endpoints and default model settings
        </p>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        <form onSubmit={handleSave} className="max-w-4xl">
          <div className="space-y-6">
            {/* API Endpoints Card */}
            <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-semibold text-[var(--text-primary)]">API Endpoints</h3>
                <div className="flex items-center space-x-2">
                  <span className="text-xs text-[var(--text-secondary)]">Endpoints</span>
                </div>
              </div>
              <div className="space-y-5">
                <div>
                  <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                    Gateway Base URL
                  </label>
                  <input
                    type="text"
                    name="gatewayBaseUrl"
                    value={formData.gatewayBaseUrl}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 transition-all duration-200"
                    placeholder="http://192.168.5.5:8090"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                    Direct Base URL
                  </label>
                  <input
                    type="text"
                    name="directBaseUrl"
                    value={formData.directBaseUrl}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 transition-all duration-200"
                    placeholder="http://192.168.5.5:8080"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                    Router Base URL
                  </label>
                  <input
                    type="text"
                    name="routerBaseUrl"
                    value={formData.routerBaseUrl}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 transition-all duration-200"
                    placeholder="http://192.168.5.5:8090"
                  />
                </div>
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
