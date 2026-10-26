<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Ban, CircleCheck, CircleDashed, CircleSlash, CircleX, LoaderCircle, Play, Settings2, Workflow } from 'lucide-vue-next'
import { api } from '@/api/http'
import type { PipelineConfig, PipelineRun, PipelineStepRun, PipelineStepSpec, TaskDetail } from '@/api/types'
import { humanSpan, parseTime } from '@/lib/format'
import { completedActions, errorMessage, useRequest } from '@/composables/useRequest'
import { usePrefs } from '@/stores/prefs'
import AppButton from '@/components/base/AppButton.vue'
import EmptyState from '@/components/base/EmptyState.vue'
import RelativeTime from '@/components/base/RelativeTime.vue'
import TagChip from '@/components/base/TagChip.vue'
import CodeEditor from '@/components/misc/CodeEditor.vue'

/** Bitbucket Pipelines style checks: configured steps run in the task's worktrees, with logs. */
const props = defineProps<{ detail: TaskDetail }>()
const prefs = usePrefs()
const { pending, run } = useRequest()

const runs = ref<PipelineRun[]>([])
const steps = ref<PipelineStepSpec[]>([])
const selectedRun = ref<string | null>(null)
const selectedStep = ref<number | null>(null)
const log = ref('')
const logOffset = ref(0)
const failure = ref('')
const editing = ref(false)
const config = ref<PipelineConfig | null>(null)
const draft = ref('')
const validation = ref('')
const valid = ref(true)
const logBox = ref<HTMLElement>()

const number = computed(() => props.detail.task.task_number)
const current = computed(() => runs.value.find((item) => item.id === selectedRun.value) ?? runs.value[0] ?? null)
const step = computed(() => current.value?.steps.find((item) => item.index === selectedStep.value) ?? null)
const running = computed(() => runs.value.some((item) => item.status === 'running'))
const hasWorktrees = computed(() => props.detail.task.derived.worktrees.length > 0)

async function load() {
  try {
    const result = await api.get<{ runs: PipelineRun[]; steps: PipelineStepSpec[] }>(`/tasks/${number.value}/pipelines`)
    runs.value = result.runs
    steps.value = result.steps
    failure.value = ''
    if (current.value && selectedStep.value === null) {
      const active = current.value.steps.find((item) => item.status === 'running' || item.status === 'failed')
      selectedStep.value = (active ?? current.value.steps[0])?.index ?? null
    }
  } catch (error) {
    failure.value = errorMessage(error)
  }
}

async function loadLog(reset = false) {
  if (!current.value || !step.value) return
  if (reset) {
    log.value = ''
    logOffset.value = 0
  }
  const box = logBox.value
  const atBottom = !box || box.scrollHeight - box.scrollTop - box.clientHeight < 40
  try {
    const chunk = await api.get<{ text: string; offset: number }>(
      `/tasks/${number.value}/pipelines/${current.value.id}/steps/${step.value.index}/log?offset=${logOffset.value}`,
    )
    log.value += chunk.text
    logOffset.value = chunk.offset
    if (atBottom && chunk.text) requestAnimationFrame(() => box && (box.scrollTop = box.scrollHeight))
  } catch {
    /* The run list reports problems. */
  }
}

watch([number, completedActions], load, { immediate: true })
watch([() => current.value?.id, selectedStep], () => void loadLog(true), { immediate: true })

let timer: number | undefined
onMounted(() => {
  timer = window.setInterval(() => {
    if (document.visibilityState !== 'visible') return
    if (running.value) void load()
    if (step.value && (step.value.status === 'running' || running.value)) void loadLog()
  }, 1500)
})
onBeforeUnmount(() => window.clearInterval(timer))

async function start() {
  const result = await run(() => api.post<{ run: PipelineRun }>(`/tasks/${number.value}/pipelines`, { actor: prefs.values.actor }), {
    task: number.value,
    toastErrors: true,
  })
  if (result?.run) {
    selectedRun.value = result.run.id
    selectedStep.value = result.run.steps[0]?.index ?? null
  }
}

