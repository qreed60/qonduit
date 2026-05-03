import React, { useState, useRef, useEffect } from 'react';
import { Settings } from '../types';
import { ENDPOINTS, getMode } from '../config/endpoints';
import {
  Monitor,
  ChevronDown,
  Globe,
  Server,
  Zap,
  Router,
  LayoutDashboard,
} from 'lucide-react';

interface StatusBarProps {
  settings: Settings;
  onGoToDashboard?: () => void;
}

const StatusBar: React.FC<StatusBarProps> = ({ settings, onGoToDashboard }) => {
  const [showEndpoints, setShowEndpoints] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const mode = getMode();

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowEndpoints(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const modeLabel = mode === 'public' ? 'Public' : 'Local';
  const modeColor = mode === 'public'
    ? 'bg-accent-primary/10 text-accent-primary border-accent-primary/20'
    : 'bg-bg-tertiary text-text-tertiary border-border-subtle';

  const endpointList = [
    { key: 'gateway' as const, label: 'Gateway', icon: Globe, color: 'text-status-success' },
    { key: 'llama' as const, label: 'Direct', icon: Zap, color: 'text-status-success' },
    { key: 'router' as const, label: 'Router', icon: Router, color: 'text-status-success' },
    { key: 'webui' as const, label: 'WebUI', icon: Monitor, color: 'text-text-tertiary' },
  ];

  return (
    <header className="h-14 bg-bg-secondary border-b border-border-subtle flex items-center px-4 space-x-3 flex-shrink-0">
      {/* Dashboard CTA */}
      {onGoToDashboard && (
        <button
          onClick={onGoToDashboard}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-all duration-200"
        >
          <LayoutDashboard className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Dashboard</span>
        </button>
      )}

      {/* Mode Badge */}
      <div className="flex items-center space-x-2 flex-shrink-0">
        <span className={`px-2.5 py-1 rounded-lg text-xs font-semibold border ${modeColor}`}>
          {modeLabel}
        </span>
      </div>

      {/* Provider Pill */}
      <div className="flex items-center space-x-1.5 flex-shrink-0">
        <div className="px-2.5 py-1 rounded-lg text-xs font-medium bg-accent-primary/10 text-accent-primary border border-accent-primary/20">
          {settings.defaultProvider}
        </div>
      </div>

      {/* Model Pill */}
      <div className="flex items-center space-x-1.5 flex-shrink-0">
        <div className="px-2.5 py-1 rounded-lg text-xs font-medium bg-bg-tertiary text-text-primary border border-border-subtle truncate max-w-[160px]" title={settings.defaultModel}>
          {settings.defaultModel}
        </div>
      </div>

      {/* Endpoints Dropdown */}
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => setShowEndpoints(!showEndpoints)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-all duration-200 border border-border-subtle"
        >
          <Server className="w-3.5 h-3.5" />
          <span className="hidden md:inline">Endpoints</span>
          <ChevronDown className={`w-3 h-3 transition-transform duration-200 ${showEndpoints ? 'rotate-180' : ''}`} />
        </button>

        {showEndpoints && (
          <div className="absolute right-0 top-full mt-2 w-72 bg-bg-card border border-border-primary rounded-xl shadow-xl z-50 overflow-hidden animate-fade-in">
            <div className="p-3 border-b border-border-subtle">
              <p className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Service Endpoints</p>
            </div>
            <div className="p-2 space-y-1">
              {endpointList.map(({ key, label, icon: Icon, color }) => (
                <div
                  key={key}
                  className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-bg-tertiary transition-colors cursor-pointer group"
                  onClick={() => {
                    navigator.clipboard.writeText(ENDPOINTS[key][mode]);
                    setShowEndpoints(false);
                  }}
                  title={`Click to copy ${label} URL`}
                >
                  <Icon className={`w-4 h-4 flex-shrink-0 ${color}`} />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-text-primary">{label}</p>
                    <p className="text-[10px] font-mono text-text-tertiary truncate">{ENDPOINTS[key][mode]}</p>
                  </div>
                  <span className="opacity-0 group-hover:opacity-100 text-text-tertiary transition-opacity">
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Quick Actions */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <a
          href={ENDPOINTS.webui[mode]}
          target="_blank"
          rel="noopener noreferrer"
          className="p-1.5 rounded-lg text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary transition-all duration-200"
          title="Open WebUI"
        >
          <Globe className="w-4 h-4" />
        </a>
      </div>
    </header>
  );
};

export default StatusBar;
