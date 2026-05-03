import React, { useState, useEffect, useCallback, useRef } from 'react';
import { streamLogs } from '../services/api';
import {
  Terminal,
  Play,
  Pause,
  RotateCcw,
  Copy,
  CheckCircle2,
  AlertCircle,
  Maximize2,
  Minimize2,
} from 'lucide-react';

interface LogsPanelProps {
  routerStatus: { running: boolean; exists: boolean } | null;
}

const LOG_COLORS: Record<string, string> = {
  ERROR: 'text-status-error',
  WARN: 'text-status-warning',
  DEBUG: 'text-text-tertiary',
  INFO: 'text-text-secondary',
};

const LogsPanel: React.FC<LogsPanelProps> = ({ routerStatus }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef(false);
  const maxLines = 500;

  // Load expanded state from localStorage
  useEffect(() => {
    const saved = localStorage.getItem('qonduit-logs-expanded');
    if (saved === 'true') setIsExpanded(true);
  }, []);

  const saveExpandedState = (expanded: boolean) => {
    setIsExpanded(expanded);
    localStorage.setItem('qonduit-logs-expanded', String(expanded));
  };

  const startStreaming = useCallback(async () => {
    setIsStreaming(true);
    setError(null);
    abortRef.current = false;
    setIsPaused(false);

    try {
      for await (const _ of streamLogs((line: string) => {
        if (abortRef.current || isPaused) return;
        setLogs((prev) => {
          const next = [...prev, line];
          return next.length > maxLines ? next.slice(-maxLines) : next;
        });
      })) {
        // Keep the generator running
      }
    } catch (err) {
      if (!abortRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to connect to log stream');
      }
    } finally {
      if (!abortRef.current) {
        setIsStreaming(false);
      }
    }
  }, [isPaused]);

  const stopStreaming = useCallback(() => {
    abortRef.current = true;
    setIsStreaming(false);
  }, []);

  const reconnect = useCallback(() => {
    setError(null);
    startStreaming();
  }, [startStreaming]);

  const togglePause = useCallback(() => {
    if (isPaused) {
      setIsPaused(false);
      startStreaming();
    } else {
      setIsPaused(true);
      stopStreaming();
    }
  }, [isPaused, startStreaming, stopStreaming]);

  useEffect(() => {
    if (routerStatus?.running && !isStreaming && !error) {
      startStreaming();
    }
    return () => {
      abortRef.current = true;
    };
  }, [routerStatus?.running, isStreaming, error, startStreaming]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const isRunning = routerStatus?.running;

  const handleCopyAll = async () => {
    try {
      await navigator.clipboard.writeText(logs.join('\n'));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* ignore */ }
  };

  // Filter logs by search term
  const filteredLogs = searchTerm
    ? logs.filter((log) => log.toLowerCase().includes(searchTerm.toLowerCase()))
    : logs;

  // Get color class for a log line
  const getLogColor = (log: string): string => {
    const upper = log.toUpperCase();
    for (const [level, color] of Object.entries(LOG_COLORS)) {
      if (upper.includes(level)) return color;
    }
    return 'text-text-secondary';
  };

  const containerHeight = isExpanded ? 'h-[calc(100vh-200px)]' : 'h-64';

  return (
    <div className="bg-bg-card rounded-xl border border-border-primary shadow-card">
      {/* Header */}
      <div className="flex items-center justify-between p-4 pb-2">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-text-tertiary" />
            <h3 className="text-sm font-semibold text-text-primary">Terminal</h3>
          </div>
          {isRunning && (
            <div className="flex items-center gap-1.5">
              {isStreaming && !isPaused ? (
                <>
                  <div className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse-dot" />
                  <span className="text-[10px] text-status-success">Streaming</span>
                </>
              ) : isPaused ? (
                <>
                  <div className="w-1.5 h-1.5 rounded-full bg-status-warning" />
                  <span className="text-[10px] text-status-warning">Paused</span>
                </>
              ) : (
                <>
                  <div className="w-1.5 h-1.5 rounded-full bg-text-tertiary" />
                  <span className="text-[10px] text-text-tertiary">Reconnecting...</span>
                </>
              )}
            </div>
          )}
          <span className="text-[10px] text-text-tertiary">
            {filteredLogs.length} / {logs.length} lines
          </span>
        </div>
        <div className="flex items-center gap-1">
          {isRunning && (
            <>
              <button
                onClick={togglePause}
                className="p-1.5 rounded-lg text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary transition-all duration-200"
                title={isPaused ? 'Resume' : 'Pause'}
              >
                {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              </button>
              {error && (
                <button
                  onClick={reconnect}
                  className="p-1.5 rounded-lg text-status-warning hover:bg-status-warning/10 transition-all duration-200"
                  title="Reconnect"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                </button>
              )}
            </>
          )}
          {logs.length > 0 && (
            <button
              onClick={handleCopyAll}
              className="p-1.5 rounded-lg text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary transition-all duration-200"
              title="Copy all logs"
            >
              {copied ? <CheckCircle2 className="w-3.5 h-3.5 text-status-success" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          )}
          <button
            onClick={() => saveExpandedState(!isExpanded)}
            className="p-1.5 rounded-lg text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary transition-all duration-200"
            title={isExpanded ? 'Collapse' : 'Expand'}
          >
            {isExpanded ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Search Bar */}
      {logs.length > 0 && (
        <div className="px-4 pb-2">
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Filter logs..."
            className="w-full px-3 py-1.5 bg-bg-secondary border border-border-subtle rounded-lg text-xs text-text-primary placeholder-text-tertiary focus:outline-none focus:border-accent-primary/50 focus:ring-1 focus:ring-accent-primary/50 transition-all duration-200"
          />
        </div>
      )}

      {/* Logs Display */}
       <div className={`px-4 pb-4 ${isExpanded ? 'pt-2' : ''}`}>
         <div className={`bg-bg-terminal rounded-lg border border-border-subtle overflow-hidden ${containerHeight}`}>
           <div className="h-full overflow-y-auto font-mono text-xs p-3">
             {logs.length === 0 ? (
               <div className="flex items-center justify-center h-full text-text-tertiary">
                 {error ? (
                   <div className="text-center">
                     <AlertCircle className="w-8 h-8 text-status-error mx-auto mb-2" />
                     <p className="text-status-error mb-1">Connection Error</p>
                     <p className="text-xs text-text-tertiary mb-3">{error}</p>
                     {isRunning && (
                       <button
                         onClick={reconnect}
                         className="px-3 py-1.5 rounded-lg text-xs font-medium border border-status-warning/30 text-status-warning hover:bg-status-warning/10 transition-all duration-200"
                       >
                         Reconnect
                       </button>
                     )}
                   </div>
                 ) : !routerStatus ? (
                   <div className="text-center">
                     <Terminal className="w-8 h-8 text-text-tertiary/50 mx-auto mb-2" />
                     <p className="text-text-tertiary text-sm">Router not available</p>
                     <p className="text-text-tertiary/60 text-xs mt-1">Check that the Router service is running</p>
                   </div>
                 ) : isRunning ? (
                   <div className="text-center">
                     <Terminal className="w-8 h-8 text-text-tertiary/50 mx-auto mb-2" />
                     <p className="text-text-tertiary text-sm">Waiting for logs...</p>
                     <p className="text-text-tertiary/60 text-xs mt-1">Logs will appear once the model starts</p>
                   </div>
                 ) : (
                   <div className="text-center">
                     <Terminal className="w-8 h-8 text-text-tertiary/50 mx-auto mb-2" />
                     <p className="text-text-tertiary text-sm">No logs yet</p>
                     <p className="text-text-tertiary/60 text-xs mt-1">Launch a model to see logs here</p>
                     <button
                       onClick={startStreaming}
                       className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-accent-primary/10 text-accent-primary border border-accent-primary/20 hover:bg-accent-primary/20 transition-colors"
                     >
                       <Play className="w-3 h-3" />
                       Start Streaming
                     </button>
                   </div>
                 )}
               </div>
             ) : (
               <div className="space-y-0.5">
                 {filteredLogs.map((log, idx) => (
                   <div key={idx} className={`${getLogColor(log)} hover:bg-white/5 px-1 -mx-1 rounded transition-colors`}>
                     <span className="text-text-tertiary/40 select-none mr-3">{String(idx + 1).padStart(4, ' ')}</span>
                     {log}
                   </div>
                 ))}
                 <div ref={logsEndRef} />
               </div>
             )}
           </div>
         </div>
       </div>
    </div>
  );
};

export default LogsPanel;
