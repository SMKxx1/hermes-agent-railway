// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const mocks = vi.hoisted(() => ({
  model: 'provider/configured-default',
  sidecarInfo: null as ((event: { payload: { model: string } }) => void) | null,
  feedInfo: null as ((event: { payload: { model: string } }) => void) | null
}))
vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('@/lib/api', () => ({
  HERMES_BASE_PATH: '',
  api: {
    getModelInfo: async () => ({ model: mocks.model }),
    setModelAssignment: async ({ model }: { model: string }) => { mocks.model = model; return {} }
  }
}))
vi.mock('@/lib/gatewayClient', () => ({
  GatewayClient: class {
    onState(fn: (state: string) => void) { fn('open'); return () => {} }
    on(type: string, fn: typeof mocks.sidecarInfo) {
      if (type === 'session.info') mocks.sidecarInfo = fn
      return () => {}
    }
    async connect() {}
    async request() { return { session_id: 'auxiliary' } }
    close() {}
  }
}))
vi.mock('@/lib/eventsFeedClient', () => ({
  EventsFeedClient: class {
    onState(fn: (state: string) => void) { fn('open'); return () => {} }
    onClose() { return () => {} }
    on(type: string, fn: typeof mocks.feedInfo) {
      if (type === 'session.info') mocks.feedInfo = fn
      return () => {}
    }
    async connect() {}
    close() {}
  }
}))
vi.mock('@/components/ModelPickerDialog', () => ({
  ModelPickerDialog: ({ onApply, onClose }: {
    onApply: (value: { model: string; provider: string }) => Promise<unknown>;
    onClose: () => void;
  }) => <button onClick={() => void onApply({ model: 'provider/new-default', provider: 'provider' }).then(onClose)}>Save selected model</button>
}))
vi.mock('@/components/ReasoningPicker', () => ({ ReasoningPicker: () => null }))
vi.mock('@nous-research/ui/ui/components/button', () => ({
  Button: ({ children, onClick, disabled }: { children?: ReactNode; onClick?: () => void; disabled?: boolean }) => <button disabled={disabled} onClick={onClick}>{children}</button>
}))
vi.mock('@nous-research/ui/ui/components/badge', () => ({
  Badge: ({ children, 'aria-label': label }: { children?: ReactNode; 'aria-label'?: string }) => <span data-testid="connection-badge" aria-label={label}>{children}</span>
}))
vi.mock('@nous-research/ui/ui/components/card', () => ({ Card: ({ children }: { children?: ReactNode }) => <div>{children}</div> }))

it('distinguishes the saved default, PTY-reported model and terminal readiness through a model change', async () => {
  const { ChatSidebar } = await import('./ChatSidebar')
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const fresh = vi.fn()
  const badge = () => container.querySelector('[data-testid="connection-badge"]')?.textContent
  const click = async (label: string) => {
    const button = [...document.querySelectorAll('button')].find(b => b.textContent === label)
    expect(button).toBeDefined()
    await act(async () => button!.click())
  }
  try {
    await act(async () => root.render(<ChatSidebar channel="old" terminalState="open" onDashboardNewSessionRequest={fresh} />))
    expect(badge()).toBe('starting chat') // Both auxiliary sockets already report open.
    expect(container.textContent).toContain('default model')
    expect(container.textContent).toContain('Chat model: not yet confirmed')
    await act(async () => mocks.sidecarInfo?.({ payload: { model: 'irrelevant-sidecar-model' } }))
    expect(container.textContent).not.toContain('irrelevant-sidecar-model')
    await act(async () => mocks.feedInfo?.({ payload: { model: 'provider/actual-chat-model' } }))
    expect(container.textContent).toContain('Chat model: provider/actual-chat-model')
    await click('configured-default')
    await click('Save selected model')
    expect(container.textContent).toContain('new-default')
    expect(container.textContent).toContain('Chat model: provider/actual-chat-model')
    await click('Start new chat')
    expect(fresh).toHaveBeenCalledTimes(1)
    const oldInfo = mocks.feedInfo
    await act(async () => root.render(<ChatSidebar channel="new" terminalState="connecting" onDashboardNewSessionRequest={fresh} />))
    await act(async () => oldInfo?.({ payload: { model: 'stale-old-model' } }))
    expect(container.textContent).not.toContain('stale-old-model')
    expect(container.textContent).toContain('Chat model: not yet confirmed')
    await act(async () => mocks.feedInfo?.({ payload: { model: 'provider/new-default' } }))
    await act(async () => root.render(<ChatSidebar channel="new" terminalState="open" terminalHasOutput onDashboardNewSessionRequest={fresh} />))
    expect(badge()).toBe('connected')
    expect(container.textContent).toContain('Chat model: provider/new-default')
    await act(async () => root.render(<ChatSidebar channel="new" terminalState="closed" terminalHasOutput onDashboardNewSessionRequest={fresh} />))
    expect(badge()).toBe('disconnected')
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})