async function cancel() {
  if (current.value) await run(() => api.post(`/tasks/${number.value}/pipelines/${current.value!.id}/cancel`, {}), { task: number.value, toastErrors: true })
}

async function openEditor() {
  config.value = await api.get<PipelineConfig>('/pipelines')
  draft.value = config.value.text
  validation.value = ''
  editing.value = true
}

let validateTimer: number | undefined
watch(draft, (text) => {
  window.clearTimeout(validateTimer)
  validateTimer = window.setTimeout(async () => {
    try {
      validation.value = (await api.post<{ message: string }>('/pipelines/validate', { text })).message
      valid.value = true
    } catch (error) {
      validation.value = errorMessage(error)
      valid.value = false
    }
  }, 300)
})

async function save() {
  const result = await run(() => api.put('/pipelines', { text: draft.value }), { toastErrors: true })
  if (result) editing.value = false
}

const ICONS = {
  pending: CircleDashed,
  running: LoaderCircle,
  passed: CircleCheck,
  failed: CircleX,
  skipped: CircleSlash,
  cancelled: Ban,
  interrupted: Ban,
}

function duration(item: { started_at: string | null; finished_at: string | null }): string {
  const start = parseTime(item.started_at)
  if (start === null) return ''
  const end = parseTime(item.finished_at) ?? Date.now()
  return humanSpan(Math.max(0, (end - start) / 1000))
}

const tone = (status: string) => (status === 'passed' ? 'ok' : status === 'running' ? 'accent' : status === 'pending' || status === 'skipped' ? 'default' : 'danger')

function selectRun(item: PipelineRun) {
  selectedRun.value = item.id
  const failed = item.steps.find((entry: PipelineStepRun) => entry.status === 'failed')
  selectedStep.value = (failed ?? item.steps[0])?.index ?? null
}
</script>

<template>
  <div class="pipelines">
    <div class="toolbar">
      <AppButton
        tone="primary"
        :icon="Play"
        :loading="pending"
        :disabled="running || !steps.length || !hasWorktrees"
        :title="!hasWorktrees ? 'Create the task worktrees first' : !steps.length ? 'Configure pipeline steps first' : running ? 'A pipeline is running' : 'Run every step in the task worktrees'"
        @click="start"
      >
        Run pipeline
      </AppButton>
      <AppButton v-if="current?.status === 'running'" tone="danger" :icon="Ban" @click="cancel">Stop</AppButton>
      <span class="spacer" />
      <AppButton tone="quiet" :icon="Settings2" @click="openEditor">Configure steps</AppButton>
    </div>

    <p v-if="failure" class="failure">{{ failure }}</p>

    <section v-if="editing" class="panel editor">
      <header class="panel-header">
        <h3>pipelines.toml</h3>
        <span class="faint small mono">{{ config?.path }}</span>
      </header>
      <div class="panel-body stack">
        <CodeEditor v-model="draft" label="Pipeline steps TOML" />
        <p class="small" :class="valid ? 'ok' : 'failure'">
          {{ validation }}
        </p>
        <p class="faint small">
          Each step is an argument array run in its repository's task worktree, never through a shell. A repository's steps stop at
          its first failure. The file lives next to config.toml and stays out of Git.
        </p>
        <div class="row">
          <AppButton tone="primary" :loading="pending" :disabled="!valid" @click="save">Save</AppButton>
          <AppButton tone="quiet" @click="editing = false">Cancel</AppButton>
        </div>
      </div>
    </section>

    <EmptyState v-if="!steps.length && !runs.length && !editing" :icon="Workflow" title="No pipeline steps for this task">
      Declare checks such as a build or the QA suite under Configure steps; they run in this task's worktrees, before review.
    </EmptyState>

    <ol v-else-if="steps.length && !runs.length" class="planned">
      <li v-for="item in steps" :key="`${item.repository}-${item.name}`">
        <CircleDashed :size="15" aria-hidden="true" />
        <strong>{{ item.name }}</strong>
        <span class="faint small">{{ item.repository }}</span>
        <code class="small">{{ item.command.join(' ') }}</code>
      </li>
    </ol>

    <div v-if="runs.length" class="layout">
      <section class="panel history">
        <header class="panel-header"><h3>Runs</h3></header>
        <ul>
          <li v-for="item in runs" :key="item.id">
            <button type="button" :class="{ active: current?.id === item.id }" @click="selectRun(item)">
              <component :is="ICONS[item.status]" :size="15" :class="['icon', item.status]" aria-hidden="true" />
              <span class="run-text">
                <span class="run-title">{{ item.status }}</span>
                <RelativeTime class="faint small" :value="item.started_at" />
              </span>
              <span class="faint small">{{ duration(item) }}</span>
            </button>
          </li>
        </ul>
      </section>

      <section v-if="current" class="panel detail" :data-pipeline="current.id">
        <header class="panel-header head">
          <TagChip :tone="tone(current.status)">{{ current.status }}</TagChip>
          <span class="small">started by <strong>{{ current.actor }}</strong> <RelativeTime :value="current.started_at" /></span>
          <span v-for="(head, repository) in current.heads" :key="repository" class="faint small mono">{{ repository }}@{{ head }}</span>
        </header>
        <div class="steps">
          <ol class="step-list">
            <li v-for="item in current.steps" :key="item.index">
              <button type="button" :class="{ active: step?.index === item.index }" @click="selectedStep = item.index">
                <component :is="ICONS[item.status]" :size="15" :class="['icon', item.status]" aria-hidden="true" />
                <span class="run-text">
                  <span class="run-title">{{ item.name }}</span>
                  <span class="faint small">{{ item.repository }}{{ item.exit_code !== null ? ` · exit ${item.exit_code}` : '' }}</span>
                </span>
                <span class="faint small">{{ duration(item) }}</span>
              </button>
            </li>
          </ol>
          <div class="log-pane">
            <p v-if="step" class="log-title mono small">{{ step.command.join(' ') }}</p>
            <pre ref="logBox" class="log" :aria-label="step ? `Log of ${step.name}` : 'Step log'">{{ log || (step?.status === 'pending' ? 'Waiting to start…' : step?.status === 'skipped' ? 'Skipped after an earlier failure.' : '') }}</pre>
          </div>
        </div>
        <p v-if="current.error" class="failure small">{{ current.error }}</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.pipelines {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5) var(--space-6);
}

