<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { signInRequired } from '@/api/http'
import { useDesktopAlerts } from '@/composables/useDesktopAlerts'
import { setTabBadge } from '@/lib/alerts'
import { startClock } from '@/lib/format'
import { NOTIFICATION_META } from '@/lib/status'
import { useDialogs } from '@/stores/dialogs'
import { useLive } from '@/stores/live'
import { usePrefs } from '@/stores/prefs'
import { useToasts } from '@/stores/toasts'
import NavRail from '@/components/layout/NavRail.vue'
import TopBar from '@/components/layout/TopBar.vue'
import CallBoard from '@/components/layout/CallBoard.vue'
import ToastStack from '@/components/layout/ToastStack.vue'
import TaskDrawer from '@/components/task/TaskDrawer.vue'
import SessionTerminalDialog from '@/components/terminal/SessionTerminalDialog.vue'
import ActionDialog from '@/components/dialogs/ActionDialog.vue'
import TaskFormDialog from '@/components/dialogs/TaskFormDialog.vue'
import TriggerDialog from '@/components/dialogs/TriggerDialog.vue'
import ConfirmDialog from '@/components/dialogs/ConfirmDialog.vue'
import CommandPalette from '@/components/dialogs/CommandPalette.vue'
import SignInView from '@/views/SignInView.vue'

const live = useLive()
usePrefs() // Creating the store applies the remembered theme.
const dialogs = useDialogs()
const toasts = useToasts()
const route = useRoute()

const pendingCount = computed(() => live.calls.filter((call) => call.task !== null).length)

watch(
  [() => route.meta.title, pendingCount],
  ([title, count]) => {
    document.title = `${count ? `(${count}) ` : ''}${title ?? 'Alfred'} · Alfred`
  },
  { immediate: true },
)

// Everything waiting, including workspace-wide calls, shows on the tab icon.
watch(() => live.calls.length, setTabBadge, { immediate: true })

// Announce new notifications as they arrive: a toast, and a desktop alert when enabled.
const announced = new Set<string>()
let primed = false
watch(
  () => live.notifications,
  (items) => {
    const fresh = items.filter((item) => !item.acknowledged && !announced.has(item.notification_id))
    for (const item of items) announced.add(item.notification_id)
    if (!primed) {
      primed = live.snapshot !== null
      return
    }
    for (const item of fresh) {
      const meta = NOTIFICATION_META[item.notification_type]
      const title = `${meta?.label ?? item.notification_type}: task ${item.task_number}`
      const detail = String(item.details.summary ?? item.details.message ?? '')
      toasts.push({ tone: item.notification_type === 'task_completed' ? 'brass' : 'error', title, detail, task: item.task_number }, 12000)
    }
  },
)

useDesktopAlerts()

// Fade out the splash from index.html once there is something to show instead.
const SPLASH_MINIMUM_MS = 450
const ready = computed(() => live.snapshot !== null || signInRequired.value || live.connection === 'offline')
watch(
  ready,
  (value) => {
    if (!value) return
    const splash = document.getElementById('splash')
    if (!splash) return
    window.setTimeout(() => {
      splash.classList.add('done')
      window.setTimeout(() => splash.remove(), 400)
    }, Math.max(0, SPLASH_MINIMUM_MS - performance.now()))
  },
  { immediate: true },
)

function onKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    dialogs.palette = !dialogs.palette
  }
}

onMounted(() => {
  startClock()
  live.connect()
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  live.disconnect()
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <SignInView v-if="signInRequired" />
  <div v-else class="shell">
    <NavRail />
    <div class="main">
      <TopBar />
      <CallBoard />
      <main id="content">
        <RouterView />
      </main>
    </div>
    <TaskDrawer />
    <SessionTerminalDialog />
    <ActionDialog />
    <TaskFormDialog />
    <TriggerDialog />
    <ConfirmDialog />
    <CommandPalette />
    <ToastStack />
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

main {
  flex: 1;
  min-width: 0;
}
</style>
