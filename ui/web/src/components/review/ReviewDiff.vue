<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { ChevronDown, ChevronRight, MessageSquarePlus } from 'lucide-vue-next'
import type { ReviewComment } from '@/api/types'
import { splitRows, type DiffFile, type DiffLine } from '@/lib/diff'
import ReviewThread from './ReviewThread.vue'

/** One file of a branch diff, with line comments in unified or side-by-side layout. */
const props = defineProps<{
  file: DiffFile
  repository: string
  comments: ReviewComment[]
  mode: 'unified' | 'split'
  pending?: boolean
}>()
const emit = defineEmits<{
  comment: [{ line: number | null; side: 'old' | 'new'; body: string }]
  update: [{ id: string; body?: string; resolved?: boolean }]
  remove: [string]
}>()

const collapsed = ref(false)
const composing = ref<string | null>(null)

interface Target {
  side: 'old' | 'new'
  line: number
}

/** Comments anchor to the new file's line numbers, except on removed lines. */
function target(line: DiffLine | null): Target | null {
  if (!line) return null
  if (line.kind === 'remove' && line.oldNumber !== null) return { side: 'old', line: line.oldNumber }
  if ((line.kind === 'add' || line.kind === 'context') && line.newNumber !== null) return { side: 'new', line: line.newNumber }
  return null
}

const keyOf = (value: Target | null) => (value ? `${value.side}:${value.line}` : '')

const threads = computed(() => {
  const groups = new Map<string, ReviewComment[]>()
  for (const comment of props.comments) {
    const key = comment.line === null ? 'file' : `${comment.side}:${comment.line}`
    groups.set(key, [...(groups.get(key) ?? []), comment])
  }
  return groups
})

const fileComments = computed(() => threads.value.get('file') ?? [])
const rows = computed(() => (props.mode === 'split' ? splitRows(props.file.lines) : []))
const openCount = computed(() => props.comments.filter((item) => !item.resolved).length)

async function startComment(value: Target | 'file' | null) {
  if (!value) return
  composing.value = value === 'file' ? 'file' : keyOf(value)
  collapsed.value = false
  await nextTick()
}

function submit(key: string, body: string) {
  if (key === 'file') emit('comment', { line: null, side: 'new', body })
  else {
    const [side, line] = key.split(':')
    emit('comment', { line: Number(line), side: side as 'old' | 'new', body })
  }
  composing.value = null
}

function shown(key: string) {
  return key && (threads.value.has(key) || composing.value === key)
}
</script>

<template>
  <section class="file" :data-review-file="`${repository}/${file.path}`">
    <header class="file-head">
      <button type="button" class="toggle" :aria-expanded="!collapsed" @click="collapsed = !collapsed">
        <ChevronRight v-if="collapsed" :size="15" aria-hidden="true" />
        <ChevronDown v-else :size="15" aria-hidden="true" />
        <span class="path mono">{{ file.path }}</span>
      </button>
      <span v-if="openCount" class="open-count" :title="`${openCount} open comment${openCount === 1 ? '' : 's'}`">{{ openCount }}</span>
      <span class="counts mono"><span class="plus">+{{ file.additions }}</span> <span class="minus">−{{ file.deletions }}</span></span>
      <button type="button" class="file-comment" title="Comment on this file" @click="startComment('file')">
        <MessageSquarePlus :size="15" aria-hidden="true" />
      </button>
    </header>

    <template v-if="!collapsed">
      <ReviewThread
        v-if="fileComments.length || composing === 'file'"
        class="file-thread"
        :comments="fileComments"
        :composing="composing === 'file'"
        :pending="pending"
        @submit="submit('file', $event)"
        @cancel="composing = null"
        @reply="startComment('file')"
        @update="emit('update', $event)"
        @remove="emit('remove', $event)"
      />

      <p v-if="file.binary" class="binary">Binary file; nothing to comment on line by line.</p>

      <div v-else-if="mode === 'unified'" class="lines unified" role="table" :aria-label="`Changes in ${file.path}`">
        <template v-for="(line, index) in file.lines" :key="index">
          <div class="line" :class="line.kind" role="row">
            <span class="number" role="cell">{{ line.oldNumber ?? '' }}</span>
            <span class="number" role="cell">{{ line.newNumber ?? '' }}</span>
            <span class="gutter" role="cell">
              <button
                v-if="target(line)"
                type="button"
                class="add-comment"
                :aria-label="`Comment on line ${target(line)?.line}`"
                @click="startComment(target(line))"
              >
                +
              </button>
            </span>
            <span class="code" role="cell">{{ line.text }}</span>
          </div>
          <ReviewThread
            v-if="shown(keyOf(target(line)))"
            :comments="threads.get(keyOf(target(line))) ?? []"
            :composing="composing === keyOf(target(line))"
            :pending="pending"
            @submit="submit(keyOf(target(line)), $event)"
            @cancel="composing = null"
            @reply="startComment(target(line))"
            @update="emit('update', $event)"
            @remove="emit('remove', $event)"
          />
        </template>
      </div>

      <div v-else class="lines split" role="table" :aria-label="`Side-by-side changes in ${file.path}`">
        <template v-for="(row, index) in rows" :key="index">
          <div v-if="row.kind !== 'pair'" class="line wide" :class="row.kind" role="row">
            <span class="code" role="cell">{{ row.left?.text }}</span>
          </div>
          <div v-else class="pair" role="row">
            <div class="half" :class="row.left?.kind === 'remove' ? 'remove' : row.left ? 'context' : 'blank'">
              <span class="number" role="cell">{{ row.left?.oldNumber ?? '' }}</span>
              <span class="gutter" role="cell">
                <button
                  v-if="row.left?.kind === 'remove'"
                  type="button"
                  class="add-comment"
                  :aria-label="`Comment on removed line ${row.left.oldNumber}`"
                  @click="startComment(target(row.left))"
                >
                  +
                </button>
              </span>
              <span class="code" role="cell">{{ row.left?.text }}</span>
            </div>
            <div class="half" :class="row.right?.kind === 'add' ? 'add' : row.right ? 'context' : 'blank'">
              <span class="number" role="cell">{{ row.right?.newNumber ?? '' }}</span>
              <span class="gutter" role="cell">
                <button
                  v-if="row.right"
                  type="button"
                  class="add-comment"
                  :aria-label="`Comment on line ${row.right.newNumber}`"
                  @click="startComment(target(row.right))"
                >
                  +
                </button>
              </span>
              <span class="code" role="cell">{{ row.right?.text }}</span>
            </div>
          </div>
          <template v-for="side in [row.left?.kind === 'remove' ? target(row.left) : null, target(row.right)]" :key="keyOf(side)">
            <ReviewThread
              v-if="side && shown(keyOf(side))"
              :class="side.side === 'old' ? 'left-thread' : 'right-thread'"
              :comments="threads.get(keyOf(side)) ?? []"
              :composing="composing === keyOf(side)"
              :pending="pending"
              @submit="submit(keyOf(side), $event)"
              @cancel="composing = null"
              @reply="startComment(side)"
              @update="emit('update', $event)"
              @remove="emit('remove', $event)"
            />
          </template>
        </template>
      </div>
    </template>
  </section>
