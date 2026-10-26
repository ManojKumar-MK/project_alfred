<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ReviewComment } from '@/api/types'
import AppButton from '@/components/base/AppButton.vue'
import RelativeTime from '@/components/base/RelativeTime.vue'
import TagChip from '@/components/base/TagChip.vue'
import TextArea from '@/components/base/TextArea.vue'

/** The comments on one line or file, with a composer for a new one. */
const props = defineProps<{ comments: ReviewComment[]; composing: boolean; pending?: boolean }>()
const emit = defineEmits<{
  submit: [string]
  cancel: []
  reply: []
  update: [{ id: string; body?: string; resolved?: boolean }]
  remove: [string]
}>()

const draft = ref('')
const editing = ref<string | null>(null)
const editText = ref('')
const root = ref<HTMLElement>()

watch(
  () => props.composing,
  async (value) => {
    if (!value) return
    await nextTick()
    root.value?.querySelector<HTMLTextAreaElement>('textarea.composer, .composer textarea')?.focus()
  },
  { immediate: true },
)

function submit() {
  if (!draft.value.trim()) return
  emit('submit', draft.value)
  draft.value = ''
}

function startEdit(comment: ReviewComment) {
  editing.value = comment.id
  editText.value = comment.body
}

function saveEdit(id: string) {
  if (!editText.value.trim()) return
  emit('update', { id, body: editText.value })
  editing.value = null
}

function onKey(event: KeyboardEvent, action: () => void) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault()
    action()
  } else if (event.key === 'Escape') {
    editing.value = null
    emit('cancel')
  }
}
</script>

<template>
  <div ref="root" class="thread">
    <article v-for="comment in comments" :key="comment.id" class="comment" :class="{ resolved: comment.resolved }" :data-comment="comment.id">
      <header class="meta">
        <strong>{{ comment.author }}</strong>
        <RelativeTime class="faint" :value="comment.created_at" />
        <span v-if="comment.updated_at" class="faint">(edited)</span>
        <TagChip v-if="comment.resolved" tone="ok">Resolved</TagChip>
        <TagChip v-else-if="comment.sent_at" tone="accent" :title="`Sent to the agent ${comment.sent_at}`">Sent to agent</TagChip>
        <span class="spacer" />
        <button type="button" class="link" @click="emit('update', { id: comment.id, resolved: !comment.resolved })">
          {{ comment.resolved ? 'Reopen' : 'Resolve' }}
        </button>
        <button type="button" class="link" @click="startEdit(comment)">Edit</button>
        <button type="button" class="link danger" @click="emit('remove', comment.id)">Delete</button>
      </header>
      <div v-if="editing === comment.id" class="composer" @keydown="onKey($event, () => saveEdit(comment.id))">
        <TextArea v-model="editText" :rows="3" />
        <div class="row">
          <AppButton size="sm" tone="primary" :loading="pending" :disabled="!editText.trim()" @click="saveEdit(comment.id)">Save</AppButton>
          <AppButton size="sm" tone="quiet" @click="editing = null">Cancel</AppButton>
        </div>
      </div>
      <p v-else class="body">{{ comment.body }}</p>
    </article>

    <div v-if="composing" class="composer" @keydown="onKey($event, submit)">
      <TextArea v-model="draft" :rows="3" placeholder="What should change here? ⌘↩ to comment" />
      <div class="row">
        <AppButton size="sm" tone="primary" :loading="pending" :disabled="!draft.trim()" @click="submit">Comment</AppButton>
        <AppButton size="sm" tone="quiet" @click="emit('cancel')">Cancel</AppButton>
      </div>
    </div>
    <button v-else-if="comments.length" type="button" class="reply" @click="emit('reply')">Reply…</button>
  </div>
</template>

<style scoped>
.thread {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px 10px 66px;
  background: var(--paper);
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  line-height: 1.45;
  white-space: normal;
}

.comment {
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  max-width: 820px;
}

.comment.resolved {
  opacity: 0.7;
}

.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: var(--text-xs);
}

.meta strong {
  font-size: var(--text-sm);
}

.body {
  margin: 6px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.link {
  border: 0;
  background: none;
  padding: 0;
  color: var(--accent);
  font-size: var(--text-xs);
  cursor: pointer;
}

.link.danger {
  color: var(--danger);
}

.composer {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 820px;
}

.row {
  display: flex;
  gap: 6px;
}

.reply {
  align-self: flex-start;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--ink-2);
  padding: 4px 10px;
  font-size: var(--text-sm);
  cursor: pointer;
}

.reply:hover {
  color: var(--ink);
}
</style>