.toolbar,
.row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.failure {
  color: var(--danger);
}

.ok {
  color: var(--ok);
}

.planned {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.planned li {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.layout {
  display: grid;
  grid-template-columns: minmax(180px, 230px) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.history ul,
.step-list {
  list-style: none;
  margin: 0;
  padding: 4px 0;
}

.history button,
.step-list button {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border: 0;
  background: none;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.history button:hover,
.step-list button:hover {
  background: var(--panel-2);
}

.history button.active,
.step-list button.active {
  background: var(--accent-soft);
}

.run-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.run-title {
  font-size: var(--text-sm);
  font-weight: 600;
  text-transform: capitalize;
}

.step-list .run-title {
  text-transform: none;
}

.icon.passed {
  color: var(--ok);
}

.icon.failed,
.icon.interrupted {
  color: var(--danger);
}

.icon.running {
  color: var(--accent);
  animation: spin 1s linear infinite;
}

.icon.pending,
.icon.skipped,
.icon.cancelled {
  color: var(--ink-3);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .icon.running {
    animation: none;
  }
}

.head {
  flex-wrap: wrap;
  gap: 10px;
}

.steps {
  display: grid;
  grid-template-columns: minmax(180px, 240px) minmax(0, 1fr);
  border-top: 1px solid var(--line);
}

.step-list {
  border-right: 1px solid var(--line);
}

.log-pane {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.log-title {
  margin: 0;
  padding: 6px 10px;
  border-bottom: 1px solid var(--line);
  color: var(--ink-2);
  overflow-wrap: anywhere;
}

.log {
  margin: 0;
  min-height: 260px;
  max-height: 560px;
  overflow: auto;
  padding: 10px;
  background: #11181d;
  color: #d9e2dd;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 900px) {
  .layout,
  .steps {
    grid-template-columns: 1fr;
  }
}
</style>
