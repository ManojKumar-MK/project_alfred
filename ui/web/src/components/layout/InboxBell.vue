<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bell, CheckCheck } from 'lucide-vue-next'
import { api } from '@/api/http'
import { parseTime, serverSkew } from '@/lib/format'
import { NOTIFICATION_META } from '@/lib/status'
import { useRequest } from '@/composables/useRequest'
import { useLive } from '@/stores/live'
import { usePrefs } from '@/stores/prefs'
import LampDot from '@/components/base/LampDot.vue'
import RelativeTime from '@/components/base/RelativeTime.vue'

/** A Bitbucket-style inbox: what needs you now and what happened earlier, with unread marks. */
const live = useLive()
const prefs = usePrefs()
const route = useRoute()
const router = useRouter()
const { run } = useRequest()
const open = ref(false)
const root = ref<HTMLElement>()

const EARLIER_LIMIT = 15

const readAt = computed(() => parseTime(prefs.values.inboxReadAt) ?? 0)
const isUnread = (created: string) => (parseTime(created) ?? 0) > readAt.value

const needs = computed(() =>
  live.calls.map((call) => ({
    ...call,
    title: call.task === null ? call.label : `${call.label} · #${call.task} ${live.taskMap.get(call.task)?.title ?? ''}`,
    unread: isUnread(call.created),
  })),
)

const earlier = computed(() =>
  live.notifications
    .filter((item) => item.acknowledged)
    .slice(-EARLIER_LIMIT)
    .reverse()
    .map((item) => ({
      id: item.notification_id,
      task: item.task_number,
      title: `${NOTIFICATION_META[item.notification_type]?.label ?? item.notification_type} · #${item.task_number}`,
      detail: String(item.details.summary ?? item.details.message ?? ''),
      created: item.created_at,
    })),
)

const unread = computed(() => needs.value.filter((item) => item.unread).length)

function markAllRead() {
  // Item times come from the server, so read them against the server's clock.
  prefs.values.inboxReadAt = new Date(Date.now() + serverSkew.value).toISOString()
}

function openTask(task: number | null) {
  open.value = false
  if (task === null) void router.push('/operations')
  else void router.push({ path: route.path, query: { ...route.query, task: String(task) } })
}

async function acknowledge(task: number) {
  await run(() => api.post('/notifications/ack', { task }), { command: `alfred notifications ack --task ${task}`, task })
}

