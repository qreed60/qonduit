import React, { useState, useEffect } from 'react';
import { Settings } from '../types';
import { getSettings, saveSettings } from '../services/api';

const SettingsPage: React.FC = () => {
  const [formData, setFormData] = useState<Settings>(getSettings());

  useEffect(() => {
    setFormData(getSettings());
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData((prev: Settings) => ({
      ...prev,
      [name]: value,
    }));
  };

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    saveSettings(formData);
    alert('Settings saved successfully!');
  };

  const handleReset = () => {
    const defaults = getSettings();
    setFormData(defaults);
  };

  return (
    <div className="p-8">
      <h2 className="text-2xl font-bold mb-6 text-purple-400">Settings</h2>
      <form onSubmit={handleSave} className="max-w-2xl">
        <div className="bg-gray-800 rounded-lg p-6 border border-gray-700 space-y-6">
          <div>
            <h3 className="text-lg font-medium mb-4 text-gray-300">API Endpoints</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  Gateway Base URL
                </label>
                <input
                  type="text"
                  name="gatewayBaseUrl"
                  value={formData.gatewayBaseUrl}
                  onChange={handleChange}
                  className="w-full px-4 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-200 focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                  placeholder="http://192.168.5.5:8090"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  Direct Base URL
                </label>
                <input
                  type="text"
                  name="directBaseUrl"
                  value={formData.directBaseUrl}
                  onChange={handleChange}
                  className="w-full px-4 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-200 focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                  placeholder="http://192.168.5.5:8080"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  Router Base URL
                </label>
                <input
                  type="text"
                  name="routerBaseUrl"
                  value={formData.routerBaseUrl}
                  onChange={handleChange}
                  className="w-full px-4 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-200 focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                  placeholder="http://192.168.5.5:8090"
                />
              </div>
            </div>
          </div>

          <div>
            <h3 className="text-lg font-medium mb-4 text-gray-300">Default Configuration</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  Default Provider
                </label>
                <select
                  name="defaultProvider"
                  value={formData.defaultProvider}
                  onChange={handleChange}
                  className="w-full px-4 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-200 focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                >
                  <option value="Direct">Direct</option>
                  <option value="Gateway">Gateway</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  Default Model
                </label>
                <input
                  type="text"
                  name="defaultModel"
                  value={formData.defaultModel}
                  onChange={handleChange}
                  className="w-full px-4 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-200 focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                  placeholder="Qwen3-Coder-Next-IQ4_NL.gguf"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  API Key
                </label>
                <input
                  type="text"
                  name="apiKey"
                  value={formData.apiKey}
                  onChange={handleChange}
                  className="w-full px-4 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-200 focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                  placeholder="local"
                />
              </div>
            </div>
          </div>

          <div className="flex items-center space-x-4 pt-4 border-t border-gray-700">
            <button
              type="submit"
              className="px-6 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg font-medium transition-colors"
            >
              Save Settings
            </button>
            <button
              type="button"
              onClick={handleReset}
              className="px-6 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg font-medium transition-colors"
            >
              Reset to Defaults
            </button>
          </div>
        </div>
      </form>
    </div>
  );
};

export default SettingsPage;
