<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BellOff, Eraser } from 'lucide-vue-next'
import { api } from '@/api/http'
import type { Notification } from '@/api/types'
import { humanize } from '@/lib/format'
import { NOTIFICATION_META, statusLamp } from '@/lib/status'
import { useAlertSwitch } from '@/composables/useDesktopAlerts'
import { useRequest } from '@/composables/useRequest'
import { useLive } from '@/stores/live'
import { usePrefs } from '@/stores/prefs'
import AppButton from '@/components/base/AppButton.vue'
import EmptyState from '@/components/base/EmptyState.vue'
import LampDot from '@/components/base/LampDot.vue'
import RelativeTime from '@/components/base/RelativeTime.vue'
import TagChip from '@/components/base/TagChip.vue'
import ToggleSwitch from '@/components/base/ToggleSwitch.vue'
import WorkspaceProblem from '@/components/layout/WorkspaceProblem.vue'

/** Human-action notifications raised by the coordinator, grouped by task. */
const live = useLive()
const prefs = usePrefs()
const route = useRoute()
const router = useRouter()
const { pending, run } = useRequest()
const showAcknowledged = ref(false)

function group(items: Notification[]) {
  const groups = new Map<number, Notification[]>()
  for (const item of items) groups.set(item.task_number, [...(groups.get(item.task_number) ?? []), item])
  return [...groups.entries()].map(([task, list]) => ({ task, list: [...list].reverse() }))
}

const pendingGroups = computed(() => group(live.pendingNotifications))
const acknowledged = computed(() => live.notifications.filter((item) => item.acknowledged).reverse())

async function acknowledge(task: number) {
  await run(() => api.post('/notifications/ack', { task }), { command: `alfred notifications ack --task ${task}`, task })
}

async function clear() {
  await run(() => api.post('/notifications/clear'), { command: 'alfred notifications clear' })
}

const alerts = useAlertSwitch()

function openTask(task: number) {
  void router.push({ path: route.path, query: { ...route.query, task: String(task) } })
}
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>Notifications</h1>
        <p>
          The coordinator turns completion reports and dead sessions into notifications. Acknowledge them once
          handled; clearing removes acknowledged ones.
        </p>
      </div>
      <span class="spacer" />
      <ToggleSwitch
        :model-value="prefs.values.browserNotifications"
        label="Desktop alerts"
        :help="alerts.help()"
        @update:model-value="alerts.toggle"
      />
      <AppButton v-if="prefs.values.browserNotifications && alerts.permission.value === 'granted'" size="sm" tone="quiet" @click="alerts.testLater()">
        {{ alerts.countdown.value ? `Sending in ${alerts.countdown.value}s…` : 'Send a test' }}
      </AppButton>
    </header>

    <WorkspaceProblem />

    <EmptyState v-if="!pendingGroups.length" :icon="BellOff" title="Nothing needs you">
      New reviews, failures, blockers, and dead sessions appear here and in the bar under the header.
    </EmptyState>

    <div class="groups">
      <section v-for="entry in pendingGroups" :key="entry.task" class="panel group">
        <header class="panel-header">
          <LampDot :color="statusLamp(live.taskMap.get(entry.task)?.status ?? '')" :size="9" />
          <button type="button" class="task" @click="openTask(entry.task)">#{{ entry.task }} {{ live.taskMap.get(entry.task)?.title ?? '' }}</button>
          <span class="spacer" />
          <AppButton size="sm" tone="brass" :loading="pending" @click="acknowledge(entry.task)">Acknowledge</AppButton>
        </header>
        <ul class="panel-body list">
          <li v-for="item in entry.list" :key="item.notification_id" :data-notification="item.notification_type">
            <p class="line">
              <TagChip :tone="item.notification_type === 'task_completed' ? 'ok' : item.notification_type === 'task_blocked' ? 'brass' : 'danger'">
                {{ NOTIFICATION_META[item.notification_type]?.label ?? humanize(item.notification_type) }}
              </TagChip>
              <RelativeTime class="faint small" :value="item.created_at" />
            </p>
            <p v-if="item.details.summary || item.details.message">{{ item.details.summary || item.details.message }}</p>
            <p class="faint small">
              <template v-if="item.details.status">Result {{ item.details.status }}. </template>
              <template v-if="item.details.agent">Agent {{ item.details.agent }}. </template>
              <template v-if="item.details.repositories?.length">Repositories {{ item.details.repositories.join(', ') }}. </template>
              <template v-if="item.details.commits?.length">Commits {{ item.details.commits.join(', ') }}. </template>
              <template v-if="item.details.session_name">Session {{ item.details.session_name }}.</template>
            </p>
            <ul v-if="item.details.validation_issues?.length" class="issues">
              <li v-for="issue in item.details.validation_issues" :key="issue">{{ issue }}</li>
            </ul>
          </li>
        </ul>
      </section>
    </div>

    <section class="panel">
      <header class="panel-header">
        <h3>Acknowledged</h3>
        <span class="faint small">{{ acknowledged.length }}</span>
        <span class="spacer" />
        <AppButton size="sm" tone="quiet" @click="showAcknowledged = !showAcknowledged">{{ showAcknowledged ? 'Hide' : 'Show' }}</AppButton>
        <AppButton size="sm" :icon="Eraser" :disabled="!acknowledged.length" :loading="pending" @click="clear">Clear acknowledged</AppButton>
      </header>
      <ul v-if="showAcknowledged" class="panel-body list">
        <li v-if="!acknowledged.length" class="muted small">None.</li>
        <li v-for="item in acknowledged" :key="item.notification_id" class="done">
          <p class="line">
            <button type="button" class="task" @click="openTask(item.task_number)">#{{ item.task_number }}</button>
            <strong>{{ NOTIFICATION_META[item.notification_type]?.label ?? humanize(item.notification_type) }}</strong>
            <span class="faint small">acknowledged <RelativeTime :value="item.acknowledged_at" /></span>
          </p>
          <p class="small muted">{{ item.details.summary || item.details.message }}</p>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.group {
  border-color: var(--brass-line);
}

.task {
  border: 0;
  background: none;
  padding: 0;
  font-weight: 700;
  cursor: pointer;
  text-align: left;
  color: var(--ink);
}

.task:hover {
  color: var(--accent);
}

.list {
  list-style: none;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.list p {
  margin: 0 0 2px;
}

.line {
  display: flex;
  gap: 8px;
  align-items: center;
}

.issues {
  margin: 4px 0 0;
  padding-left: 1.2em;
  color: var(--danger);
  font-size: var(--text-sm);
}

.done {
  opacity: 0.75;
}
</style>
