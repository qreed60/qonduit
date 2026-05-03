import React, { useState, useEffect, useCallback, useRef } from 'react';
import { streamLogs } from '../services/api';

interface LogsPanelProps {
  routerStatus: { running: boolean; exists: boolean } | null;
}

const LogsPanel: React.FC<LogsPanelProps> = ({ routerStatus }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef(false);
  const maxLines = 500;

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

  return (
    <div className="bg-bg-card rounded-xl border border-border-primary shadow-card">
      <div className="flex items-center justify-between p-5 pb-3">
        <div>
          <h3 className="text-lg font-semibold text-text-primary">Live Logs</h3>
          <p className="text-sm text-text-secondary mt-1">
            {isRunning ? (
              isStreaming && !isPaused ? (
                <span className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-status-success animate-pulse-dot" />
                  <span>Streaming...</span>
                </span>
              ) : isPaused ? (
                <span className="text-status-warning">Paused</span>
              ) : (
                <span className="text-text-tertiary">Reconnecting...</span>
              )
            ) : (
              <span className="text-text-tertiary">Start a model to view logs</span>
            )}
          </p>
        </div>
        {isRunning && (
          <div className="flex gap-2">
            <button
              onClick={togglePause}
              className="px-4 py-2 rounded-lg text-xs font-medium border border-border-primary text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-all duration-200"
            >
              {isPaused ? 'Resume' : 'Pause'}
            </button>
            {error && (
              <button
                onClick={reconnect}
                className="px-4 py-2 rounded-lg text-xs font-medium border border-status-warning/30 text-status-warning hover:bg-status-warning/10 transition-all duration-200"
              >
                Reconnect
              </button>
            )}
          </div>
        )}
      </div>

      {/* Logs Display */}
      <div className="px-5 pb-5">
        <div className="bg-bg-terminal rounded-lg border border-border-subtle p-4 h-64 overflow-y-auto font-mono text-xs">
          {logs.length === 0 ? (
            <div className="flex items-center justify-center h-full text-text-tertiary">
              {error ? (
                <div className="text-center">
                  <p className="text-status-error mb-2">Connection Error</p>
                  <p className="text-xs">{error}</p>
                  {isRunning && (
                    <button
                      onClick={reconnect}
                      className="mt-2 px-3 py-1 rounded text-xs font-medium border border-status-warning/30 text-status-warning hover:bg-status-warning/10 transition-all duration-200"
                    >
                      Reconnect
                    </button>
                  )}
                </div>
              ) : (
                <p>No logs to display</p>
              )}
            </div>
          ) : (
            <div className="space-y-0.5">
              {logs.map((log, idx) => (
                <div key={idx} className="text-text-secondary hover:text-text-primary transition-colors">
                  <span className="text-text-tertiary select-none mr-3">{String(idx + 1).padStart(4, ' ')}</span>
                  {log}
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LogsPanel;
