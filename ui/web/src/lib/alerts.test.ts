import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Call } from '@/stores/live'
import { alertPermission, alertTitle, newCalls, requestAlertPermission, showAlert } from './alerts'

function makeCall(overrides: Partial<Call> = {}): Call {
  return { key: 'review:7', task: 7, kind: 'review', label: 'Review needed', detail: 'Done', tone: 'brass', created: '', ...overrides }
}

class FakeNotification {
  static permission: NotificationPermission = 'default'
  static requestPermission = vi.fn(async () => 'granted' as NotificationPermission)
  static shown: FakeNotification[] = []
  onclick: (() => void) | null = null
  close = vi.fn()
  listeners: Record<string, () => void> = {}
  addEventListener(type: string, listener: () => void) {
    this.listeners[type] = listener
  }

  constructor(
    public title: string,
    public options: NotificationOptions,
  ) {
    FakeNotification.shown.push(this)
  }
}

function installNotification(permission: NotificationPermission) {
  FakeNotification.permission = permission
  FakeNotification.shown = []
  FakeNotification.requestPermission.mockClear()
  vi.stubGlobal('Notification', FakeNotification)
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('newCalls', () => {
  it('returns only calls whose keys were not seen, keeping their order', () => {
    const calls = [makeCall({ key: 'failed:3', task: 3 }), makeCall(), makeCall({ key: 'plan:9', task: 9 })]
    expect(newCalls(calls, new Set(['review:7'])).map((call) => call.key)).toEqual(['failed:3', 'plan:9'])
  })

  it('treats a call that cleared and returned as new again', () => {
    expect(newCalls([makeCall()], new Set())).toHaveLength(1)
  })
})

describe('alertTitle', () => {
  it('names the task when there is one', () => {
    expect(alertTitle(makeCall())).toBe('Review needed: task 7')
    expect(alertTitle(makeCall({ task: null, label: '2 completion reports waiting' }))).toBe('2 completion reports waiting')
  })
})

describe('permission', () => {
  it('reports unsupported browsers', async () => {
    vi.stubGlobal('Notification', undefined)
    Reflect.deleteProperty(window, 'Notification')
    expect(alertPermission()).toBe('unsupported')
    expect(await requestAlertPermission()).toBe('unsupported')
  })

  it('asks only while the permission is undecided', async () => {
    installNotification('default')
    expect(await requestAlertPermission()).toBe('granted')
    installNotification('denied')
    expect(await requestAlertPermission()).toBe('denied')
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()
  })
})

describe('showAlert', () => {
  it('tags the notification by call and opens it on click', () => {
    installNotification('granted')
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    const focus = vi.spyOn(window, 'focus').mockImplementation(() => undefined)
    const opened: Call[] = []
    showAlert(makeCall({ tone: 'danger' }), (call) => opened.push(call))
    const [shown] = FakeNotification.shown
    expect(shown.title).toBe('Review needed: task 7')
    expect(shown.options).toMatchObject({ body: 'Done', tag: 'alfred:review:7', requireInteraction: true, renotify: true })
    shown.onclick?.()
    expect(focus).toHaveBeenCalled()
    expect(opened.map((call) => call.key)).toEqual(['review:7'])
    expect(shown.close).toHaveBeenCalled()
  })
})

describe('useAlertSwitch test alert', () => {
  async function setup(permission: NotificationPermission) {
    const { createPinia, setActivePinia } = await import('pinia')
    setActivePinia(createPinia())
    installNotification(permission)
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    const { useAlertSwitch } = await import('@/composables/useDesktopAlerts')
    const { useToasts } = await import('@/stores/toasts')
    return { alerts: useAlertSwitch(), toasts: useToasts() }
  }

  it('uses a fresh tag each time and confirms when the browser shows it', async () => {
    const { alerts, toasts } = await setup('granted')
    alerts.test()
    alerts.test()
    const [first, second] = FakeNotification.shown
    expect(first.options.tag).not.toBe(second.options.tag)
    first.listeners.show()
    expect(toasts.items.at(-1)?.title).toBe('Test alert sent to your system')
  })

  it('explains the system settings when nothing is shown', async () => {
    vi.useFakeTimers()
    try {
      const { alerts, toasts } = await setup('granted')
      alerts.test()
      vi.advanceTimersByTime(4000)
      expect(toasts.items.at(-1)).toMatchObject({ tone: 'error', title: 'The browser did not show the test alert' })
      expect(toasts.items.at(-1)?.detail).toContain('System Settings')
    } finally {
      vi.useRealTimers()
    }
  })

  it('reports a blocked permission instead of sending', async () => {
    const { alerts, toasts } = await setup('denied')
    alerts.test()
    expect(FakeNotification.shown).toHaveLength(0)
    expect(toasts.items.at(-1)?.title).toBe('Notifications are not allowed for this page')
  })
})

describe('setTabBadge', () => {
  it('falls back to the plain favicon when nothing waits or canvas is unavailable', async () => {
    const { setTabBadge } = await import('./alerts')
    document.head.innerHTML = '<link rel="icon" type="image/svg+xml" href="/favicon.svg" />'
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    setTabBadge(3)
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')!
    expect([link.getAttribute('href'), link.type]).toEqual(['/favicon.svg', 'image/svg+xml'])
    setTabBadge(0)
    expect(link.getAttribute('href')).toBe('/favicon.svg')
  })
})

describe('testLater', () => {
  it('counts down before sending, so there is time to switch tabs', async () => {
    vi.useFakeTimers()
    try {
      const { createPinia, setActivePinia } = await import('pinia')
      setActivePinia(createPinia())
      installNotification('granted')
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
      const { useAlertSwitch } = await import('@/composables/useDesktopAlerts')
      const alerts = useAlertSwitch()
      alerts.testLater(3)
      alerts.testLater(3)
      expect(alerts.countdown.value).toBe(3)
      vi.advanceTimersByTime(2000)
      expect(FakeNotification.shown).toHaveLength(0)
      vi.advanceTimersByTime(1000)
      expect(alerts.countdown.value).toBe(0)
      expect(FakeNotification.shown).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
