import React, { useState, useEffect } from 'react';
import { Settings, EndpointOverrides } from '../types';
import { getSettings, saveSettings } from '../services/api';
import { ENDPOINTS, getMode, setMode, setEndpointOverride, clearEndpointOverrides, hasEndpointOverride, validateEndpoint } from '../config/endpoints';
import Toast from '../components/Toast';

const SettingsPage: React.FC = () => {
  const [formData, setFormData] = useState<Settings>(getSettings());
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  const [overrides, setOverrides] = useState<EndpointOverrides>({});
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setFormData(getSettings());
    // Load runtime overrides
    try {
      const raw = localStorage.getItem('qonduit-endpoint-overrides');
      if (raw) setOverrides(JSON.parse(raw));
    } catch { /* ignore */ }
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
 
   const handleOverrideChange = (key: string, value: string) => {
     setOverrides((prev) => ({ ...prev, [key]: value }));
     // Validate in real-time
     const result = validateEndpoint(value);
     setValidationErrors((prev) => {
       const next = { ...prev };
       if (value && !result.valid) next[key] = result.error || '';
       else delete next[key];
       return next;
     });
   };
 
   const handleApplyOverrides = () => {
     for (const [key, value] of Object.entries(overrides)) {
       if (value) {
         const result = validateEndpoint(value);
         if (!result.valid) {
           setToastMessage(`Invalid ${key} endpoint: ${result.error}`);
           setTimeout(() => setToastMessage(null), 4000);
           return;
         }
         setEndpointOverride(key as 'gateway' | 'router' | 'llama' | 'webui', value);
       }
     }
     setToastMessage('Endpoint overrides applied — page will refresh');
     setTimeout(() => {
       setToastMessage(null);
       window.location.reload();
     }, 2000);
   };
 
   const handleClearOverrides = () => {
     clearEndpointOverrides();
     setOverrides({});
     setValidationErrors({});
     setToastMessage('Endpoint overrides cleared — page will refresh');
     setTimeout(() => {
       setToastMessage(null);
       window.location.reload();
     }, 2000);
   };
 
   const currentMode = getMode();

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold bg-gradient-to-r from-accent-primary to-accent-tertiary bg-clip-text text-transparent">
          Settings
        </h2>
        <p className="text-text-secondary mt-2">
          Configure endpoint mode and default model settings
        </p>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        <form onSubmit={handleSave} className="max-w-4xl">
          <div className="space-y-6">
            {/* Endpoint Mode Card */}
            <div className="bg-bg-card rounded-2xl border border-border-primary p-6 shadow-card">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-text-primary">Endpoint Mode</h3>
                <div className="flex items-center space-x-2">
                  <span className="text-xs text-text-secondary">Mode</span>
                  <span className={`px-2 py-1 rounded-lg text-xs font-medium ${
                    currentMode === 'public'
                      ? 'bg-accent-primary/10 text-accent-primary border border-accent-primary/20'
                      : 'bg-bg-tertiary text-text-primary border border-border-primary'
                  }`}>
                    {currentMode === 'public' ? 'Public' : 'Local'}
                  </span>
                </div>
              </div>
              <p className="text-sm text-text-secondary mb-4">
                Choose whether to connect to local services or the public Qonduit endpoints.
                <span className="block text-xs mt-1 text-text-tertiary">
                  The Router API requires local network access and may not work in public mode.
                </span>
              </p>
              <div className="flex space-x-4">
                <button
                  type="button"
                  onClick={() => handleModeChange('local')}
                  className={`flex-1 px-6 py-4 rounded-xl border-2 text-left transition-all duration-200 ${
                    currentMode === 'local'
                      ? 'border-accent-primary bg-accent-primary/5'
                      : 'border-border-primary bg-bg-secondary/30 hover:border-border-primary/60'
                  }`}
                >
                  <div className="flex items-center space-x-3">
                    <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                      currentMode === 'local' ? 'border-accent-primary' : 'border-border-primary'
                    }`}>
                      {currentMode === 'local' && (
                        <div className="w-2 h-2 rounded-full bg-accent-primary" />
                      )}
                    </div>
                    <div>
                      <p className="font-medium text-text-primary">Local</p>
                      <p className="text-xs text-text-secondary">
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
                      ? 'border-accent-primary bg-accent-primary/5'
                      : 'border-border-primary bg-bg-secondary/30 hover:border-border-primary/60'
                  }`}
                >
                  <div className="flex items-center space-x-3">
                    <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                      currentMode === 'public' ? 'border-accent-primary' : 'border-border-primary'
                    }`}>
                      {currentMode === 'public' && (
                        <div className="w-2 h-2 rounded-full bg-accent-primary" />
                      )}
                    </div>
                    <div>
                      <p className="font-medium text-text-primary">Public</p>
                      <p className="text-xs text-text-secondary">
                        {ENDPOINTS.router.public}
                      </p>
                    </div>
                  </div>
                </button>
              </div>
            </div>

            {/* Default Configuration Card */}
            <div className="bg-bg-card rounded-2xl border border-border-primary p-6 shadow-card">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-semibold text-text-primary">Default Configuration</h3>
                <div className="flex items-center space-x-2">
                  <span className="text-xs text-text-secondary">Defaults</span>
                </div>
              </div>
              <div className="space-y-5">
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-2">
                    Default Provider
                  </label>
                  <select
                    name="defaultProvider"
                    value={formData.defaultProvider}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-bg-secondary border border-border-primary rounded-xl text-text-primary focus:outline-none focus:border-accent-primary/50 focus:ring-1 focus:ring-accent-primary/50 transition-all duration-200"
                  >
                    <option value="Direct">Direct</option>
                    <option value="Gateway">Gateway</option>
                    <option value="Router">Router</option>
                    <option value="WebUI">WebUI</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-2">
                    Default Model
                  </label>
                  <input
                    type="text"
                    name="defaultModel"
                    value={formData.defaultModel}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-bg-secondary border border-border-primary rounded-xl text-text-primary placeholder-text-tertiary focus:outline-none focus:border-accent-primary/50 focus:ring-1 focus:ring-accent-primary/50 transition-all duration-200"
                    placeholder="Qwen3-Coder-Next-IQ4_NL.gguf"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-2">
                    API Key
                  </label>
                  <input
                    type="password"
                    name="apiKey"
                    value={formData.apiKey}
                    onChange={handleChange}
                    className="w-full px-5 py-3 bg-bg-secondary border border-border-primary rounded-xl text-text-primary placeholder-text-tertiary focus:outline-none focus:border-accent-primary/50 focus:ring-1 focus:ring-accent-primary/50 transition-all duration-200"
                    placeholder="local"
                  />
                </div>
              </div>
            </div>

            {/* Runtime Endpoint Overrides Card */}
             <div className="bg-bg-card rounded-2xl border border-border-primary p-6 shadow-card">
               <div className="flex items-center justify-between mb-4">
                 <div>
                   <h3 className="text-lg font-semibold text-text-primary">Runtime Endpoint Overrides</h3>
                   <p className="text-sm text-text-secondary mt-1">
                     Override endpoints without rebuilding. Changes apply after reload.
                   </p>
                 </div>
                 <button
                   type="button"
                   onClick={handleClearOverrides}
                   className="px-4 py-2 rounded-lg text-xs font-medium border border-border-primary text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-all duration-200"
                 >
                   Clear All
                 </button>
               </div>
               <div className="space-y-3">
                 {(Object.keys(ENDPOINTS) as Array<keyof typeof ENDPOINTS>).map((key) => {
                   const currentValue = overrides[key] || ENDPOINTS[key][currentMode];
                   const hasOverride = hasEndpointOverride(key);
                   const error = validationErrors[key];
                   return (
                     <div key={key} className="flex items-center gap-3">
                       <span className="text-sm font-medium text-text-secondary w-20 capitalize">{key}</span>
                       <input
                         type="text"
                         value={currentValue}
                         onChange={(e) => handleOverrideChange(key, e.target.value)}
                         placeholder={`http://...`}
                         className={`flex-1 px-4 py-2.5 bg-bg-secondary border rounded-xl text-text-primary text-sm font-mono focus:outline-none focus:ring-1 transition-all duration-200 ${
                           error
                             ? 'border-status-error/50 focus:border-status-error/50 focus:ring-status-error/50'
                             : hasOverride
                             ? 'border-accent-primary/50 focus:border-accent-primary/50 focus:ring-accent-primary/50'
                             : 'border-border-primary focus:border-accent-primary/50 focus:ring-accent-primary/50'
                         }`}
                       />
                       {hasOverride && (
                         <span className="text-xs text-accent-primary font-medium">Override</span>
                       )}
                     </div>
                   );
                 })}
               </div>
               {Object.keys(validationErrors).length > 0 && (
                 <div className="mt-3 space-y-1">
                   {Object.entries(validationErrors).map(([key, error]) => (
                     <p key={key} className="text-xs text-status-error">{key}: {error}</p>
                   ))}
                 </div>
               )}
               <div className="mt-4 flex gap-3">
                 <button
                   type="button"
                   onClick={handleApplyOverrides}
                   className="px-6 py-2.5 rounded-xl font-medium bg-gradient-to-r from-accent-primary to-accent-tertiary hover:from-accent-primary-hover hover:to-accent-tertiary text-white shadow-lg shadow-accent-primary/20 transition-all duration-200"
                 >
                   Apply &amp; Reload
                 </button>
               </div>
             </div>
 
             {/* Active Endpoints Card */}
             <div className="bg-bg-card rounded-2xl border border-border-primary p-6 shadow-card">
               <h3 className="text-lg font-semibold text-text-primary mb-4">Active Endpoints</h3>
              <div className="space-y-3">
                {Object.entries(ENDPOINTS).map(([key, urls]) => (
                  <div key={key} className="flex items-center justify-between p-3 bg-bg-secondary/30 rounded-xl border border-border-subtle">
                    <span className="text-sm font-medium text-text-primary capitalize">{key}</span>
                    <span className="text-xs font-mono text-text-secondary">{urls[currentMode]}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center space-x-4 pt-4 border-t border-border-primary">
              <button
                type="submit"
                disabled={!isDirty}
                className={`px-8 py-3 rounded-xl font-medium transition-all duration-200 ${
                  isDirty
                    ? 'bg-gradient-to-r from-accent-primary to-accent-tertiary hover:from-accent-primary-hover hover:to-accent-tertiary text-white shadow-lg shadow-accent-primary/20 hover:shadow-accent-primary/30'
                    : 'bg-bg-tertiary text-text-secondary cursor-not-allowed'
                }`}
              >
                Save Settings
              </button>
              <button
                type="button"
                onClick={handleReset}
                className="px-8 py-3 bg-bg-secondary border border-border-primary rounded-xl text-text-primary hover:bg-bg-tertiary transition-colors"
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
