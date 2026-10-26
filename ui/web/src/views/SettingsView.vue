<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  FolderOpen,
  FolderPlus,
  Hammer,
  HeartPulse,
  NotebookPen,
  RefreshCw,
  RotateCw,
  Save,
  Server,
  SlidersHorizontal,
  Trash2,
  Undo2,
  Upload,
  Wrench,
} from 'lucide-vue-next'
import { api } from '@/api/http'
import type {
  ActionResult,
  ConfigPayload,
  ConfigValidation,
  DoctorCheck,
  RebuildResult,
  ServerStatus,
  SkillEntry,
  SkillListing,
} from '@/api/types'
import { absolute } from '@/lib/format'
import { alfredCommand, shellJoin, shellQuote } from '@/lib/cli'
import { splitCommand } from '@/lib/shellwords'
import { useAlertSwitch } from '@/composables/useDesktopAlerts'
import { errorMessage, useRequest } from '@/composables/useRequest'
import { useLive } from '@/stores/live'
import { usePrefs } from '@/stores/prefs'
import { useToasts } from '@/stores/toasts'
import AppButton from '@/components/base/AppButton.vue'
import CommandLine from '@/components/base/CommandLine.vue'
import FormField from '@/components/base/FormField.vue'
import KeyValue from '@/components/base/KeyValue.vue'
import LampDot from '@/components/base/LampDot.vue'
import SegmentedControl from '@/components/base/SegmentedControl.vue'
import SelectInput from '@/components/base/SelectInput.vue'
import TabBar from '@/components/base/TabBar.vue'
import TagChip from '@/components/base/TagChip.vue'
import TextArea from '@/components/base/TextArea.vue'
import TextInput from '@/components/base/TextInput.vue'
import ToggleSwitch from '@/components/base/ToggleSwitch.vue'
import CodeEditor from '@/components/misc/CodeEditor.vue'

const live = useLive()
const prefs = usePrefs()
const toasts = useToasts()
const alerts = useAlertSwitch()
const route = useRoute()
const router = useRouter()

const sections = [
  { key: 'workspace', label: 'Workspace', icon: FolderOpen },
  { key: 'configuration', label: 'Configuration', icon: Wrench },
  { key: 'skills', label: 'Skills', icon: NotebookPen },
  { key: 'health', label: 'Health checks', icon: HeartPulse },
  { key: 'server', label: 'Server', icon: Server },
  { key: 'migrate', label: 'Legacy migration', icon: Upload },
  { key: 'preferences', label: 'Preferences', icon: SlidersHorizontal },
]
const section = computed({
  get: () => {
    const hash = route.hash.replace('#', '')
    return sections.some((item) => item.key === hash) ? hash : 'workspace'
  },
  set: (value: string) => void router.replace({ hash: `#${value}`, query: route.query }),
})

// Workspace ---------------------------------------------------------------------------
const RECENT_KEY = 'alfred-ui:recent-workspaces'
const openPath = ref('')
const openInvalid = ref(false)
const initRoot = ref('')
const initForce = ref(false)
const workspace = useRequest()
const recent = ref<string[]>(loadRecent())

function loadRecent(): string[] {
  try {
    return JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? '[]') as string[]
  } catch {
    return []
  }
}

function remember(path: string) {
  if (!path) return
  recent.value = [path, ...recent.value.filter((item) => item !== path)].slice(0, 8)
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(recent.value))
  } catch {
    /* Recent workspaces are only a convenience. */
  }
}

watch(
  () => live.snapshot?.workspace.config_path,
  (path) => {
    if (path) remember(path)
  },
  { immediate: true },
)

async function openWorkspace(path = openPath.value) {
  const result = await workspace.run(
    () => api.post<ActionResult>('/workspace/open', { path, validate_config: !openInvalid.value }),
    { toastErrors: false },
  )
  if (result) {
    openPath.value = ''
    void loadConfig()
  }
}

async function initialize() {
  const result = await workspace.run(
    () => api.post<ActionResult & { config_path: string }>('/workspace/init', { root: initRoot.value, force: initForce.value }),
    { command: alfredCommand('init', ['--root', initRoot.value], initForce.value ? '--force' : false) },
  )
  if (result) {
    initRoot.value = ''
    section.value = 'configuration'
    void loadConfig()
  }
}

// Configuration ------------------------------------------------------------------------
const config = ref<ConfigPayload | null>(null)
const text = ref('')
const saved = ref('')
const validation = ref<ConfigValidation | null>(null)
const configFailure = ref('')
const saving = useRequest()
const editor = ref<InstanceType<typeof CodeEditor>>()
let validateTimer: number | undefined

