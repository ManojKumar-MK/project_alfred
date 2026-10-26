/** Per-browser preferences, remembered in localStorage when it is available. */

import { defineStore } from 'pinia'
import { reactive, watch } from 'vue'

export type Theme = 'system' | 'light' | 'dark'
export type BoardGrouping = 'status' | 'phase' | 'agent'

export interface Preferences {
  actor: string
  theme: Theme
  grouping: BoardGrouping
  showFinished: boolean
  search: string
  agentFilter: string
  priorityFilter: string
  repositoryFilter: string
  browserNotifications: boolean
  /** When the inbox was last marked read (ISO time); newer items count as unread. */
  inboxReadAt: string
  terminalFontSize: number
  includeConfigInCommands: boolean
}

const KEY = 'alfred-ui:preferences'

const DEFAULTS: Preferences = {
  actor: 'manager',
  theme: 'system',
  grouping: 'status',
  showFinished: true,
  search: '',
  agentFilter: '',
  priorityFilter: '',
  repositoryFilter: '',
  browserNotifications: false,
  inboxReadAt: '',
  terminalFontSize: 13,
  includeConfigInCommands: false,
}

function load(): Preferences {
  try {
    const raw = window.localStorage.getItem(KEY)
    if (raw) return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Preferences>) }
  } catch {
    /* Private windows or blocked storage: fall back to defaults. */
  }
  return { ...DEFAULTS }
}

export const usePrefs = defineStore('prefs', () => {
  const values = reactive<Preferences>(load())

  watch(
    values,
    (current) => {
      try {
        window.localStorage.setItem(KEY, JSON.stringify(current))
      } catch {
        /* Preferences simply are not remembered. */
      }
    },
    { deep: true },
  )

  watch(
    () => values.theme,
    (theme) => {
      const root = document.documentElement
      if (theme === 'system') root.removeAttribute('data-theme')
      else root.setAttribute('data-theme', theme)
    },
    { immediate: true },
  )

  function reset() {
    Object.assign(values, DEFAULTS)
  }

  return { values, reset }
})
