/** Desktop alerts: raising them for new calls, and the switch that turns them on. */

import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { alertPermission, newCalls, requestAlertPermission, showAlert, type AlertPermission } from '@/lib/alerts'
import type { Call } from '@/stores/live'
import { useLive } from '@/stores/live'
import { usePrefs } from '@/stores/prefs'
import { useToasts } from '@/stores/toasts'

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

  function test() {
    showAlert(
      { key: 'test', task: null, kind: 'problem', label: 'Desktop alerts work', detail: 'Alfred will alert you like this when something needs you.', tone: 'brass', created: '' },
      () => undefined,
    )
  }

  const help = () =>
    permission.value === 'denied'
      ? "Blocked by the browser. Allow notifications in this site's settings."
      : permission.value === 'unsupported'
        ? 'Not supported by this browser.'
        : 'Alert me when something needs me while this tab is in the background.'

  return { permission, toggle, test, help }
}