function onPointer(event: PointerEvent) {
  if (root.value && !root.value.contains(event.target as Node)) open.value = false
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

watch(open, (value) => {
  if (value) {
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
  } else {
    document.removeEventListener('pointerdown', onPointer)
    document.removeEventListener('keydown', onKey)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointer)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="inbox">
    <button
      type="button"
      class="bell"
      :aria-expanded="open"
      aria-haspopup="dialog"
      :aria-label="unread ? `Inbox, ${unread} unread` : 'Inbox'"
      @click="open = !open"
    >
      <Bell :size="18" aria-hidden="true" />
      <span v-if="unread" class="count">{{ unread > 99 ? '99+' : unread }}</span>
    </button>

    <div v-if="open" class="panel-pop" role="dialog" aria-label="Inbox">
      <header class="pop-head">
        <h2>Inbox</h2>
        <span class="spacer" />
        <button type="button" class="link" :disabled="!unread" @click="markAllRead">
          <CheckCheck :size="14" aria-hidden="true" /> Mark all read
        </button>
      </header>

      <div class="scroll">
        <h3 class="section">Needs you</h3>
        <p v-if="!needs.length" class="empty">Nothing needs you right now.</p>
        <ul v-else class="items">
          <li v-for="item in needs" :key="item.key" :class="{ unread: item.unread }" :data-inbox="item.kind">
            <button type="button" class="item" @click="openTask(item.task)">
              <LampDot :color="item.tone === 'danger' ? 'var(--danger)' : 'var(--brass-strong)'" :lit="item.unread" :size="8" />
              <span class="text">
                <span class="title">{{ item.title }}</span>
                <span v-if="item.detail" class="detail">{{ item.detail }}</span>
              </span>
              <RelativeTime v-if="item.created" class="time" :value="item.created" />
            </button>
            <button
              v-if="item.task !== null && ['review', 'failed', 'blocked', 'session', 'problem'].includes(item.kind)"
              type="button"
              class="ack"
              :title="`alfred notifications ack --task ${item.task}`"
              @click="acknowledge(item.task)"
            >
              Acknowledge
            </button>
          </li>
        </ul>

        <template v-if="earlier.length">
          <h3 class="section">Earlier</h3>
          <ul class="items">
            <li v-for="item in earlier" :key="item.id" class="done">
              <button type="button" class="item" @click="openTask(item.task)">
                <LampDot color="var(--lamp-off)" :size="8" />
                <span class="text">
                  <span class="title">{{ item.title }}</span>
                  <span v-if="item.detail" class="detail">{{ item.detail }}</span>
                </span>
                <RelativeTime class="time" :value="item.created" />
              </button>
            </li>
          </ul>
        </template>
      </div>

      <footer class="pop-foot">
        <RouterLink to="/notifications" @click="open = false">All notifications</RouterLink>
        <RouterLink to="/settings#preferences" @click="open = false">Desktop alerts</RouterLink>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.inbox {
  position: relative;
}

.bell {
  position: relative;
  display: inline-grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid transparent;
  border-radius: var(--radius);
  background: transparent;
  color: var(--ink-2);
  cursor: pointer;
}

.bell:hover,
.bell[aria-expanded='true'] {
  border-color: var(--line);
  background: var(--panel);
  color: var(--ink);
}

.count {
  position: absolute;
  top: 1px;
  right: 0;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: var(--danger);
  color: #fff;
  font-size: 10.5px;
  font-weight: 700;
  line-height: 17px;
}

.panel-pop {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: min(400px, calc(100vw - 32px));
  max-height: min(560px, calc(100vh - 90px));
  border: 1px solid var(--line);
  border-radius: var(--radius-l);
  background: var(--panel);
  box-shadow: var(--shadow-float);
}

.pop-head,
.pop-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
}

.pop-head {
  border-bottom: 1px solid var(--line);
}

.pop-head h2 {
  margin: 0;
  font-size: var(--text-md);
}

.pop-foot {
  justify-content: space-between;
  border-top: 1px solid var(--line);
  font-size: var(--text-sm);
}

.link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: none;
  color: var(--accent);
  font-size: var(--text-sm);
  cursor: pointer;
}

.link:disabled {
  color: var(--ink-3);
  cursor: default;
}

.scroll {
  overflow-y: auto;
  padding-bottom: 6px;
}

.section {
  margin: 10px 14px 4px;
  font-size: var(--text-xs);
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.empty {
  margin: 4px 14px 8px;
  font-size: var(--text-sm);
  color: var(--ink-2);
}

.items {
  list-style: none;
  margin: 0;
  padding: 0;
}

.items li {
  display: flex;
  align-items: center;
  gap: 4px;
  padding-right: 10px;
}

.items li.unread {
  background: color-mix(in srgb, var(--accent-soft) 55%, transparent);
}

.item {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 4px 8px 14px;
  border: 0;
  background: none;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.item > :first-child {
  margin-top: 5px;
}

.item:hover .title {
  text-decoration: underline;
}

.text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.title {
  font-size: var(--text-sm);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.unread .title {
  font-weight: 750;
}

.detail {
  font-size: var(--text-xs);
  color: var(--ink-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.done .title {
  font-weight: 500;
  color: var(--ink-2);
}

.time {
  flex: none;
  font-size: var(--text-xs);
  color: var(--ink-3);
  white-space: nowrap;
}

.ack {
  flex: none;
  border: 1px solid var(--line);
  border-radius: var(--radius-s);
  background: var(--panel);
  color: var(--ink-2);
  font-size: var(--text-xs);
  padding: 2px 6px;
  cursor: pointer;
}

.ack:hover {
  color: var(--ink);
  border-color: var(--line-strong);
}
</style>
