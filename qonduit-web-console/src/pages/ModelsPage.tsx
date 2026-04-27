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

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-purple-400">Models</h2>
        <button
          onClick={loadModels}
          disabled={loading}
          className={`px-4 py-2 rounded-lg font-medium transition-colors ${
            loading
              ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
              : 'bg-purple-600 hover:bg-purple-700 text-white'
          }`}
        >
          {loading ? 'Loading...' : 'Refresh Models'}
        </button>
      </div>

      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-900 border-b border-gray-700">
              <tr>
                <th className="px-6 py-3 text-left text-sm font-medium text-gray-400">ID</th>
                <th className="px-6 py-3 text-left text-sm font-medium text-gray-400">Object</th>
                <th className="px-6 py-3 text-left text-sm font-medium text-gray-400">Created</th>
                <th className="px-6 py-3 text-left text-sm font-medium text-gray-400">Owned By</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {loading ? (
                <tr>
                  <td colSpan={4} className="px-6 py-4 text-center text-gray-400">
                    Loading models...
                  </td>
                </tr>
              ) : error ? (
                <tr>
                  <td colSpan={4} className="px-6 py-4 text-center text-red-400">
                    {error}
                  </td>
                </tr>
              ) : models.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-4 text-center text-gray-400">
                    No models available
                  </td>
                </tr>
              ) : (
                models.map((model) => (
                  <tr key={model.id} className="hover:bg-gray-700/50">
                    <td className="px-6 py-4 text-sm font-medium text-gray-200">{model.id}</td>
                    <td className="px-6 py-4 text-sm text-gray-400">{model.object}</td>
                    <td className="px-6 py-4 text-sm text-gray-400">
                      {new Date(model.created * 1000).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-400">{model.owned_by}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
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
