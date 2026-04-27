import React from 'react';
import { getSettings } from '../services/api';

const RouterPage: React.FC = () => {
  const settings = getSettings();

  return (
    <div className="p-8">
      <h2 className="text-2xl font-bold mb-6 text-purple-400">Router</h2>
      <div className="bg-gray-800 rounded-lg p-6 border border-gray-700 space-y-4">
        <div>
          <h3 className="text-lg font-medium mb-2 text-gray-300">Router Configuration</h3>
          <p className="text-gray-400">Router endpoint: {settings.routerBaseUrl}</p>
          <p className="text-sm text-gray-500 mt-1">
            Router functionality will be implemented in a future milestone.
          </p>
        </div>
        <div>
          <h3 className="text-lg font-medium mb-2 text-gray-300">API Endpoints</h3>
          <ul className="space-y-2 text-gray-400">
            <li>• GET {settings.routerBaseUrl}/status</li>
            <li>• POST {settings.routerBaseUrl}/route</li>
            <li>• GET {settings.routerBaseUrl}/routes</li>
          </ul>
        </div>
      </div>
    </div>
  );
};

export default RouterPage;
