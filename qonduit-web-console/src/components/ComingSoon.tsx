import React from 'react';

interface ComingSoonCardProps {
  icon: string;
  title: string;
  description: string;
}

const ComingSoonCard: React.FC<ComingSoonCardProps> = ({ icon, title, description }) => {
  return (
    <div className="bg-bg-card rounded-xl border border-border-primary p-5 shadow-card opacity-70 hover:opacity-90 transition-opacity duration-200">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 bg-bg-secondary rounded-lg flex items-center justify-center border border-border-subtle">
          <span className="text-lg">{icon}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-semibold text-text-primary text-sm">{title}</h3>
            <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-accent-primary/10 text-accent-primary border border-accent-primary/20">
              Coming Soon
            </span>
          </div>
          <p className="text-xs text-text-tertiary">{description}</p>
        </div>
      </div>
    </div>
  );
};

interface ComingSoonProps {
  items: ComingSoonCardProps[];
}

const ComingSoon: React.FC<ComingSoonProps> = ({ items }) => {
  if (items.length === 0) return null;

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-4">More Features</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {items.map((item, idx) => (
          <ComingSoonCard key={idx} {...item} />
        ))}
      </div>
    </div>
  );
};

export default ComingSoon;
