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
    expect(shown.options).toMatchObject({ body: 'Done', tag: 'alfred:review:7', requireInteraction: true })
    shown.onclick?.()
    expect(focus).toHaveBeenCalled()
    expect(opened.map((call) => call.key)).toEqual(['review:7'])
    expect(shown.close).toHaveBeenCalled()
  })
})
