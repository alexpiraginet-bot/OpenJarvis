import type { ShellAppId } from './types';

export interface MobileShellState {
  openApp: ShellAppId | null;
  assistantOpen: boolean;
  assistantContext: ShellAppId | null;
}

export type MobileShellAction =
  | { type: 'open_app'; app: ShellAppId }
  | { type: 'close_app' }
  | { type: 'open_assistant'; context?: ShellAppId }
  | { type: 'close_assistant' }
  | { type: 'reset' };

export const initialMobileShellState: MobileShellState = {
  openApp: null,
  assistantOpen: false,
  assistantContext: null,
};

export function mobileShellReducer(
  state: MobileShellState,
  action: MobileShellAction,
): MobileShellState {
  switch (action.type) {
    case 'open_app':
      return {
        openApp: action.app,
        assistantOpen: false,
        assistantContext: null,
      };
    case 'close_app':
      return { ...state, openApp: null };
    case 'open_assistant':
      return {
        openApp: null,
        assistantOpen: true,
        assistantContext: action.context ?? null,
      };
    case 'close_assistant':
    case 'reset':
      return initialMobileShellState;
  }
}