async function loadConfig() {
  if (!live.snapshot?.workspace.config_path) return
  try {
    config.value = await api.get<ConfigPayload>('/config')
    text.value = config.value.text
    saved.value = config.value.text
    validation.value = config.value.validation
    configFailure.value = ''
  } catch (error) {
    configFailure.value = errorMessage(error)
  }
}

watch(() => live.snapshot?.workspace.config_path, loadConfig, { immediate: true })

watch(text, (value) => {
  window.clearTimeout(validateTimer)
  validateTimer = window.setTimeout(async () => {
    try {
      validation.value = await api.post<ConfigValidation>('/config/validate', { text: value })
    } catch (error) {
      validation.value = { valid: false, error: errorMessage(error) }
    }
  }, 500)
})

const dirty = computed(() => text.value !== saved.value)
const errorLine = computed(() => {
  const match = /line (\d+)/.exec(validation.value?.error ?? '')
  return match ? Number(match[1]) : null
})

async function saveConfig() {
  const result = await saving.run(() => api.put<ActionResult & { backup: string }>('/config', { text: text.value }), {})
  if (result) {
    saved.value = text.value
    toasts.push({ tone: 'info', title: 'The previous configuration was kept', detail: String(result.backup) })
  }
}

function revert() {
  text.value = saved.value
}

function append(snippet: string) {
  text.value = `${text.value.replace(/\s*$/, '')}\n\n${snippet.trim()}\n`
}

const repository = reactive({ name: '', path: '', default_branch: 'main', remote: 'origin', selected_by_default: false })
const repositoryError = ref('')
async function addRepository() {
  repositoryError.value = ''
  if (!repository.name.trim() || !repository.path.trim()) {
    repositoryError.value = 'Name and path are required.'
    return
  }
  const result = await api.post<{ text: string }>('/config/snippets/repository', repository)
  append(result.text)
  Object.assign(repository, { name: '', path: '', default_branch: 'main', remote: 'origin', selected_by_default: false })
}

const agent = reactive({ preset: 'claude', alias: '', runtime_target: '', direct: '', plan: '', execution: '' })
const agentError = ref('')
watch(
  () => [agent.preset, config.value?.presets] as const,
  ([preset, presets]) => {
    const chosen = presets?.[preset]
    if (!chosen) return
    agent.alias = agent.alias || preset
    agent.runtime_target = chosen.runtime_target
    agent.direct = shellJoin(chosen.commands.direct)
    agent.plan = shellJoin(chosen.commands.plan)
    agent.execution = shellJoin(chosen.commands.execution)
  },
  { immediate: true },
)
async function addAgent() {
  agentError.value = ''
  try {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(agent.alias)) throw new Error('The alias may contain letters, numbers, underscores, and hyphens.')
    const commands = { direct: splitCommand(agent.direct), plan: splitCommand(agent.plan), execution: splitCommand(agent.execution) }
    if (!commands.direct.length || !commands.plan.length || !commands.execution.length) throw new Error('Every phase needs a command.')
    const result = await api.post<{ text: string }>('/config/snippets/agent', {
      alias: agent.alias,
      runtime_target: agent.runtime_target,
      ...commands,
    })
    append(result.text)
    agent.alias = ''
  } catch (error) {
    agentError.value = errorMessage(error)
  }
}

// Skills ------------------------------------------------------------------------------
const SKILL_LABELS: Record<SkillEntry['phase'], { title: string; help: string }> = {
  plan: { title: 'Plan skill', help: 'Appended to planning prompts of plan-execution tasks.' },
  execution: { title: 'Execution skill', help: 'Appended to execution prompts, including direct tasks.' },
}
const skills = ref<SkillListing | null>(null)
const skillText = reactive<Record<SkillEntry['phase'], string>>({ plan: '', execution: '' })
const skillsFailure = ref('')
const skillSaving = useRequest()

async function loadSkills() {
  if (!live.snapshot?.workspace.config_path) return
  try {
    skills.value = await api.get<SkillListing>('/skills')
    for (const item of skills.value.skills) skillText[item.phase] = item.content
    skillsFailure.value = ''
  } catch (error) {
    skillsFailure.value = errorMessage(error)
  }
}

// Also wait for the first snapshot: opening #skills directly runs this before the workspace is known.
watch(
  () => [section.value, live.snapshot?.workspace.config_path] as const,
  ([value]) => {
    if (value === 'skills') void loadSkills()
  },
  { immediate: true },
)

