import React from 'react';

interface ToastProps {
  message: string;
  type?: 'error' | 'success' | 'info';
  onClose: () => void;
}

const Toast: React.FC<ToastProps> = ({ message, type = 'info', onClose }) => {
  React.useEffect(() => {
    const timer = setTimeout(onClose, 5000);
    return () => clearTimeout(timer);
  }, [onClose]);

  const styles = {
    error: {
      bg: 'bg-[var(--status-error)]',
      border: 'border-[var(--status-error)]/30',
      icon: '❌',
    },
    success: {
      bg: 'bg-[var(--status-success)]',
      border: 'border-[var(--status-success)]/30',
      icon: '✅',
    },
    info: {
      bg: 'bg-[var(--status-info)]',
      border: 'border-[var(--status-info)]/30',
      icon: 'ℹ️',
    },
  };

  const style = styles[type];

  return (
    <div
      className={`fixed bottom-4 right-4 ${style.bg} text-white px-6 py-4 rounded-xl shadow-xl z-50 border ${style.border} animate-fade-in-up min-w-[300px]`}
      role="alert"
    >
      <div className="flex items-center justify-between w-full">
        <div className="flex items-center space-x-3">
          <span className="text-xl">{style.icon}</span>
          <span className="font-medium">{message}</span>
        </div>
        <button
          onClick={onClose}
          className="ml-4 hover:text-[var(--text-primary)]/70 focus:outline-none transition-colors"
        >
          <svg
            style={{ width: '16px', height: '16px' }}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>
    </div>
  );
};

export default Toast;
