<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, Search } from 'lucide-vue-next'
import { useLive } from '@/stores/live'
import { usePrefs } from '@/stores/prefs'
import { useDialogs } from '@/stores/dialogs'
import LampDot from '@/components/base/LampDot.vue'
import AppButton from '@/components/base/AppButton.vue'
import InboxBell from './InboxBell.vue'

const live = useLive()
const prefs = usePrefs()
const dialogs = useDialogs()
const router = useRouter()

const workspace = computed(() => {
  const root = live.config?.workspace_root ?? ''
  return root.split('/').filter(Boolean).pop() ?? ''
})

const connection = computed(() => {
  switch (live.connection) {
    case 'live':
      return { label: 'Live', color: 'var(--lamp-running)', lit: true, title: 'Receiving live updates' }
    case 'connecting':
      return { label: 'Connecting', color: 'var(--lamp-queued)', lit: false, title: 'Connecting to alfred-ui' }
    case 'reconnecting':
      return { label: 'Reconnecting', color: 'var(--lamp-queued)', lit: true, title: 'Lost the live connection; retrying' }
    default:
      return { label: 'Offline', color: 'var(--lamp-blocked)', lit: true, title: 'The alfred-ui server is not reachable' }
  }
})

const tmux = computed(() => live.snapshot?.tmux)
const coordinator = computed(() => live.snapshot?.coordinator)
const learner = computed(() => live.snapshot?.learner)
</script>

<template>
  <header class="topbar">
    <button
      type="button"
      class="workspace"
      :title="live.snapshot?.workspace.config_path || 'No workspace open'"
      @click="router.push('/settings')"
    >
      <span class="name">{{ workspace || 'No workspace' }}</span>
      <span v-if="live.config" class="prefix mono">{{ live.config.runtime.session_prefix }}-*</span>
    </button>

    <div class="lamps" role="status" aria-label="System status">
      <span class="lamp" :title="connection.title">
        <LampDot :color="connection.color" :lit="connection.lit" :pulse="live.connection === 'reconnecting'" />
        {{ connection.label }}
      </span>
      <span
        class="lamp"
        :title="
          tmux?.available
            ? tmux.server_running
              ? 'tmux server running'
              : 'tmux available; no server yet'
            : 'tmux not found; dispatches follow tmux_unavailable_policy'
        "
      >
        <LampDot :color="tmux?.available ? 'var(--lamp-running)' : 'var(--lamp-blocked)'" :lit="!!tmux?.server_running" />
        tmux
      </span>
      <RouterLink class="lamp" to="/operations" :title="coordinator?.running ? `Coordinator running in ${coordinator.session_name}` : 'Coordinator stopped'">
        <LampDot color="var(--lamp-running)" :lit="!!coordinator?.running" :pulse="live.isBusy(coordinator?.session_name)" />
        Coordinator
      </RouterLink>
      <RouterLink class="lamp" to="/operations" :title="learner?.running ? `Learner running (${learner.agent})` : 'Learner stopped'">
        <LampDot color="var(--lamp-review)" :lit="!!learner?.running" :pulse="live.isBusy(learner?.session_name)" />
        Learner
      </RouterLink>
    </div>

    <span class="spacer" />

    <button type="button" class="jump" @click="dialogs.palette = true">
      <Search :size="15" aria-hidden="true" />
      <span>Jump to a task or action</span>
      <kbd>⌘K</kbd>
    </button>
    <InboxBell />
    <RouterLink to="/settings#preferences" class="actor" title="The actor recorded for your actions">
      Acting as <strong class="mono">{{ prefs.values.actor }}</strong>
    </RouterLink>
    <AppButton tone="primary" :icon="Plus" :disabled="!live.config" @click="dialogs.openTaskForm('create')">
      New task
    </AppButton>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: var(--topbar-height);
  padding: 0 var(--space-5);
  border-bottom: 1px solid var(--line);
  background: color-mix(in srgb, var(--paper) 92%, transparent);
  backdrop-filter: blur(6px);
}

.workspace {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  border: 0;
  background: transparent;
  padding: 2px 6px;
  border-radius: var(--radius);
  cursor: pointer;
  line-height: 1.2;
  max-width: 280px;
  min-width: 0;
}

.workspace:hover {
  background: var(--panel-2);
}

.name {
  font-weight: 750;
  font-stretch: 110%;
  font-size: var(--text-md);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.prefix {
  font-size: 11px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.lamps {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  font-size: var(--text-sm);
  color: var(--ink-2);
}

.lamp {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: inherit;
  text-decoration: none;
  white-space: nowrap;
}

a.lamp:hover {
  color: var(--ink);
}

.jump {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--ink-2);
  font-size: var(--text-sm);
  cursor: pointer;
}

.jump:hover {
  border-color: var(--line-strong);
  color: var(--ink);
}

.actor {
  font-size: var(--text-sm);
  color: var(--ink-2);
  text-decoration: none;
  white-space: nowrap;
}

.actor strong {
  color: var(--ink);
  font-weight: 600;
}

@media (max-width: 1180px) {
  .jump span,
  .jump kbd,
  .actor {
    display: none;
  }
}

@media (max-width: 760px) {
  .topbar {
    gap: var(--space-3);
    padding: 0 var(--space-3);
  }

  .lamps .lamp:not(:first-child) {
    display: none;
  }
}
</style>
