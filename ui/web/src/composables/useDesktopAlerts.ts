/** Desktop alerts: raising them for new calls, and the switch that turns them on. */

import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { alertPermission, newCalls, requestAlertPermission, showAlert, type AlertPermission } from '@/lib/alerts'
import type { Call } from '@/stores/live'
import { useLive } from '@/stores/live'
import { usePrefs } from '@/stores/prefs'
import { useToasts } from '@/stores/toasts'

// The page can hold permission while the operating system still hides the browser's alerts.
const TEST_DELAY_SECONDS = 5
const OS_HINT =
  'Allow alerts for your browser in System Settings › Notifications (choose Banners or Alerts) and turn off Focus. Settings › Preferences has a step-by-step guide.'

/** Raise a desktop notification for each new call while the page is hidden or unfocused. */
export function useDesktopAlerts(): void {
  const live = useLive()
  const prefs = usePrefs()
  const router = useRouter()
  let previous: Set<string> | null = null
  let generation: number | null = null

  function open(call: Call) {
    const query = call.task === null ? {} : { task: String(call.task) }
    void router.push({ path: call.task === null ? '/operations' : router.currentRoute.value.path, query })
  }

  watch(
    () => live.calls,
    (calls) => {
      const snapshot = live.snapshot
      if (!snapshot || snapshot.error) return
      const fresh = previous !== null && generation === snapshot.workspace.generation ? newCalls(calls, previous) : []
      previous = new Set(calls.map((call) => call.key))
      generation = snapshot.workspace.generation
      if (!fresh.length || !prefs.values.browserNotifications || alertPermission() !== 'granted') return
      if (document.visibilityState === 'visible' && document.hasFocus()) return
      for (const call of fresh) showAlert(call, open)
    },
  )
}

/** The desktop-alerts preference, kept in step with the browser's permission. */
export function useAlertSwitch() {
  const prefs = usePrefs()
  const toasts = useToasts()
  const permission = ref<AlertPermission>(alertPermission())

  async function toggle(enabled: boolean) {
    if (!enabled) {
      prefs.values.browserNotifications = false
      return
    }
    permission.value = await requestAlertPermission()
    prefs.values.browserNotifications = permission.value === 'granted'
    if (permission.value === 'denied') {
      toasts.push({
        tone: 'error',
        title: 'Notifications are blocked for this page',
        detail: "Allow them in the browser's site settings, then turn desktop alerts on again.",
      })
    } else if (permission.value === 'unsupported') {
      toasts.push({ tone: 'error', title: 'This browser does not support desktop notifications' })
    }
  }

  /** Send a sample alert and report what the browser did with it. */
  function test() {
    permission.value = alertPermission()
    if (permission.value !== 'granted') {
      toasts.push({ tone: 'error', title: 'Notifications are not allowed for this page', detail: help() })
      return
    }
    let notification: Notification
    try {
      notification = showAlert(
        { key: 'test', task: null, kind: 'problem', label: 'Desktop alerts work', detail: 'Alfred will alert you like this when something needs you.', tone: 'brass', created: '' },
        () => undefined,
      )
    } catch (error) {
      toasts.push({ tone: 'error', title: 'The browser refused the test alert', detail: String(error) })
      return
    }
    const quiet = window.setTimeout(() => toasts.push({ tone: 'error', title: 'The browser did not show the test alert', detail: OS_HINT }, 12000), 4000)
    notification.addEventListener('show', () => {
      window.clearTimeout(quiet)
      toasts.push({ tone: 'ok', title: 'Test alert sent to your system', detail: `Nothing on screen? ${OS_HINT}` }, 12000)
    })
    notification.addEventListener('error', () => {
      window.clearTimeout(quiet)
      toasts.push({ tone: 'error', title: 'The browser could not show the test alert', detail: OS_HINT }, 12000)
    })
  }

  /** Seconds left before a delayed test, so there is time to switch to another tab or app. */
  const countdown = ref(0)
  let countdownTimer: number | undefined

  function testLater(seconds = TEST_DELAY_SECONDS) {
    if (countdown.value) return
    countdown.value = seconds
    toasts.push({ tone: 'info', title: `Test alert in ${seconds} seconds`, detail: 'Switch to another tab or app now to see it arrive.' }, seconds * 1000)
    countdownTimer = window.setInterval(() => {
      countdown.value -= 1
      if (countdown.value > 0) return
      window.clearInterval(countdownTimer)
      test()
    }, 1000)
  }

  const help = () =>
    permission.value === 'denied'
      ? "Blocked by the browser. Allow notifications in this site's settings."
      : permission.value === 'unsupported'
        ? 'Not supported by this browser.'
        : 'Alert me when something needs me while this tab is in the background.'

  return { permission, toggle, test, testLater, countdown, help }
}
