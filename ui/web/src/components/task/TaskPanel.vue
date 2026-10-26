<script setup lang="ts">
import { computed } from 'vue'
import { Activity, Bot, FileText, GitCompareArrows, GitPullRequestArrow, LayoutList, ScrollText, SquareTerminal, Workflow } from 'lucide-vue-next'
import type { TaskDetail } from '@/api/types'
import { PHASE_META, PLANNING_LABELS, statusLabel, statusLamp } from '@/lib/status'
import { useLive } from '@/stores/live'
import StatusPill from '@/components/base/StatusPill.vue'
import TabBar from '@/components/base/TabBar.vue'
import TagChip from '@/components/base/TagChip.vue'
import JourneyBar from '@/components/misc/JourneyBar.vue'
import TaskActions from './TaskActions.vue'
import TaskOverview from './TaskOverview.vue'
import TaskPlan from './TaskPlan.vue'
import TaskAgents from './TaskAgents.vue'
import TaskTerminal from './TaskTerminal.vue'
import TaskChanges from './TaskChanges.vue'
import TaskReview from './TaskReview.vue'
import TaskPipelines from './TaskPipelines.vue'
import TaskActivity from './TaskActivity.vue'
import TaskFiles from './TaskFiles.vue'

/** Header, lifecycle, actions, and tabbed detail for one task. */
const props = defineProps<{ detail: TaskDetail; tab: string; headingId?: string }>()
const emit = defineEmits<{ tab: [string] }>()
const live = useLive()

const task = computed(() => props.detail.task)
const derived = computed(() => task.value.derived)
const tabs = computed(() => {
  const changes = derived.value.worktrees.reduce((sum, item) => sum + item.changes, 0)
  return [
    { key: 'overview', label: 'Overview', icon: LayoutList },
    {
      key: 'plan',
      label: 'Plan',
      icon: ScrollText,
      badge: derived.value.plan.awaiting_approval && derived.value.plan.reported ? 'ready' : null,
      tone: 'brass' as const,
    },
    { key: 'agents', label: 'Agents and runs', icon: Bot, badge: derived.value.run_count || null },
    {
      key: 'terminal',
      label: 'Terminal',
      icon: SquareTerminal,
      badge: derived.value.session?.alive ? 'live' : null,
    },
    { key: 'changes', label: 'Changes', icon: GitCompareArrows, badge: changes || null, tone: 'brass' as const },
    {
      key: 'review',
      label: 'Review',
      icon: GitPullRequestArrow,
      badge: task.value.status === 'MR in Review' && !derived.value.approved_since_last_work ? 'open' : null,
      tone: 'brass' as const,
    },
    { key: 'pipelines', label: 'Pipelines', icon: Workflow },
    {
      key: 'activity',
      label: 'Activity',
      icon: Activity,
      badge: derived.value.notifications.pending || null,
      tone: 'brass' as const,
    },
    { key: 'files', label: 'Files', icon: FileText },
  ]
})

const current = computed({
  get: () => (tabs.value.some((item) => item.key === props.tab) ? props.tab : 'overview'),
  set: (value: string) => emit('tab', value),
})
</script>

<template>
  <article class="task-panel" :data-task-panel="task.task_number">
    <header class="head">
      <div class="title-row">
        <span class="number">#{{ task.task_number }}</span>
        <h1 :id="headingId" class="title">{{ task.title }}</h1>
      </div>
      <div class="facts">
        <StatusPill
          :label="statusLabel(task.status)"
          :color="statusLamp(task.status)"
          :lit="!!derived.session?.alive"
          :pulse="live.isBusy(derived.session?.name)"
          :title="derived.session?.alive ? `Session ${derived.session.name} is live` : undefined"
        />
        <TagChip :tone="task.priority === 'P0' ? 'danger' : task.priority === 'P1' ? 'brass' : 'default'">{{ task.priority }}</TagChip>
        <TagChip>{{ PHASE_META[task.lifecycle_phase].label }}</TagChip>
        <TagChip v-if="task.execution_mode === 'plan-execution'" :tone="derived.plan.awaiting_approval ? 'brass' : 'default'">
          {{ PLANNING_LABELS[task.planning_state] }}
        </TagChip>
        <TagChip v-if="task.branch_name" mono>{{ task.branch_name }}</TagChip>
        <TagChip v-if="task.assigned_agent_alias" tone="accent">{{ task.assigned_agent_alias }}</TagChip>
        <TagChip v-if="derived.approved_since_last_work" tone="ok">Approved</TagChip>
      </div>
      <TaskActions :task="task" />
      <JourneyBar :steps="derived.journey" />
    </header>
    <TabBar v-model="current" :tabs="tabs" label="Task sections" />
    <div class="content">
      <TaskOverview v-if="current === 'overview'" :detail="detail" @tab="current = $event" />
      <TaskPlan v-else-if="current === 'plan'" :detail="detail" @tab="current = $event" />
      <TaskAgents v-else-if="current === 'agents'" :detail="detail" />
      <TaskTerminal v-else-if="current === 'terminal'" :detail="detail" />
      <TaskChanges v-else-if="current === 'changes'" :detail="detail" />
      <TaskReview v-else-if="current === 'review'" :detail="detail" @tab="current = $event" />
      <TaskPipelines v-else-if="current === 'pipelines'" :detail="detail" />
      <TaskActivity v-else-if="current === 'activity'" :detail="detail" />
      <TaskFiles v-else-if="current === 'files'" :detail="detail" />
    </div>
  </article>
</template>

<style scoped>
.task-panel {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.head {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-5) var(--space-4);
}

.title-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  padding-right: 40px;
}

.number {
  font-size: var(--text-lg);
  font-weight: 800;
  font-stretch: 120%;
  color: var(--ink-3);
}

.title {
  font-size: var(--text-xl);
  font-stretch: 112%;
  overflow-wrap: anywhere;
}

.facts {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.content {
  flex: 1;
  background: var(--paper);
}
</style>
