// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { GatewayClient } from '@/lib/gatewayClient'
import { SIDECAR_DISCONNECTED_MESSAGE } from '@/lib/chat-sidebar-banner'

import { ChatSidebar } from './ChatSidebar'

vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('@/lib/api', () => ({
  HERMES_BASE_PATH: '',
  api: { getModelInfo: async () => ({ model: 'test/model' }) },
  buildWsAuthParam: async () => ['ticket', 'test-ticket'],
  buildWsUrl: async () => 'ws://localhost/api/events?channel=chat-1'
}))
vi.mock('@/lib/dashboard-auth-reload', () => ({ maybeReloadForLoopbackWsAuthFailure: () => false }))
vi.mock('@/components/ModelPickerDialog', () => ({ ModelPickerDialog: () => null }))
vi.mock('@/components/ModelReloadConfirm', () => ({ ModelReloadConfirm: () => null }))
vi.mock('@/components/ReasoningPicker', () => ({ ReasoningPicker: () => null }))
vi.mock('@nous-research/ui/ui/components/button', () => ({
  Button: ({ children, onClick }: { children?: ReactNode; onClick?: () => void }) => (
    <button onClick={onClick}>{children}</button>
  )
}))
vi.mock('@nous-research/ui/ui/components/badge', () => ({
  Badge: ({ children }: { children?: ReactNode }) => <span>{children}</span>
}))
vi.mock('@nous-research/ui/ui/components/card', () => ({
  Card: ({ children }: { children?: ReactNode }) => <div>{children}</div>
}))

interface Request {
  id: string
  method: string
  params?: Record<string, unknown>
}

// Keep both real clients, their state subscriptions, and JSON-RPC framing.
// Only replace the network peer, which retires each close_on_disconnect session.
class Socket extends EventTarget {
  static OPEN = 1
  static instances: Socket[] = []
  readyState = 0
  requests: Request[] = []
  readonly sessionId: string
  readonly url: string

  constructor(url: string) {
    super()
    this.url = url
    this.sessionId = `sidecar-${Socket.instances.length}`
    Socket.instances.push(this)
  }

  open() {
    this.readyState = Socket.OPEN
    this.dispatchEvent(new Event('open'))
  }

  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    this.dispatchEvent(new CloseEvent('close', { code: 1012 }))
  }

  frame(value: unknown) {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(value) }))
  }

  send(text: string) {
    const request = JSON.parse(text) as Request
    this.requests.push(request)
    queueMicrotask(() => {
      if (request.method === 'session.create') {
        this.frame({ jsonrpc: '2.0', id: request.id, result: { session_id: this.sessionId } })
        this.frame({
          jsonrpc: '2.0', method: 'event',
          params: { type: 'session.info', session_id: this.sessionId, seq: 1, payload: { model: 'test/model' } }
        })
      } else if (request.method === 'session.events.since') {
        this.frame({ jsonrpc: '2.0', id: request.id, result: { events: [], latest_seq: 1 } })
      }
    })
  }
}

const sidecars = () => Socket.instances.filter(socket => new URL(socket.url).pathname === '/api/ws')
const feeds = () => Socket.instances.filter(socket => new URL(socket.url).pathname === '/api/events')
let root: Root
let container: HTMLDivElement

async function mount(openSidecar = true) {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(<ChatSidebar channel="chat-1" />))
  await act(async () => {
    for (const socket of Socket.instances) {
      if (openSidecar || new URL(socket.url).pathname === '/api/events') socket.open()
    }
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('ChatSidebar sidecar connection ownership', () => {
  it('keeps a recovered sidecar and passive feed alive after a slow handshake or manual reconnect', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    await mount(false)
    const feed = feeds()[0]
    await act(async () => sidecars()[0].close())
    expect(container.textContent).toContain(SIDECAR_DISCONNECTED_MESSAGE)
    await act(async () => vi.advanceTimersByTimeAsync(250))
    expect(sidecars()).toHaveLength(2)
    const recovered = sidecars()[1]

    // A legitimate handshake may exceed the first retry delay. Registering
    // listeners on the old CLOSED state must not schedule another teardown.
    await act(async () => vi.advanceTimersByTimeAsync(500))
    expect(sidecars()).toHaveLength(2)
    expect(recovered.readyState).toBe(0)
    await act(async () => recovered.open())
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(sidecars()).toHaveLength(2)
    expect(recovered.readyState).toBe(Socket.OPEN)
    expect(container.textContent).not.toContain(SIDECAR_DISCONNECTED_MESSAGE)
    expect(feeds()).toEqual([feed])
    expect(feed.readyState).toBe(Socket.OPEN)

    // Surface a side-panel warning so its manual recovery affordance appears.
    await act(async () => recovered.frame({
      jsonrpc: '2.0', method: 'event', params: { type: 'error', payload: { message: 'test warning' } }
    }))
    const reconnect = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('Reconnect'))
    expect(reconnect).toBeDefined()
    await act(async () => reconnect?.click())
    const manual = sidecars().at(-1)!
    await act(async () => vi.advanceTimersByTimeAsync(500))
    expect(sidecars()).toHaveLength(3)
    expect(manual.readyState).toBe(0)
    await act(async () => {
      manual.open()
      manual.frame({
        jsonrpc: '2.0', method: 'event', params: { type: 'error', payload: { message: 'new credential warning' } }
      })
    })
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(manual.readyState).toBe(Socket.OPEN)
    expect(sidecars()).toHaveLength(3)
    expect(container.textContent).toContain('new credential warning')
  })

  it('creates only one disposable session per recovery without replaying or retaining retired sessions', async () => {
    const connect = vi.spyOn(GatewayClient.prototype, 'connect')
    await mount()
    const client = connect.mock.contexts[0] as GatewayClient

    for (let attempt = 0; attempt < 4; attempt += 1) {
      const socket = sidecars().at(-1)!
      expect(socket.requests.map(request => request.method)).toEqual(['session.create'])
      expect(socket.requests[0].params).toMatchObject({ close_on_disconnect: true })
      await act(async () => socket.close())
      await act(async () => vi.advanceTimersByTimeAsync(250))
      await act(async () => sidecars().at(-1)!.open())
    }

    expect(sidecars()).toHaveLength(5)
    expect(sidecars().flatMap(socket => socket.requests.map(request => request.method)))
      .toEqual(Array.from({ length: 5 }, () => 'session.create'))
    expect(client.getSeqWatermarks()).toEqual({})
  })
})
