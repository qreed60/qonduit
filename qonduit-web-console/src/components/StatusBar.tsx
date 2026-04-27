import React from 'react';
import { Settings } from '../types';

interface StatusBarProps {
  settings: Settings;
}

const StatusBar: React.FC<StatusBarProps> = ({ settings }) => {
  const getStatusColor = (url: string) => {
    try {
      const urlObj = new URL(url);
      return urlObj.hostname === '192.168.5.5' ? 'text-green-400' : 'text-yellow-400';
    } catch {
      return 'text-red-400';
    }
  };

  return (
    <header className="h-16 bg-gray-900 border-b border-gray-800 flex items-center px-6 space-x-6">
      <div className="flex items-center space-x-2">
        <span className="text-sm text-gray-400">Gateway:</span>
        <span className={`text-sm font-mono ${getStatusColor(settings.gatewayBaseUrl)}`}>
          {settings.gatewayBaseUrl}
        </span>
      </div>
      <div className="flex items-center space-x-2">
        <span className="text-sm text-gray-400">Direct:</span>
        <span className={`text-sm font-mono ${getStatusColor(settings.directBaseUrl)}`}>
          {settings.directBaseUrl}
        </span>
      </div>
      <div className="flex items-center space-x-2">
        <span className="text-sm text-gray-400">Router:</span>
        <span className={`text-sm font-mono ${getStatusColor(settings.routerBaseUrl)}`}>
          {settings.routerBaseUrl}
        </span>
      </div>
      <div className="flex items-center space-x-2">
        <span className="text-sm text-gray-400">Provider:</span>
        <span className="text-sm font-medium text-purple-400">{settings.defaultProvider}</span>
      </div>
      <div className="flex items-center space-x-2">
        <span className="text-sm text-gray-400">Model:</span>
        <span className="text-sm font-medium text-blue-400 truncate max-w-[200px]">
          {settings.defaultModel}
        </span>
      </div>
    </header>
  );
};

export default StatusBar;
