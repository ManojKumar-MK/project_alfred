/** Live workspace state streamed from the server over a WebSocket. */

import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import { api, signInRequired, socketUrl } from '@/api/http'
import type { AgentDescription, Notification, SessionInfo, Snapshot, Task } from '@/api/types'
import { now, serverSkew } from '@/lib/format'
import { NOTIFICATION_META } from '@/lib/status'

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'offline'

export interface Call {
  key: string
  task: number | null
  kind: 'review' | 'failed' | 'blocked' | 'session' | 'plan' | 'completions' | 'problem'
  label: string
  detail: string
  tone: 'brass' | 'danger'
  created: string
}

export interface SessionChange {
  id: number
  time: number
  name: string
  change: 'started' | 'ended'
  kind: string
  task: number | null
}

const ACTIVE_WINDOW_SECONDS = 6

export const useLive = defineStore('live', () => {
  const snapshot = shallowRef<Snapshot | null>(null)
  const connection = ref<ConnectionState>('connecting')
  const activity = ref<Record<string, number>>({})
  const updates = ref(0)
  const lastMessageAt = ref(0)
  const sessionLog = ref<SessionChange[]>([])
  let knownSessions: Map<string, { kind: string; task: number | null }> | null = null
  let changeCounter = 0
  let socket: WebSocket | null = null
  let attempts = 0
  let retryTimer: number | undefined
  let stopped = false

  function trackSessions(data: Snapshot) {
    const current = new Map((data.sessions ?? []).map((item) => [item.name, { kind: item.kind, task: item.task_number }]))
    if (knownSessions !== null && !data.error) {
      const time = data.server_time * 1000
      const changes: SessionChange[] = []
      for (const [name, info] of current) {
        if (!knownSessions.has(name)) changes.push({ id: ++changeCounter, time, name, change: 'started', ...info })
      }
      for (const [name, info] of knownSessions) {
        if (!current.has(name)) changes.push({ id: ++changeCounter, time, name, change: 'ended', ...info })
      }
      if (changes.length) sessionLog.value = [...sessionLog.value, ...changes].slice(-200)
    }
    knownSessions = data.error ? null : current
  }

  function apply(data: Snapshot) {
    if (snapshot.value && snapshot.value.workspace.generation !== data.workspace.generation) {
      knownSessions = null
      sessionLog.value = []
    }
    trackSessions(data)
    snapshot.value = data
    serverSkew.value = data.server_time * 1000 - Date.now()
    const merged: Record<string, number> = {}
    for (const session of data.sessions ?? []) merged[session.name] = session.activity
    activity.value = merged
    updates.value += 1
  }

  function connect() {
    stopped = false
    window.clearTimeout(retryTimer)
    connection.value = attempts === 0 ? 'connecting' : 'reconnecting'
    const current = new WebSocket(socketUrl('/live'))
    let opened = false
    socket = current
    current.onopen = () => {
      opened = true
      attempts = 0
    }
    current.onmessage = (event: MessageEvent<string>) => {
      lastMessageAt.value = Date.now()
      connection.value = 'live'
      signInRequired.value = false
      let message: { type: string; data?: unknown; server_time?: number }
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.type === 'snapshot') apply(message.data as Snapshot)
      else if (message.type === 'activity') activity.value = { ...(message.data as Record<string, number>) }
      if (typeof message.server_time === 'number') serverSkew.value = message.server_time * 1000 - Date.now()
    }
    current.onclose = (event) => {
      if (socket !== current) return
      socket = null
      if (stopped) return
      // A refused handshake (such as a missing access token) closes before it ever opens.
      if (!opened || event.code === 1008) {
        void checkSignIn()
      }
      attempts += 1
      connection.value = attempts > 5 ? 'offline' : 'reconnecting'
      const delay = Math.min(10000, 500 * 2 ** Math.min(attempts, 5))
      retryTimer = window.setTimeout(connect, delay)
    }
  }

  async function checkSignIn() {
    try {
      const status = await api.get<{ required: boolean; authenticated: boolean }>('/auth/status')
      signInRequired.value = status.required && !status.authenticated
    } catch {
      /* The reconnect loop reports connection problems. */
    }
  }

  function disconnect() {
    stopped = true
    window.clearTimeout(retryTimer)
    socket?.close()
    socket = null
  }

  /** Ask the server to check for changes immediately, falling back to a plain fetch. */
  async function refresh() {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'refresh' }))
      return
    }
    apply(await api.get<Snapshot>('/snapshot'))
  }

  const tasks = computed<Task[]>(() => snapshot.value?.tasks ?? [])
  const taskMap = computed(() => new Map(tasks.value.map((task) => [task.task_number, task])))
  const config = computed(() => snapshot.value?.config)
  const agents = computed<AgentDescription[]>(() => config.value?.agents ?? [])
  const agentMap = computed(() => new Map(agents.value.map((agent) => [agent.alias, agent])))
  const sessions = computed<SessionInfo[]>(() => snapshot.value?.sessions ?? [])
  const sessionMap = computed(() => new Map(sessions.value.map((session) => [session.name, session])))
  const notifications = computed<Notification[]>(() => snapshot.value?.notifications ?? [])
  const pendingNotifications = computed(() => notifications.value.filter((item) => !item.acknowledged))
  const runs = computed(() => snapshot.value?.runs ?? [])
  const events = computed(() => snapshot.value?.events ?? [])
  const error = computed(() => snapshot.value?.error ?? null)

  /** Everything that currently needs a person, most urgent first. */
  const calls = computed<Call[]>(() => {
    const result: Call[] = []
    const seen = new Set<string>()
    for (const notification of pendingNotifications.value) {
      const meta = NOTIFICATION_META[notification.notification_type]
      const kind: Call['kind'] =
        notification.notification_type === 'task_completed'
          ? 'review'
          : notification.notification_type === 'task_failed'
            ? 'failed'
            : notification.notification_type === 'task_blocked'
              ? 'blocked'
              : notification.notification_type === 'session_died'
                ? 'session'
                : 'problem'
      const key = `${kind}:${notification.task_number}`
      if (seen.has(key)) continue
      seen.add(key)
      result.push({
        key,
        task: notification.task_number,
        kind,
        label: meta?.call ?? notification.notification_type,
        detail: String(notification.details.summary ?? notification.details.message ?? ''),
        tone: kind === 'review' || kind === 'blocked' ? 'brass' : 'danger',
        created: notification.created_at,
      })
    }
    for (const task of tasks.value) {
      const plan = task.derived.plan
      if (plan.awaiting_approval && plan.reported) {
        result.push({
          key: `plan:${task.task_number}`,
          task: task.task_number,
          kind: 'plan',
          label: 'Plan ready',
          detail: task.title,
          tone: 'brass',
          created: plan.last_report?.timestamp ?? task.updated_at,
        })
      }
    }
    const waiting = snapshot.value?.completions?.pending ?? 0
    if (waiting > 0) {
      result.push({
        key: 'completions',
        task: null,
        kind: 'completions',
        label: `${waiting} completion report${waiting === 1 ? '' : 's'} waiting`,
        detail: snapshot.value?.coordinator?.running
          ? 'The coordinator will process them within seconds'
          : 'Run the coordinator to turn them into review notifications',
        tone: 'brass',
        created: snapshot.value?.completions?.oldest_pending ?? snapshot.value?.generated_at ?? '',
      })
    }
    const order: Record<Call['kind'], number> = {
      session: 0,
      failed: 1,
      problem: 2,
      blocked: 3,
      plan: 4,
      review: 5,
      completions: 6,
    }
    return result.sort((left, right) => order[left.kind] - order[right.kind])
  })

  /** Whether a session produced output within the last few seconds. */
  function isBusy(sessionName: string | null | undefined): boolean {
    if (!sessionName) return false
    const stamp = activity.value[sessionName]
    if (!stamp) return false
    return (now.value + serverSkew.value) / 1000 - stamp < ACTIVE_WINDOW_SECONDS
  }

  return {
    snapshot,
    connection,
    activity,
    updates,
    lastMessageAt,
    sessionLog,
    tasks,
    taskMap,
    config,
    agents,
    agentMap,
    sessions,
    sessionMap,
    notifications,
    pendingNotifications,
    runs,
    events,
    error,
    calls,
    connect,
    disconnect,
    refresh,
    apply,
    isBusy,
  }
})
