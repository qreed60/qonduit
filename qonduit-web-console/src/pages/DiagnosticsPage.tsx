import React, { useState, useEffect, useRef, useCallback } from 'react';
import { testEndpoint, testRouterHealth, testWebuiEndpoint, getSettings } from '../services/api';
import { ENDPOINTS } from '../config/endpoints';
import StatusBar from '../components/StatusBar';
import {
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  Loader2,
  Activity,
} from 'lucide-react';

interface CheckResult {
  service: string;
  endpoint: string;
  status: 'online' | 'offline' | 'unknown';
  latency: number | null;
  error: string | null;
}

const DiagnosticsPage: React.FC = () => {
  const settings = getSettings();
  const [results, setResults] = useState<CheckResult[]>([]);
  const [isChecking, setIsChecking] = useState(false);
  const [lastCheck, setLastCheck] = useState<Date | null>(null);
  const [consecutiveFailures, setConsecutiveFailures] = useState<Record<string, number>>({
    gateway: 0,
    llama: 0,
    router: 0,
    webui: 0,
  });

  const initialLoadRef = useRef(true);

  const performAllChecks = useCallback(async () => {
    setIsChecking(true);
    const checks: Array<{ name: string; fn: () => Promise<boolean> }> = [
      { name: 'gateway', fn: () => testEndpoint('gateway') },
      { name: 'llama', fn: () => testEndpoint('llama') },
      { name: 'router', fn: () => testRouterHealth() },
      { name: 'webui', fn: () => testWebuiEndpoint() },
    ];

    const newResults: CheckResult[] = [];

    for (const check of checks) {
       const startTime = performance.now();
       try {
         const ok = await check.fn();
         const elapsed = Math.round(performance.now() - startTime);
         newResults.push({
           service: check.name,
           endpoint: ENDPOINTS[check.name as keyof typeof ENDPOINTS]?.local || '',
           status: ok ? 'online' : 'offline',
           latency: ok ? elapsed : null,
           error: ok ? null : 'No response',
         });
       } catch (err) {
         newResults.push({
           service: check.name,
           endpoint: ENDPOINTS[check.name as keyof typeof ENDPOINTS]?.local || '',
           status: 'offline',
           latency: null,
           error: err instanceof Error ? err.message : 'Connection failed',
         });
       }
     }

    setResults(newResults);
    setLastCheck(new Date());

    // Track consecutive failures
    setConsecutiveFailures((prev) => {
      const next = { ...prev };
      for (const r of newResults) {
        if (r.status === 'offline') {
          next[r.service] = (next[r.service] || 0) + 1;
        } else {
          next[r.service] = 0;
        }
      }
      return next;
    });

    setIsChecking(false);
  }, []);

  // Initial test on mount
  useEffect(() => {
    if (initialLoadRef.current) {
      initialLoadRef.current = false;
      performAllChecks();
    }
  }, [performAllChecks]);

  const getStatusIcon = (result: CheckResult) => {
    const hasFailures = (consecutiveFailures[result.service] || 0) > 2;
    if (result.status === 'online') {
      return <CheckCircle2 className="w-4 h-4 text-status-success" />;
    }
    if (hasFailures || result.status === 'offline') {
      return <XCircle className="w-4 h-4 text-status-error" />;
    }
    return <AlertCircle className="w-4 h-4 text-text-tertiary" />;
  };

  const getStatusText = (result: CheckResult) => {
    if (result.status === 'online') return 'Online';
    if ((consecutiveFailures[result.service] || 0) > 2) return 'Disconnected';
    return result.status === 'offline' ? 'Offline' : 'Unknown';
  };

  const getServiceLabel = (name: string) => {
    const labels: Record<string, string> = {
      gateway: 'Memory Gateway',
      llama: 'Direct (llama.cpp)',
      router: 'Router API',
      webui: 'Open WebUI',
    };
    return labels[name] || name;
  };

  return (
    <div className="flex flex-col h-screen bg-bg-primary text-text-primary">
      <StatusBar settings={settings} />
      <div className="flex-1 overflow-y-auto p-6">
        {/* Header */}
        <div className="mb-6">
          <h2 className="text-xl font-bold bg-gradient-to-r from-accent-primary to-accent-tertiary bg-clip-text text-transparent">
            Diagnostics
          </h2>
          <p className="text-sm text-text-secondary mt-1">
            Test connectivity and monitor service health
          </p>
        </div>

        {/* Test All Button */}
        <div className="mb-4 flex items-center justify-between">
          <button
            onClick={performAllChecks}
            disabled={isChecking}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-gradient-to-r from-accent-primary to-accent-tertiary hover:from-accent-primary-hover hover:to-accent-tertiary text-white shadow-lg shadow-accent-primary/20 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isChecking ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Testing...
              </>
            ) : (
              <>
                <Activity className="w-4 h-4" />
                Test All
              </>
            )}
          </button>
          {lastCheck && (
            <span className="text-xs text-text-tertiary">
              Last check: {lastCheck.toLocaleTimeString()}
            </span>
          )}
        </div>

        {/* Results Table */}
        <div className="bg-bg-card rounded-xl border border-border-primary overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border-subtle">
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-tertiary uppercase tracking-wider">Service</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-tertiary uppercase tracking-wider hidden sm:table-cell">Endpoint</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-tertiary uppercase tracking-wider">Status</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-tertiary uppercase tracking-wider hidden md:table-cell">Latency</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-tertiary uppercase tracking-wider hidden lg:table-cell">Failures</th>
                </tr>
              </thead>
              <tbody>
                {results.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-12 text-center">
                      <Loader2 className="w-6 h-6 text-text-tertiary animate-spin mx-auto mb-2" />
                      <p className="text-text-tertiary text-sm">Running initial checks...</p>
                    </td>
                  </tr>
                ) : (
                  results.map((result) => (
                    <tr
                      key={result.service}
                      className="border-b border-border-subtle last:border-0 hover:bg-bg-secondary/30 transition-colors"
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {getStatusIcon(result)}
                          <span className="text-sm font-medium text-text-primary">{getServiceLabel(result.service)}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 hidden sm:table-cell">
                        <span className="text-xs font-mono text-text-tertiary truncate max-w-[200px] block" title={result.endpoint}>
                          {result.endpoint}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                          result.status === 'online'
                            ? 'bg-status-success/10 text-status-success'
                            : (consecutiveFailures[result.service] || 0) > 2
                            ? 'bg-status-error/10 text-status-error'
                            : 'bg-bg-tertiary text-text-tertiary'
                        }`}>
                          {getStatusText(result)}
                        </span>
                      </td>
                      <td className="px-4 py-3 hidden md:table-cell">
                        {result.latency !== null ? (
                          <span className="text-xs text-text-secondary flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {result.latency}ms
                          </span>
                        ) : (
                          <span className="text-xs text-text-tertiary">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 hidden lg:table-cell">
                        {(consecutiveFailures[result.service] || 0) > 0 ? (
                          <span className="text-xs text-status-warning">{consecutiveFailures[result.service]}</span>
                        ) : (
                          <span className="text-xs text-status-success">0</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Error Details */}
        {results.some((r) => r.error) && (
          <div className="mt-4 bg-status-error/5 border border-status-error/20 rounded-xl p-4">
            <h4 className="text-xs font-medium text-status-error mb-2">Error Details</h4>
            <div className="space-y-1">
              {results.filter((r) => r.error).map((r) => (
                <p key={r.service} className="text-xs text-text-tertiary font-mono">
                  {getServiceLabel(r.service)}: {r.error}
                </p>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default DiagnosticsPage;
