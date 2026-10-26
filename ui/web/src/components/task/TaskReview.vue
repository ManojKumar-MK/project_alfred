<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, ExternalLink, GitBranch, GitMerge, GitPullRequestArrow, MessageSquareWarning, Upload } from 'lucide-vue-next'
import { api } from '@/api/http'
import type { PipelineRun, ReviewDocument, TaskDetail, WorktreeDetail } from '@/api/types'
import { parseDiff, type DiffFile } from '@/lib/diff'
import { completedActions, errorMessage, useRequest } from '@/composables/useRequest'
import { useDialogs } from '@/stores/dialogs'
import { usePrefs } from '@/stores/prefs'
import AppButton from '@/components/base/AppButton.vue'
import EmptyState from '@/components/base/EmptyState.vue'
import RelativeTime from '@/components/base/RelativeTime.vue'
import SegmentedControl from '@/components/base/SegmentedControl.vue'
import TagChip from '@/components/base/TagChip.vue'
import TextArea from '@/components/base/TextArea.vue'
import ReviewDiff from '@/components/review/ReviewDiff.vue'

/** Pull-request style review of the task's branch against each repository's default branch. */
const props = defineProps<{ detail: TaskDetail }>()
const emit = defineEmits<{ tab: [string] }>()
const dialogs = useDialogs()
const prefs = usePrefs()
const { pending, run } = useRequest()

const worktrees = ref<WorktreeDetail[]>([])
const review = ref<ReviewDocument | null>(null)
const pipeline = ref<PipelineRun | null>(null)
const failure = ref('')
const loading = ref(true)
const mode = ref<'unified' | 'split'>('unified')
const summary = ref('')

const task = computed(() => props.detail.task)
const number = computed(() => task.value.task_number)
const actions = computed(() => task.value.derived.actions)

interface ReviewFile extends DiffFile {
  repository: string
  key: string
}

const files = computed<ReviewFile[]>(() =>
  worktrees.value.flatMap((worktree) =>
    parseDiff(worktree.committed_diff?.text ?? '').map((file) => ({
      ...file,
      repository: worktree.repository,
      key: `${worktree.repository}/${file.path}`,
    })),
  ),
)

const commentsFor = (file: ReviewFile) =>
  (review.value?.comments ?? []).filter((item) => item.repository === file.repository && item.path === file.path)

const openComments = computed(() => (review.value?.comments ?? []).filter((item) => !item.resolved))
const outstanding = computed(() => openComments.value.filter((item) => !item.sent_at))
const totals = computed(() => ({
  additions: files.value.reduce((sum, file) => sum + file.additions, 0),
  deletions: files.value.reduce((sum, file) => sum + file.deletions, 0),
}))
const dirty = computed(() => worktrees.value.filter((item) => item.dirty))
const truncated = computed(() => worktrees.value.some((item) => item.committed_diff?.truncated))
const latestLink = computed(() => review.value?.links[0]?.url ?? '')