function skillDirty(item: SkillEntry): boolean {
  return skillText[item.phase].trim() !== item.content
}

async function saveSkill(item: SkillEntry) {
  const result = await skillSaving.run(() => api.put<ActionResult>(`/skills/${item.phase}`, { content: skillText[item.phase] }), {
    command: alfredCommand('skill', 'set', ['--phase', item.phase], ['--file', item.path]),
  })
  if (result) void loadSkills()
}

async function removeSkill(item: SkillEntry) {
  const result = await skillSaving.run(() => api.post<ActionResult>(`/skills/${item.phase}/remove`), {
    command: alfredCommand('skill', 'remove', ['--phase', item.phase]),
  })
  if (result) void loadSkills()
}

// Server ------------------------------------------------------------------------------
const server = ref<ServerStatus | null>(null)
const serverFailure = ref('')
const rebuildResult = ref<RebuildResult | null>(null)
const serverBusy = ref<'' | 'rebuild' | 'restart'>('')
const restartProblem = ref('')

async function loadServer() {
  try {
    server.value = await api.get<ServerStatus>('/server')
    serverFailure.value = ''
  } catch (error) {
    serverFailure.value = errorMessage(error)
  }
}

watch(section, (value) => {
  if (value === 'server') void loadServer()
}, { immediate: true })

/** Rebuild the frontend from this checkout; the result, including a failure, is shown inline. */
async function rebuild(): Promise<boolean> {
  serverBusy.value = 'rebuild'
  rebuildResult.value = null
  try {
    rebuildResult.value = await api.post<RebuildResult>('/server/rebuild')
    if (!rebuildResult.value.ok) toasts.push({ tone: 'error', title: rebuildResult.value.message })
    return rebuildResult.value.ok
  } catch (error) {
    toasts.push({ tone: 'error', title: 'Rebuild failed', detail: errorMessage(error) })
    return false
  } finally {
    serverBusy.value = ''
    void loadServer()
  }
}

/** Restart the server, wait until a new process answers, then reload the page to use it. */
async function restart() {
  const before = server.value?.started_at
  serverBusy.value = 'restart'
  restartProblem.value = ''
  try {
    await api.post<ActionResult>('/server/restart')
  } catch (error) {
    restartProblem.value = errorMessage(error)
    serverBusy.value = ''
    return
  }
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    await new Promise((done) => window.setTimeout(done, 1000))
    try {
      const current = await api.get<ServerStatus>('/server')
      if (current.started_at !== before) {
        window.location.reload()
        return
      }
    } catch {
      /* The server is down while it restarts. */
    }
  }
  serverBusy.value = ''
  restartProblem.value = 'The server did not come back within a minute. Check the terminal it runs in.'
}

function reloadPage() {
  window.location.reload()
}

async function updateAndRestart() {
  if (server.value?.rebuild.available && !(await rebuild())) return
  await restart()
}

// Health ------------------------------------------------------------------------------
const checks = ref<DoctorCheck[]>([])
const checking = ref(false)
const fixing = useRequest()
async function runChecks() {
  checking.value = true
  try {
    checks.value = (await api.get<{ checks: DoctorCheck[] }>('/doctor')).checks
  } catch (error) {
    checks.value = [{ key: 'failure', title: 'Health checks', status: 'error', detail: errorMessage(error), fix: null }]
  } finally {
    checking.value = false
  }
}
watch(section, (value) => {
  if (value === 'health') void runChecks()
}, { immediate: true })

async function fix(check: DoctorCheck) {
  if (check.fix?.action === 'tmux-path') {
    await fixing.run(() => api.post<ActionResult>('/doctor/tmux-path'), { toastErrors: true })
    void runChecks()
  }
}

const lamp: Record<DoctorCheck['status'], string> = {
  ok: 'var(--lamp-running)',
  info: 'var(--lamp-progress)',
  warn: 'var(--lamp-queued)',
  error: 'var(--lamp-blocked)',
}

