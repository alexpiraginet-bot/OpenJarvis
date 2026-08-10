/**
 * The in-place app container: title bar, internal tabs, scrolling body.
 *
 * This is what keeps the springboard metaphor honest. Opening Finanças does
 * not change the route or unmount the home screen — it lays a window over it,
 * and every tab inside Finanças is a segment of that window rather than
 * another destination.
 */

import { BrainCircuit, ChevronLeft } from 'lucide-react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';

export interface Tab {
  id: string;
  label: string;
  render: () => ReactNode;
}

export function AppWindow({
  title,
  specialist,
  tabs,
  activeTab,
  onTabChange,
  onClose,
  onAskJarvis,
}: {
  title: string;
  specialist: string;
  tabs: Tab[];
  activeTab: string;
  onTabChange: (id: string) => void;
  onClose: () => void;
  onAskJarvis: () => void;
}) {
  const reduceMotion = useReducedMotion();
  const current = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];

  return (
    <div className="oj-window">
      <div className="oj-window-bar">
        <button type="button" className="oj-back" onClick={onClose}>
          <ChevronLeft size={22} />
          <span>Início</span>
        </button>
        <div className="oj-window-identity">
          <div className="oj-window-title">{title}</div>
          <div className="oj-window-specialist">{specialist}</div>
        </div>
        <button
          type="button"
          className="oj-window-ai"
          onClick={onAskJarvis}
          aria-label={`Falar com ${specialist}`}
        >
          <BrainCircuit size={17} />
          <span>IA</span>
        </button>
      </div>

      <div className="oj-window-telemetry" aria-hidden="true">
        <span><i /> LINK SEGURO</span>
        <span>NODE / {title.toLocaleUpperCase('pt-BR')}</span>
        <span>SYNC 100%</span>
      </div>

      {tabs.length > 1 && (
        <div className="oj-segmented" role="tablist" aria-label={`Seções de ${title}`}>
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={tab.id === current.id}
              data-active={tab.id === current.id}
              className="oj-segment"
              onClick={() => onTabChange(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}

      <div className="oj-window-body" role="tabpanel">
        <AnimatePresence initial={false} mode="popLayout">
          <motion.div
            key={current.id}
            className="oj-tab-motion"
            initial={
              reduceMotion
                ? false
                : { opacity: 0, x: 22, filter: 'blur(3px)' }
            }
            animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
            exit={
              reduceMotion
                ? { opacity: 0 }
                : { opacity: 0, x: -14, filter: 'blur(2px)' }
            }
            transition={
              reduceMotion
                ? { duration: 0 }
                : { type: 'spring', stiffness: 470, damping: 40, mass: 0.72 }
            }
          >
            {current.render()}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
