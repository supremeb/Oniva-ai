import { render, screen } from '@testing-library/react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useAuthStore } from '@/lib/stores/auth';
import { ConversationInput } from '../ConversationInput';
import { WelcomeInput } from '../WelcomeInput';

jest.mock('../../SkillPicker', () => ({ SkillPicker: () => null }));
jest.mock('../../ConnectorPopover', () => ({ ConnectorPopover: () => null }));

const baseProps = {
  value: '',
  onChange: jest.fn(),
  onSubmit: jest.fn(),
  isLoading: false,
  isStreaming: false,
  onStop: jest.fn(),
  deepWorkMode: false,
  onDeepWorkToggle: jest.fn(),
  thinkingEnabled: false,
  onThinkingToggle: jest.fn(),
  thinkingSupported: true,
  inputRef: { current: null },
  selectedSkills: [],
  onRemoveSkill: jest.fn(),
  sessionId: null,
  activeConnectorIds: [],
  onToggleConnector: jest.fn(),
  skillPickerOpen: false,
  onSkillPickerOpenChange: jest.fn(),
  slashQuery: '',
  onSkillSelect: jest.fn(),
};

const setFlag = (on: boolean) =>
  useAuthStore.setState({ user: { features: { ENABLE_ENGINE_DEEP_WORK: on } } } as never);

describe('Deep Work toggle follows the engine Deep Work flag', () => {
  it.each([
    ['ConversationInput', (p: typeof baseProps) => <ConversationInput {...(p as unknown as React.ComponentProps<typeof ConversationInput>)} />],
    ['WelcomeInput', (p: typeof baseProps) => <WelcomeInput {...(p as unknown as React.ComponentProps<typeof WelcomeInput>)} />],
  ])('%s shows the toggle when on and hides it when the tenant opted out', (_name, renderIt) => {
    setFlag(true);
    const { unmount } = render(<TooltipProvider>{renderIt(baseProps)}</TooltipProvider>);
    const withToggle = screen.queryAllByRole('button').length;
    unmount();

    setFlag(false);
    render(<TooltipProvider>{renderIt(baseProps)}</TooltipProvider>);
    expect(screen.queryAllByRole('button').length).toBe(withToggle - 1);
  });
});
