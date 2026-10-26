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

/** Draw the Alfred mark, optionally with a red count badge, as a PNG data URL (or ``null``). */
function drawMark(size: number, badge = 0): string | null {
  try {
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const context = canvas.getContext('2d')
    if (!context) return null
    context.scale(size / 32, size / 32)
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
    if (badge > 0) {
      context.fillStyle = '#D33B2C'
      context.beginPath()
      context.arc(23, 9, 9, 0, Math.PI * 2)
      context.fill()
      context.fillStyle = '#FFFFFF'
      context.font = 'bold 13px system-ui, sans-serif'
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      context.fillText(badge > 9 ? '9+' : String(badge), 23, 9.5)
    }
    return canvas.toDataURL('image/png')
  } catch {
    return null
  }
}

/** The Alfred mark as a PNG data URL, since not every notification center renders SVG icons. */
export function alertIcon(): string {
  icon ??= drawMark(128) ?? '/favicon.svg'
  return icon
}

/** Show ``count`` on the tab's icon, so waiting work is visible from any other tab. */
export function setTabBadge(count: number): void {
  const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (!link) return
  const badged = count > 0 ? drawMark(64, count) : null
  link.type = badged ? 'image/png' : 'image/svg+xml'
  link.href = badged ?? '/favicon.svg'
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