// Migration -----------------------------------------------------------------------------
const migration = reactive({ source: '', migration_directory: '' })
const migrating = useRequest()
const migrationResult = ref<Record<string, unknown> | null>(null)
async function migrate() {
  migrationResult.value = await migrating.run(() => api.post<ActionResult>('/migrate', migration), {
    command: alfredCommand('migrate', ['--source', migration.source], ['--migration-directory', migration.migration_directory]),
  })
}
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>Settings</h1>
        <p>The workspace this UI manages, its configuration, and checks that agents can actually do their work.</p>
      </div>
    </header>

    <TabBar v-model="section" :tabs="sections" label="Settings sections" class="tabs" />

    <div v-if="section === 'workspace'" class="grid">
      <section class="panel">
        <header class="panel-header"><h3>Current workspace</h3></header>
        <div class="panel-body stack">
          <p v-if="!live.snapshot?.workspace.config_path" class="muted">No workspace is open.</p>
          <KeyValue
            v-else
            :items="[
              { label: 'Configuration', value: live.snapshot.workspace.config_path, mono: true },
              { label: 'Workspace root', value: live.config?.workspace_root, mono: true },
              { label: 'State', value: live.config?.runtime.state_directory, mono: true },
              { label: 'Prompts and handoffs', value: live.config?.runtime.temp_directory, mono: true },
              { label: 'Worktrees', value: live.config?.runtime.worktree_directory, mono: true },
              { label: 'Session prefix', value: live.config?.runtime.session_prefix, mono: true },
              { label: 'When tmux is missing', value: live.config?.runtime.tmux_unavailable_policy === 'queue' ? 'Queue the dispatch' : 'Fail the dispatch' },
              { label: 'Timezone', value: live.config?.runtime.timezone },
            ]"
          />
          <p v-if="live.error" class="failure">{{ live.error.message }}</p>
        </div>
      </section>

      <section class="panel">
        <header class="panel-header"><h3>Open another workspace</h3></header>
        <form class="panel-body stack" @submit.prevent="openWorkspace()">
          <FormField label="Configuration file or project folder" for-id="open-path" help="A .alfred/config.toml path, or the folder that contains .alfred.">
            <TextInput id="open-path" v-model="openPath" mono placeholder="/path/to/project" />
          </FormField>
          <ToggleSwitch v-model="openInvalid" label="Open even if the configuration is invalid" help="Lets you repair it in the editor." />
          <div class="row">
            <AppButton type="submit" tone="primary" :icon="FolderOpen" :loading="workspace.pending.value" :disabled="!openPath.trim()">Open</AppButton>
          </div>
          <p v-if="workspace.error.value" class="failure">{{ workspace.error.value }}</p>
          <div v-if="recent.length" class="recent">
            <h4>Recent</h4>
            <ul>
              <li v-for="path in recent" :key="path">
                <button type="button" class="link mono" :disabled="path === live.snapshot?.workspace.config_path" @click="openWorkspace(path)">{{ path }}</button>
              </li>
            </ul>
          </div>
        </form>
      </section>

      <section class="panel">
        <header class="panel-header"><h3>Create a workspace</h3></header>
        <form class="panel-body stack" @submit.prevent="initialize">
          <FormField label="Project root" for-id="init-root" help="Creates .alfred/config.toml plus state, tmp, and worktrees folders there, then opens it.">
            <TextInput id="init-root" v-model="initRoot" mono placeholder="/path/to/project" />
          </FormField>
          <ToggleSwitch v-model="initForce" label="Replace an existing configuration (force)" help="Only the config file is replaced; state is kept." />
          <CommandLine :command="alfredCommand('init', ['--root', initRoot || '<root>'], initForce ? '--force' : false)" />
          <div class="row">
            <AppButton type="submit" tone="primary" :icon="FolderPlus" :loading="workspace.pending.value" :disabled="!initRoot.trim()">Initialize</AppButton>
          </div>
        </form>
      </section>
    </div>

    <div v-else-if="section === 'configuration'" class="config">
      <p v-if="!live.snapshot?.workspace.config_path" class="muted">Open or create a workspace first.</p>
      <p v-else-if="configFailure" class="failure">{{ configFailure }}</p>
      <template v-else-if="config">
        <section class="panel editor-panel">
          <header class="panel-header wrap">
            <h3>config.toml</h3>
            <span class="mono faint tiny">{{ config.path }}</span>
            <span class="spacer" />
            <TagChip :tone="validation?.valid ? 'ok' : 'danger'">{{ validation?.valid ? 'Valid' : 'Invalid' }}</TagChip>
            <TagChip v-if="dirty" tone="brass">Unsaved</TagChip>
            <AppButton size="sm" tone="quiet" :icon="Undo2" :disabled="!dirty" @click="revert">Revert</AppButton>
            <AppButton size="sm" tone="primary" :icon="Save" :loading="saving.pending.value" :disabled="!dirty || !validation?.valid" data-testid="save-config" @click="saveConfig">
              Save
            </AppButton>
          </header>
          <div class="panel-body stack">
            <p v-if="validation && !validation.valid" class="failure" role="alert">
              {{ validation.error }}
              <button v-if="errorLine" type="button" class="link" @click="editor?.goTo(errorLine)">Go to line {{ errorLine }}</button>
            </p>
            <p v-if="saving.error.value" class="failure">{{ saving.error.value }}</p>
            <CodeEditor ref="editor" v-model="text" label="Configuration TOML" :error-line="errorLine" />
            <p class="faint small">
              Checked with Alfred's own loader as you type. Saving keeps the previous file as config.toml.bak. Agents
              and the CLI read the new configuration on their next command.
            </p>
          </div>
        </section>

        <aside class="helpers">
          <section class="panel">
            <header class="panel-header"><h3>Add a repository</h3></header>
            <form class="panel-body stack" @submit.prevent="addRepository">
              <FormField label="Name" for-id="repo-name"><TextInput id="repo-name" v-model="repository.name" mono placeholder="app" /></FormField>
              <FormField label="Path" for-id="repo-path" help="Relative to the workspace root, or absolute."><TextInput id="repo-path" v-model="repository.path" mono placeholder="app" /></FormField>
              <div class="pair">
                <FormField label="Default branch" for-id="repo-branch"><TextInput id="repo-branch" v-model="repository.default_branch" mono /></FormField>
                <FormField label="Remote" for-id="repo-remote"><TextInput id="repo-remote" v-model="repository.remote" mono /></FormField>
              </div>
              <ToggleSwitch v-model="repository.selected_by_default" label="Selected when a task names none" />
              <p v-if="repositoryError" class="failure small">{{ repositoryError }}</p>
              <AppButton type="submit" size="sm">Insert into the configuration</AppButton>
            </form>
          </section>

          <section class="panel">
            <header class="panel-header"><h3>Add an agent</h3></header>
            <form class="panel-body stack" @submit.prevent="addAgent">
              <FormField label="Start from" for-id="agent-preset">
                <SelectInput
                  id="agent-preset"
                  v-model="agent.preset"
                  :options="Object.entries(config.presets).map(([key, preset]) => ({ value: key, label: preset.label }))"
                />
              </FormField>
              <div class="pair">
                <FormField label="Alias" for-id="agent-alias"><TextInput id="agent-alias" v-model="agent.alias" mono /></FormField>
                <FormField label="Runtime target" for-id="agent-target"><TextInput id="agent-target" v-model="agent.runtime_target" mono /></FormField>
              </div>
              <FormField label="Plan command" for-id="agent-plan"><TextInput id="agent-plan" v-model="agent.plan" mono /></FormField>
              <FormField label="Execution command" for-id="agent-execution"><TextInput id="agent-execution" v-model="agent.execution" mono /></FormField>
              <FormField label="Learner (direct) command" for-id="agent-direct"><TextInput id="agent-direct" v-model="agent.direct" mono /></FormField>
              <p class="faint small">
                Placeholders: {task_number} {task_title} {task_branch} {phase} {workdir} {model} {prompt} {prompt_file}.
                Passing {prompt} or {prompt_file} delivers the prompt reliably; otherwise it is pasted after the CLI starts.
                Add --model to pin a model, or --model {model} with models and default_model to choose one per task.
              </p>
              <p v-if="agentError" class="failure small">{{ agentError }}</p>
              <AppButton type="submit" size="sm">Insert into the configuration</AppButton>
            </form>
          </section>

          <section v-if="validation?.valid && validation.summary" class="panel">
            <header class="panel-header"><h3>As Alfred reads it</h3></header>
            <div class="panel-body stack small">
              <div>
                <h4>Repositories</h4>
                <ul class="plain">
                  <li v-for="item in validation.summary.repositories" :key="item.name">
                    <LampDot :color="item.is_git ? 'var(--lamp-running)' : 'var(--lamp-blocked)'" :size="7" />
                    <strong>{{ item.name }}</strong> <span class="mono">{{ item.path }}</span> on {{ item.default_branch }}
                    <template v-if="!item.is_git"> (not a Git repository)</template>
                  </li>
                  <li v-if="!validation.summary.repositories.length" class="muted">None.</li>
                </ul>
              </div>
              <div>
                <h4>Agents</h4>
                <ul class="plain">
                  <li v-for="item in validation.summary.agents" :key="item.alias">
                    <strong>{{ item.alias }}</strong> {{ item.cli }}<template v-if="item.model">, {{ item.model }}</template>
                    <TagChip :tone="item.prompt_delivery.execution === 'argument' ? 'ok' : 'brass'">{{ item.prompt_delivery.execution }}</TagChip>
                  </li>
                  <li v-if="!validation.summary.agents.length" class="muted">None.</li>
                </ul>
              </div>
              <p>Commit tags: {{ Object.values(validation.summary.commit_tags).join(' ') }}</p>
              <p>Tracker {{ validation.summary.tracker.enabled ? 'enabled' : 'disabled' }}; knowledge entries required: {{ validation.summary.knowledge.required_completion_entries }}</p>
            </div>
          </section>
        </aside>
      </template>
    </div>

    <div v-else-if="section === 'skills'" class="stack">
      <p v-if="!live.snapshot?.workspace.config_path" class="muted">Open or create a workspace first.</p>
      <p v-else-if="skillsFailure" class="failure">{{ skillsFailure }}</p>
      <template v-else-if="skills">
        <p class="muted small">
          Skills are standing instructions Alfred appends to every agent prompt of a phase. They are stored as Markdown in
          <span class="mono">{{ skills.directory }}</span>; removing one falls back to the built-in instructions.
        </p>
        <p v-if="skillSaving.error.value" class="failure">{{ skillSaving.error.value }}</p>
        <div class="grid">
          <section v-for="item in skills.skills" :key="item.phase" class="panel" :data-skill="item.phase">
            <header class="panel-header wrap">
              <h3>{{ SKILL_LABELS[item.phase].title }}</h3>
              <span class="spacer" />
              <TagChip :tone="item.exists ? 'ok' : 'default'">{{ item.exists ? 'Defined' : 'Built-in only' }}</TagChip>
              <TagChip v-if="skillDirty(item)" tone="brass">Unsaved</TagChip>
            </header>
            <form class="panel-body stack" @submit.prevent="saveSkill(item)">
              <FormField :label="SKILL_LABELS[item.phase].title" :for-id="`skill-${item.phase}`" :help="SKILL_LABELS[item.phase].help">
                <TextArea :id="`skill-${item.phase}`" v-model="skillText[item.phase]" :rows="12" mono placeholder="Markdown instructions for the agent" />
              </FormField>
              <p class="faint tiny mono">{{ item.path }}</p>
              <CommandLine :command="alfredCommand('skill', 'set', ['--phase', item.phase], ['--file', item.path])" />
              <div class="row">
                <AppButton
                  type="submit"
                  size="sm"
                  tone="primary"
                  :icon="Save"
                  :loading="skillSaving.pending.value"
                  :disabled="!skillDirty(item) || !skillText[item.phase].trim()"
                  :data-testid="`save-skill-${item.phase}`"
                >
                  Save
                </AppButton>
                <AppButton size="sm" tone="quiet" :icon="Undo2" :disabled="!skillDirty(item)" @click="skillText[item.phase] = item.content">Revert</AppButton>
                <AppButton size="sm" tone="danger" :icon="Trash2" :disabled="!item.exists" :loading="skillSaving.pending.value" @click="removeSkill(item)">
                  Remove
                </AppButton>
              </div>
            </form>
          </section>
        </div>
      </template>
    </div>

    <div v-else-if="section === 'health'" class="stack">
      <div class="row">
        <AppButton :icon="RefreshCw" :loading="checking" @click="runChecks">Run the checks again</AppButton>
      </div>
      <ul class="checks">
        <li v-for="check in checks" :key="check.key" class="check panel" :class="check.status" :data-check="check.key">
          <LampDot :color="lamp[check.status]" :size="10" :label="check.status" />
          <div class="check-body">
            <p class="check-title">{{ check.title }}</p>
            <p class="small muted">{{ check.detail }}</p>
          </div>
          <AppButton v-if="check.fix" size="sm" tone="primary" :loading="fixing.pending.value" @click="fix(check)">{{ check.fix.label }}</AppButton>
        </li>
      </ul>
    </div>

    <div v-else-if="section === 'server'" class="stack">
      <p v-if="serverFailure" class="failure">{{ serverFailure }}</p>
      <template v-else-if="server">
        <p v-if="server.frontend.stale || server.backend.stale" class="notice" role="status">
          <template v-if="server.backend.stale">The server code changed after this server started. </template>
          <template v-if="server.frontend.stale">The frontend sources are newer than the build being served. </template>
          Update and restart to run what is on disk.
        </p>
        <div class="grid">
          <section class="panel">
            <header class="panel-header"><h3>Running server</h3></header>
            <div class="panel-body stack">
              <KeyValue
                :items="[
                  { label: 'Checkout', value: server.checkout, mono: true },
                  {
                    label: 'Git',
                    value: server.git.available
                      ? `${server.git.branch || '(detached)'} at ${server.git.commit}${server.git.changes ? `, ${server.git.changes} uncommitted` : ''}`
                      : 'Not a Git checkout',
                    mono: true,
                  },
                  { label: 'Last commit', value: server.git.subject || '—' },
                  { label: 'Versions', value: `alfred ${server.versions.alfred}, alfred-ui ${server.versions.ui}` },
                  { label: 'Alfred package', value: server.packages.alfred, mono: true },
                  { label: 'UI package', value: server.packages.alfred_ui, mono: true },
                  { label: 'Started', value: absolute(server.started_at) },
                  { label: 'Python last changed', value: server.backend.changed_at ? absolute(server.backend.changed_at) : '—' },
                  { label: 'Frontend built', value: server.frontend.built_at ? absolute(server.frontend.built_at) : 'Not built' },
                  { label: 'Frontend sources changed', value: server.frontend.sources_changed_at ? absolute(server.frontend.sources_changed_at) : '—' },
                ]"
              />
              <p class="faint small">
                Check the checkout path: it is the copy of the project this server runs, whatever directory you started
                it from.
              </p>
            </div>
          </section>

          <section class="panel">
            <header class="panel-header wrap">
              <h3>Update</h3>
              <span class="spacer" />
              <TagChip v-if="server.frontend.stale" tone="brass">Frontend out of date</TagChip>
              <TagChip v-if="server.backend.stale" tone="brass">Restart needed</TagChip>
              <TagChip v-if="!server.frontend.stale && !server.backend.stale" tone="ok">Up to date</TagChip>
            </header>
            <div class="panel-body stack">
              <p class="muted small">
                Rebuilds the frontend from this checkout with the installed node_modules and restarts the server on the same
                port and token. Nothing is downloaded: pull or switch branches yourself first. Agent sessions and the
                coordinator run in tmux and keep running during the restart.
              </p>
              <CommandLine command="cd ui/web && npm run build" />
              <p v-if="!server.rebuild.available" class="faint small">Rebuild unavailable: {{ server.rebuild.reason }}</p>
              <p v-if="!server.restart.available" class="faint small">Restart unavailable: {{ server.restart.reason }}</p>
              <div class="row">
                <AppButton
                  tone="primary"
                  :icon="RotateCw"
                  :loading="serverBusy !== ''"
                  :disabled="!server.restart.available || serverBusy !== ''"
                  data-testid="update-and-restart"
                  @click="updateAndRestart"
                >
                  {{ serverBusy === 'rebuild' ? 'Rebuilding…' : serverBusy === 'restart' ? 'Restarting…' : 'Update and restart' }}
                </AppButton>
                <AppButton :icon="Hammer" :disabled="!server.rebuild.available || serverBusy !== ''" data-testid="rebuild" @click="rebuild">
                  Rebuild frontend only
                </AppButton>
                <AppButton tone="quiet" :icon="RefreshCw" :disabled="!server.restart.available || serverBusy !== ''" @click="restart">
                  Restart only
                </AppButton>
              </div>
              <p v-if="restartProblem" class="failure">{{ restartProblem }}</p>
              <template v-if="rebuildResult">
                <p :class="rebuildResult.ok ? 'small' : 'failure'">
                  {{ rebuildResult.message }} ({{ rebuildResult.seconds }}s)
                  <button v-if="rebuildResult.ok" type="button" class="link" @click="reloadPage">Reload this page</button>
                </p>
                <pre class="fragment output" data-testid="rebuild-output">{{ rebuildResult.output }}</pre>
              </template>
            </div>
          </section>
        </div>
      </template>
    </div>

    <div v-else-if="section === 'migrate'" class="grid">
      <section class="panel">
        <header class="panel-header"><h3>Import legacy runtime state</h3></header>
        <form class="panel-body stack" @submit.prevent="migrate">
          <p class="muted small">
            Copies tasks.json, runs.json, queue.json, and agent_map.json from a legacy runtime folder into this
            workspace. It only runs against empty state, writes a timestamped backup and a marker, and never deletes
            the source. Legacy agent commands come back as TOML to review.
          </p>
          <FormField label="Legacy runtime folder" for-id="migrate-source" required>
            <TextInput id="migrate-source" v-model="migration.source" mono placeholder="/path/to/data/runtime" />
          </FormField>
          <FormField label="Backup and marker folder" for-id="migrate-dir" :help="`Default: ${live.config ? live.config.runtime.state_directory.replace(/\/state$/, '/migrations') : '.alfred/migrations'}`">
            <TextInput id="migrate-dir" v-model="migration.migration_directory" mono />
          </FormField>
          <CommandLine :command="alfredCommand('migrate', ['--source', migration.source || '<folder>'], ['--migration-directory', migration.migration_directory])" />
          <AppButton type="submit" tone="primary" :loading="migrating.pending.value" :disabled="!migration.source.trim()">Migrate</AppButton>
          <p v-if="migrating.error.value" class="failure">{{ migrating.error.value }}</p>
        </form>
      </section>
      <section v-if="migrationResult" class="panel">
        <header class="panel-header"><h3>Result</h3></header>
        <div class="panel-body stack">
          <p><strong>{{ migrationResult.message }}</strong></p>
          <KeyValue
            :items="[
              { label: 'Backup', value: String(migrationResult.backup_directory), mono: true },
              { label: 'Marker', value: String(migrationResult.marker_path), mono: true },
              { label: 'Agent fragment', value: String(migrationResult.agent_fragment || 'None'), mono: true },
            ]"
          />
          <template v-if="migrationResult.agent_fragment_text">
            <p class="small">Review these agents, then add the ones you want in the configuration editor:</p>
            <pre class="fragment">{{ migrationResult.agent_fragment_text }}</pre>
            <AppButton size="sm" @click="section = 'configuration'">Open the configuration editor</AppButton>
          </template>
        </div>
      </section>
    </div>

    <div v-else class="grid">
      <section class="panel">
        <header class="panel-header"><h3>Preferences</h3></header>
        <div class="panel-body stack">
          <FormField label="Record my actions as" for-id="pref-actor" help="Written to the event log for everything you do here. Alfred's CLI uses manager by default.">
            <TextInput id="pref-actor" v-model="prefs.values.actor" mono />
          </FormField>
          <FormField label="Theme">
            <SegmentedControl
              v-model="prefs.values.theme"
              :options="[
                { value: 'system', label: 'Match the system' },
                { value: 'light', label: 'Light' },
                { value: 'dark', label: 'Dark' },
              ]"
              label="Theme"
            />
          </FormField>
          <div class="row">
            <ToggleSwitch :model-value="prefs.values.browserNotifications" label="Desktop alerts" :help="alerts.help()" @update:model-value="alerts.toggle" />
            <AppButton v-if="prefs.values.browserNotifications && alerts.permission.value === 'granted'" size="sm" tone="quiet" @click="alerts.test">
              Send a test
            </AppButton>
          </div>
          <ToggleSwitch v-model="prefs.values.includeConfigInCommands" label="Include --config in shown commands" help="So copied commands work from any directory." />
          <p class="faint small mono">{{ prefs.values.includeConfigInCommands && live.snapshot?.workspace.config_path ? `alfred --config ${shellQuote(live.snapshot.workspace.config_path)} …` : 'alfred …' }}</p>
          <FormField label="Terminal text size" for-id="pref-font">
            <TextInput id="pref-font" v-model.number="prefs.values.terminalFontSize" inputmode="numeric" />
          </FormField>
          <div class="row">
            <AppButton tone="quiet" @click="prefs.reset()">Reset preferences</AppButton>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tabs {
  margin-bottom: var(--space-4);
  padding: 0;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(400px, 100%), 1fr));
  gap: var(--space-4);
  align-items: start;
}

.config {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: var(--space-4);
  align-items: start;
}

.helpers {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.wrap {
  flex-wrap: wrap;
}

.pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

.recent h4 {
  margin-bottom: 4px;
}

.recent ul,
.plain {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.plain li {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.link {
  border: 0;
  background: none;
  padding: 0;
  color: var(--accent);
  cursor: pointer;
  text-align: left;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.link:disabled {
  color: var(--ink-3);
  cursor: default;
}

.checks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.check {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
}

.check.error {
  border-color: color-mix(in srgb, var(--danger) 45%, var(--line));
}

.check.warn {
  border-color: var(--brass-line);
}

.check-body {
  flex: 1;
  min-width: 0;
}

.check-body p {
  margin: 0;
  overflow-wrap: anywhere;
}

.check-title {
  font-weight: 650;
}

.fragment {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--screen);
  color: var(--screen-ink);
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
}

.failure {
  color: var(--danger);
  margin: 0;
}

.notice {
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--radius);
  background: var(--brass-soft);
  color: var(--brass);
  font-size: var(--text-sm);
}

.output {
  max-height: 320px;
  overflow: auto;
}

@media (max-width: 1100px) {
  .config {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
