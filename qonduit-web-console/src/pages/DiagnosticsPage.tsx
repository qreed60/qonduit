import React, { useState, useEffect } from 'react';
import { testConnection, getSettings } from '../services/api';

const DiagnosticsPage: React.FC = () => {
  const settings = getSettings();
  const [connectionStatus, setConnectionStatus] = useState<{
    gateway: boolean | null;
    direct: boolean | null;
    router: boolean | null;
  }>({ gateway: null, direct: null, router: null });

  useEffect(() => {
    testConnections();
  }, [settings]);

  const testConnections = async () => {
    const gateway = await testConnection(settings.gatewayBaseUrl);
    const direct = await testConnection(settings.directBaseUrl);
    const router = await testConnection(settings.routerBaseUrl);

    setConnectionStatus({ gateway, direct, router });
  };

  const getStatusIcon = (status: boolean | null) => {
    switch (status) {
      case true:
        return <span className="text-green-400">●</span>;
      case false:
        return <span className="text-red-400">●</span>;
      default:
        return <span className="text-gray-500">○</span>;
    }
  };

  return (
    <div className="p-8">
      <h2 className="text-2xl font-bold mb-6 text-purple-400">Diagnostics</h2>
      <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
        <h3 className="text-lg font-medium mb-4 text-gray-300">Service Connectivity</h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between p-3 bg-gray-700/30 rounded-lg">
            <span className="text-gray-200">Gateway ({settings.gatewayBaseUrl})</span>
            <span className="font-mono">{getStatusIcon(connectionStatus.gateway)}</span>
          </div>
          <div className="flex items-center justify-between p-3 bg-gray-700/30 rounded-lg">
            <span className="text-gray-200">Direct ({settings.directBaseUrl})</span>
            <span className="font-mono">{getStatusIcon(connectionStatus.direct)}</span>
          </div>
          <div className="flex items-center justify-between p-3 bg-gray-700/30 rounded-lg">
            <span className="text-gray-200">Router ({settings.routerBaseUrl})</span>
            <span className="font-mono">{getStatusIcon(connectionStatus.router)}</span>
          </div>
        </div>
        <button
          onClick={testConnections}
          className="mt-6 px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg font-medium transition-colors"
        >
          Test Connections
        </button>
      </div>
    </div>
  );
};

export default DiagnosticsPage;