</template>

<style scoped>
.file {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  overflow: hidden;
  scroll-margin-top: calc(var(--topbar-height) + 12px);
}

.file-head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  background: var(--panel-2);
  border-bottom: 1px solid var(--line);
}

.toggle {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: none;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.path {
  font-size: 12.5px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.open-count {
  min-width: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--brass-soft);
  color: var(--brass-strong);
  font-size: 11px;
  font-weight: 700;
  text-align: center;
}

.counts {
  font-size: 12px;
}

.plus {
  color: var(--ok);
}

.minus {
  color: var(--danger);
}

.file-comment {
  display: inline-grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 1px solid var(--line);
  border-radius: var(--radius-s);
  background: var(--panel);
  color: var(--ink-2);
  cursor: pointer;
}

.file-comment:hover {
  color: var(--ink);
  border-color: var(--line-strong);
}

.binary {
  margin: 0;
  padding: 10px;
  color: var(--ink-2);
  font-size: var(--text-sm);
}

.lines {
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.unified .line {
  display: grid;
  grid-template-columns: 44px 44px 22px 1fr;
  min-width: max-content;
}

.number {
  color: var(--ink-3);
  text-align: right;
  padding-right: 8px;
  user-select: none;
}

.gutter {
  display: flex;
  justify-content: center;
}

.add-comment {
  width: 18px;
  height: 18px;
  margin-top: 1px;
  border: 0;
  border-radius: 4px;
  background: var(--accent);
  color: var(--accent-ink);
  font-weight: 800;
  line-height: 18px;
  cursor: pointer;
  opacity: 0;
}

.line:hover .add-comment,
.half:hover .add-comment,
.add-comment:focus-visible {
  opacity: 1;
}

.code {
  white-space: pre;
  padding-right: 16px;
}

.line.add,
.half.add {
  background: color-mix(in srgb, var(--ok-soft) 80%, transparent);
}

.line.remove,
.half.remove {
  background: color-mix(in srgb, var(--danger-soft) 80%, transparent);
}

.half.blank {
  background: var(--panel-2);
}

.line.hunk,
.line.meta {
  color: var(--ink-2);
  background: var(--panel-2);
}

.line.hunk .code,
.line.meta .code {
  font-style: italic;
}

.split .pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
}

.split .wide {
  padding-left: 66px;
}

.half {
  display: grid;
  grid-template-columns: 44px 22px 1fr;
  min-width: 0;
  overflow: hidden;
}

.half + .half {
  border-left: 1px solid var(--line);
}

.half .code {
  overflow: hidden;
  text-overflow: ellipsis;
}

.left-thread {
  margin-right: 50%;
}

.right-thread {
  margin-left: 50%;
}

.file-thread {
  border-bottom: 1px solid var(--line);
}
</style>