async function load() {
  try {
    const [trees, document, pipelines] = await Promise.all([
      api.get<{ worktrees: WorktreeDetail[] }>(`/tasks/${number.value}/worktrees?diff=true`),
      api.get<ReviewDocument>(`/tasks/${number.value}/review`),
      api.get<{ runs: PipelineRun[] }>(`/tasks/${number.value}/pipelines`),
    ])
    worktrees.value = trees.worktrees
    review.value = document
    pipeline.value = pipelines.runs[0] ?? null
    failure.value = ''
  } catch (error) {
    failure.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

watch([() => task.value.updated_at, completedActions], load, { immediate: true })

let timer: number | undefined
onMounted(() => {
  timer = window.setInterval(() => {
    if (document.visibilityState === 'visible') void load()
  }, 5000)
})
onBeforeUnmount(() => window.clearInterval(timer))

const path = (suffix: string) => `/tasks/${number.value}/review${suffix}`

async function addComment(file: ReviewFile, event: { line: number | null; side: 'old' | 'new'; body: string }) {
  await run(
    () => api.post(path('/comments'), { repository: file.repository, path: file.path, ...event, actor: prefs.values.actor }),
    { quiet: true, toastErrors: true, task: number.value },
  )
}

async function updateComment(event: { id: string; body?: string; resolved?: boolean }) {
  const { id, ...body } = event
  await run(() => api.post(path(`/comments/${id}`), body), { quiet: true, toastErrors: true, task: number.value })
}

async function removeComment(id: string) {
  const confirmed = await dialogs.confirm({ title: 'Delete comment', message: 'Delete this review comment?', confirm: 'Delete', tone: 'danger' })
  if (confirmed) await run(() => api.post(path(`/comments/${id}/delete`), {}), { quiet: true, toastErrors: true, task: number.value })
}

async function approve() {
  if (openComments.value.length) {
    const confirmed = await dialogs.confirm({
      title: 'Approve with open comments?',
      message: `${openComments.value.length} comment${openComments.value.length === 1 ? ' is' : 's are'} not resolved yet.`,
      confirm: 'Approve anyway',
    })
    if (!confirmed) return
  }
  const result = await run(() => api.post(path('/approve'), { note: summary.value, actor: prefs.values.actor }), {
    command: `alfred task review --task ${number.value} --decision approved --note …`,
    task: number.value,
    toastErrors: true,
  })
  if (result) summary.value = ''
}

async function requestChanges() {
  const result = await run(() => api.post(path('/request-changes'), { note: summary.value, actor: prefs.values.actor }), {
    command: `alfred task review --task ${number.value} --decision changes_requested --note …`,
    task: number.value,
    toastErrors: true,
  })
  if (result) summary.value = ''
}

function jump(file: ReviewFile) {
  document.querySelector(`[data-review-file="${CSS.escape(file.key)}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const pipelineTone = computed(() =>
  pipeline.value?.status === 'passed' ? 'ok' : pipeline.value?.status === 'running' ? 'accent' : pipeline.value ? 'danger' : 'default',
)
</script>

<template>
  <div class="review">
    <header class="panel summary">
      <div class="summary-top">
        <GitPullRequestArrow :size="20" class="pr-icon" aria-hidden="true" />
        <div class="branches">
          <p v-for="worktree in worktrees" :key="worktree.repository" class="branch">
            <strong>{{ worktree.repository }}</strong>
            <TagChip mono><GitBranch :size="11" aria-hidden="true" />{{ worktree.branch || 'detached' }}</TagChip>
            <span class="arrow" aria-label="into">→</span>
            <TagChip mono>{{ worktree.base || 'default branch' }}</TagChip>
            <span class="faint small">{{ worktree.ahead ?? '?' }} commit{{ worktree.ahead === 1 ? '' : 's' }}</span>
          </p>
          <p v-if="!worktrees.length && !loading" class="muted small">No worktrees, so nothing to review yet.</p>
        </div>
        <span class="spacer" />
        <div class="badges">
          <TagChip v-if="review?.verdict?.decision === 'approved' && task.derived.approved_since_last_work" tone="ok">
            <Check :size="12" aria-hidden="true" /> Approved by {{ review.verdict.actor }}
          </TagChip>
          <TagChip v-else-if="review?.verdict?.decision === 'changes_requested'" tone="brass">Changes requested</TagChip>
          <button v-if="pipeline" type="button" class="chip-button" @click="emit('tab', 'pipelines')">
            <TagChip :tone="pipelineTone">Pipeline {{ pipeline.status }}</TagChip>
          </button>
          <TagChip v-if="openComments.length" tone="brass">{{ openComments.length }} open comment{{ openComments.length === 1 ? '' : 's' }}</TagChip>
        </div>
      </div>

      <ul v-if="review?.links.length" class="links">
        <li v-for="link in review.links" :key="link.url">
          <a :href="link.url" target="_blank" rel="noopener noreferrer">
            <ExternalLink :size="13" aria-hidden="true" /> {{ link.url.includes('/merge_requests/') ? 'Merge request' : 'Pull request' }} · {{ link.repository }}
          </a>
          <RelativeTime class="faint small" :value="link.at" />
        </li>
      </ul>

      <div class="decide">
        <TextArea v-model="summary" :rows="2" placeholder="Overall feedback (optional). It goes to the agent with your comments." />
        <div class="buttons">
          <AppButton
            tone="primary"
            :icon="Check"
            :loading="pending"
            :disabled="!actions.review.approve"
            :title="actions.review.approve ? 'alfred task review --decision approved' : actions.review.reason"
            @click="approve"
          >
            Approve
          </AppButton>
          <AppButton
            tone="brass"
            :icon="MessageSquareWarning"
            :loading="pending"
            :disabled="!actions.review.request_changes || (!outstanding.length && !summary.trim())"
            :title="
              !actions.review.request_changes
                ? actions.review.reason
                : outstanding.length || summary.trim()
                  ? 'Sends the open comments to the agent'
                  : 'Add a comment or overall feedback first'
            "
            @click="requestChanges"
          >
            Request changes{{ outstanding.length ? ` (${outstanding.length})` : '' }}
          </AppButton>
          <span class="spacer" />
          <AppButton
            :icon="Upload"
            :disabled="!actions.push.enabled"
            :title="actions.push.reason"
            @click="dialogs.openAction('push', number, { merge_request: true })"
          >
            {{ latestLink ? 'Push update' : 'Push and open MR' }}
          </AppButton>
          <AppButton
            :icon="GitMerge"
            :disabled="!actions.merge.enabled"
            :title="actions.merge.reason || 'alfred task merge'"
            @click="dialogs.openAction('merge', number, { mr: latestLink })"
          >
            Record merge
          </AppButton>
        </div>
      </div>
    </header>

    <p v-if="failure" class="failure">{{ failure }}</p>
    <p v-else-if="loading" class="muted">Reading the branch…</p>
    <EmptyState v-else-if="!files.length" :icon="GitPullRequestArrow" title="No committed changes to review">
      The review shows commits on the task branch against the default branch. Commit the agent's work first.
    </EmptyState>

    <template v-else>
      <p v-if="dirty.length" class="notice">
        {{ dirty.map((item) => item.repository).join(', ') }} {{ dirty.length === 1 ? 'has' : 'have' }} uncommitted changes, which are not part of this review until committed.
      </p>

      <div class="body">
        <nav class="tree panel" aria-label="Changed files">
          <header class="tree-head">
            <strong>{{ files.length }} file{{ files.length === 1 ? '' : 's' }}</strong>
            <span class="mono small"><span class="plus">+{{ totals.additions }}</span> <span class="minus">−{{ totals.deletions }}</span></span>
          </header>
          <ul>
            <li v-for="file in files" :key="file.key">
              <button type="button" :title="file.key" @click="jump(file)">
                <span class="name mono">{{ file.path.split('/').pop() }}</span>
                <span class="dir mono">{{ worktrees.length > 1 ? `${file.repository}/` : '' }}{{ file.path.split('/').slice(0, -1).join('/') }}</span>
                <span v-if="commentsFor(file).filter((item) => !item.resolved).length" class="dot" aria-label="has open comments" />
              </button>
            </li>
          </ul>
        </nav>

        <div class="diffs">
          <div class="diff-toolbar">
            <SegmentedControl
              v-model="mode"
              :options="[
                { value: 'unified', label: 'Unified' },
                { value: 'split', label: 'Side by side' },
              ]"
              label="Diff layout"
            />
            <span class="faint small">Hover a line and press + to comment.</span>
          </div>
          <ReviewDiff
            v-for="file in files"
            :key="file.key"
            :file="file"
            :repository="file.repository"
            :comments="commentsFor(file)"
            :mode="mode"
            :pending="pending"
            @comment="addComment(file, $event)"
            @update="updateComment"
            @remove="removeComment"
          />
          <p v-if="truncated" class="muted small">The diff is longer than the UI shows; open the worktree to see the rest.</p>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.review {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5) var(--space-6);
}

.summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.summary-top {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.pr-icon {
  margin-top: 2px;
  color: var(--accent);
}

.branches {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.branch {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 0;
}

.arrow {
  color: var(--ink-3);
}

.badges {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.chip-button {
  border: 0;
  padding: 0;
  background: none;
  cursor: pointer;
}

.links {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
}

.links li {
  display: flex;
  align-items: center;
  gap: 8px;
}

.links a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
  font-size: var(--text-sm);
}

.decide {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.buttons {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.failure {
  color: var(--danger);
}

.notice {
  margin: 0;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--brass-soft);
  color: var(--ink);
  font-size: var(--text-sm);
}

.body {
  display: grid;
  grid-template-columns: minmax(200px, 260px) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.tree {
  position: sticky;
  top: calc(var(--topbar-height) + 12px);
  max-height: calc(100vh - var(--topbar-height) - 24px);
  overflow-y: auto;
  padding: 0;
}

.tree-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  font-size: var(--text-sm);
}

.tree ul {
  list-style: none;
  margin: 0;
  padding: 4px 0;
}

.tree button {
  position: relative;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 5px 24px 5px 12px;
  border: 0;
  background: none;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.tree button:hover {
  background: var(--panel-2);
}

.name {
  font-size: 12px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.dir {
  font-size: 11px;
  color: var(--ink-3);
  overflow-wrap: anywhere;
}

.dot {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--brass-strong);
}

.plus {
  color: var(--ok);
}

.minus {
  color: var(--danger);
}

.diffs {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.diff-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

@media (max-width: 900px) {
  .body {
    grid-template-columns: 1fr;
  }

  .tree {
    position: static;
    max-height: 240px;
  }
}
</style>
