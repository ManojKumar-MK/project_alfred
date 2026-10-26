/** Desktop notifications for new "Needs you" calls, raised while Alfred is in the background. */

import type { Call } from '@/stores/live'

export type AlertPermission = NotificationPermission | 'unsupported'

/** The browser's notification permission for this page. */
export function alertPermission(): AlertPermission {
  return 'Notification' in window ? window.Notification.permission : 'unsupported'
}

/** Ask for notification permission once; a decided permission is returned as it is. */
export async function requestAlertPermission(): Promise<AlertPermission> {
  if (!('Notification' in window)) return 'unsupported'
  if (window.Notification.permission !== 'default') return window.Notification.permission
  return window.Notification.requestPermission()
}

/** Calls that were not on the strip last time, in strip order. */
export function newCalls(calls: Call[], previous: ReadonlySet<string>): Call[] {
  return calls.filter((call) => !previous.has(call.key))
}

/** The notification title for a call. */
export function alertTitle(call: Call): string {
  return call.task === null ? call.label : `${call.label}: task ${call.task}`
}

let icon: string | null = null

/** The Alfred mark as a PNG data URL, since not every notification center renders SVG icons. */
export function alertIcon(): string {
  if (icon !== null) return icon
  icon = '/favicon.svg'
  try {
    const canvas = document.createElement('canvas')
    canvas.width = 128
    canvas.height = 128
    const context = canvas.getContext('2d')
    if (context) {
      context.scale(4, 4)
      context.fillStyle = '#1F4E79'
      context.beginPath()
      context.roundRect(0, 0, 32, 32, 7)
      context.fill()
      for (const [x, color] of [[11, '#2E9E5B'], [21, '#D4A64A']] as const) {
        context.fillStyle = color
        context.beginPath()
        context.arc(x, 16, 4.5, 0, Math.PI * 2)
        context.fill()
      }
      icon = canvas.toDataURL('image/png')
    }
  } catch {
    /* Keep the SVG favicon. */
  }
  return icon
}

/** Show one call as a system notification; clicking it brings Alfred forward and runs ``onOpen``. */
export function showAlert(call: Call, onOpen: (call: Call) => void): Notification {
  const options: NotificationOptions & { renotify?: boolean } = {
    body: call.detail,
    tag: `alfred:${call.key}`,
    icon: alertIcon(),
    requireInteraction: call.tone === 'danger',
    // A call that cleared and came back replaces its old alert; announce it again.
    renotify: true,
  }
  const notification = new window.Notification(alertTitle(call), options)
  notification.onclick = () => {
    window.focus()
    onOpen(call)
    notification.close()
  }
  return notification
}
