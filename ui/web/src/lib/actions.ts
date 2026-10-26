/**
 * Every task, run, worktree, and session action the interface offers, described once: its form
 * fields, the request it sends, and the exact `alfred` command it corresponds to.
 */

import type { ActionName, ConfigDescription, Task } from '@/api/types'
import { alfredCommand, csv, shellQuote } from '@/lib/cli'
import { EVENT_TYPES, KNOWLEDGE_CATEGORIES } from '@/lib/status'

export type FieldKind = 'text' | 'textarea' | 'select' | 'segmented' | 'toggle' | 'number' | 'checklist'

export interface FieldOption {
  value: string
  label: string
  hint?: string
  disabled?: boolean
}

export interface ActionContext {
  task: Task | null
  config: ConfigDescription | undefined
  actor: string
  session?: string
}

export type Values = Record<string, unknown>

export interface FieldSpec {
  key: string
  label: string
  kind: FieldKind
  required?: boolean
  placeholder?: string
  help?: string
  rows?: number
  mono?: boolean
  options?: (context: ActionContext, values: Values) => FieldOption[]
  initial?: (context: ActionContext) => unknown
  visible?: (values: Values, context: ActionContext) => boolean
}

export interface ActionRequest {
  method: 'POST' | 'PATCH'
  path: string
  body: Record<string, unknown>
}

export interface ActionSpec {
  id: string
  /** The task action whose availability gates this dialog, when there is one. */
  gate?: ActionName
  title: (context: ActionContext) => string
  submit: string | ((values: Values, context: ActionContext) => string)
  description?: (context: ActionContext) => string
  tone?: 'primary' | 'danger' | 'brass'
  fields: FieldSpec[]
  request: (values: Values, context: ActionContext) => ActionRequest
  command: (values: Values, context: ActionContext) => string | null
  warning?: (values: Values, context: ActionContext) => string | null
}

const text = (values: Values, key: string): string => String(values[key] ?? '').trim()
const flag = (values: Values, key: string): boolean => Boolean(values[key])
const number = (context: ActionContext): number => context.task?.task_number ?? 0
const taskPath = (context: ActionContext, suffix: string) => `/tasks/${number(context)}${suffix}`
const actorFlag = (values: Values) => ['--actor', text(values, 'actor') === 'manager' ? '' : text(values, 'actor')] as const

const actorField: FieldSpec = {
  key: 'actor',
  label: 'Recorded as',
  kind: 'text',
  mono: true,
  help: 'The actor written to the task event log.',
  initial: (context) => context.actor,
}

const agentOptions = (context: ActionContext): FieldOption[] =>
  (context.config?.agents ?? []).map((agent) => ({
    value: agent.alias,
    label: agent.alias,
    hint: [agent.cli, agent.model ? `model ${agent.model}` : '', agent.runtime_target].filter(Boolean).join(', '),
  }))

/** The models the chosen agent lists, or none when it accepts any model. */
const agentModels = (values: Values, context: ActionContext): string[] =>
  context.config?.agents.find((agent) => agent.alias === text(values, 'agent'))?.models ?? []

/**
 * The model for assign and reassign: a list when the agent configures models, free text otherwise.
 * Leaving it empty keeps the task's current model.
 */
const modelFields: FieldSpec[] = [
  {
    key: 'model',
    label: 'Model',
    kind: 'select',
    options: (context, values) => [
      { value: '', label: `Keep current (${context.task?.model || 'agent default'})` },
      ...agentModels(values, context).map((model) => ({ value: model, label: model })),
    ],
    initial: () => '',
    visible: (values, context) => agentModels(values, context).length > 0,
    help: 'Fills the {model} placeholder in the agent’s commands.',
  },
  {
    key: 'model_text',
    label: 'Model',
    kind: 'text',
    mono: true,
    placeholder: 'Keep the current model',
    initial: () => '',
    visible: (values, context) => agentModels(values, context).length === 0,
    help: 'Fills the {model} placeholder in the agent’s commands; empty keeps the current model.',
  },
]

function chosenModel(values: Values, context: ActionContext): string {
  return agentModels(values, context).length ? text(values, 'model') : text(values, 'model_text')
}

const repositoryOptions = (context: ActionContext): FieldOption[] =>
  (context.config?.repositories ?? []).map((repository) => ({
    value: repository.name,
    label: repository.name,
    hint: `${repository.default_branch}${repository.selected_by_default ? ', selected by default' : ''}`,
  }))

const worktreeRepositoryOptions = (context: ActionContext): FieldOption[] => [
  { value: '', label: 'Every worktree' },
  ...(context.task?.derived.worktrees ?? []).map((item) => ({
    value: item.repository,
    label: item.repository,
    hint: item.dirty ? `${item.changes} changed` : 'clean',
  })),
]

/** How agent-style actions are attributed: to the manager with an override, or to the agent. */
const actingAsField: FieldSpec = {
  key: 'acting',
  label: 'Report as',
  kind: 'segmented',
  options: (context) => [
    { value: 'manager', label: `${context.actor} (manager override)` },
    {
      value: 'agent',
      label: `agent:${context.task?.assigned_agent_alias || 'unassigned'}`,
      disabled: !context.task?.assigned_agent_alias,
    },
  ],
  initial: () => 'manager',
  help: 'Agents report with their own actor. A manager must pass the override to report for them.',
}

function acting(values: Values, context: ActionContext): { actor: string; override: boolean } {
  if (values.acting === 'agent' && context.task?.assigned_agent_alias) {
    return { actor: `agent:${context.task.assigned_agent_alias}`, override: false }
  }
  return { actor: context.actor, override: true }
}

export const ACTIONS: Record<string, ActionSpec> = {
  start: {
    id: 'start',
    gate: 'start',
    title: () => 'Start work',
    submit: 'Start work',
    description: () => 'Moves the task to In Progress with the note "Task started."',
    fields: [actorField],
    request: (values, context) => ({ method: 'POST', path: taskPath(context, '/start'), body: { actor: text(values, 'actor') } }),
    command: (values, context) => alfredCommand('task', 'start', ['--task', number(context)], actorFlag(values)),
  },
  progress: {
    id: 'progress',
    gate: 'progress',
    title: () => 'Record progress',
    submit: 'Record progress',
    description: () => 'Saves a progress note and moves the task to In Progress. It also resets any earlier review approval.',
    fields: [
      { key: 'note', label: 'Progress note', kind: 'textarea', required: true, rows: 3, placeholder: 'What changed?' },
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/progress'),
      body: { note: text(values, 'note'), actor: text(values, 'actor') },
    }),
    command: (values, context) =>
      alfredCommand('task', 'progress', ['--task', number(context)], ['--note', text(values, 'note') || '<note>'], actorFlag(values)),
  },
  block: {
    id: 'block',
    gate: 'block',
    title: () => 'Block task',
    submit: 'Block task',
    tone: 'danger',
    fields: [
      { key: 'reason', label: 'Reason', kind: 'textarea', required: true, rows: 2, placeholder: 'What is it waiting for?' },
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/block'),
      body: { reason: text(values, 'reason'), actor: text(values, 'actor') },
    }),
    command: (values, context) =>
      alfredCommand('task', 'block', ['--task', number(context)], ['--reason', text(values, 'reason') || '<reason>'], actorFlag(values)),
  },
  unblock: {
    id: 'unblock',
    gate: 'unblock',
    title: (context) => (context.task?.status === 'On Hold' ? 'Resume task' : 'Unblock task'),
    submit: (_, context) => (context.task?.status === 'On Hold' ? 'Resume task' : 'Unblock task'),
    description: () => 'Returns the task to Pending. An agent run blocked at completion resumes when the agent records unblocked.',
    fields: [{ key: 'note', label: 'Note', kind: 'textarea', rows: 2, placeholder: 'Blocker resolved.' }, actorField],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/unblock'),
      body: { note: text(values, 'note'), actor: text(values, 'actor') },
    }),
    command: (values, context) =>
      alfredCommand('task', 'unblock', ['--task', number(context)], ['--note', text(values, 'note')], actorFlag(values)),
  },
  hold: {
    id: 'hold',
    gate: 'hold',
    title: () => 'Put on hold',
    submit: 'Put on hold',
    description: () =>
      'Sets Alfred’s On Hold status through the task service. The CLI has no command for it; resume with Unblock.',
    fields: [{ key: 'note', label: 'Why is it paused?', kind: 'textarea', rows: 2 }, actorField],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/hold'),
      body: { note: text(values, 'note'), actor: text(values, 'actor') },
    }),
    command: () => null,
  },
  review: {
    id: 'review',
    gate: 'review',
    title: () => 'Record review',
    submit: (values) => (values.decision === 'changes_requested' ? 'Request changes' : 'Approve'),
    description: () =>
      'Merge requires an approval recorded after the latest run, progress, or status change. Requesting changes moves the task to In Progress.',
    fields: [
      {
        key: 'decision',
        label: 'Decision',
        kind: 'segmented',
        options: (context) => [
          { value: 'approved', label: 'Approve', disabled: context.task ? !context.task.derived.actions.review.approve : false },
          {
            value: 'changes_requested',
            label: 'Request changes',
            disabled: context.task ? !context.task.derived.actions.review.request_changes : false,
          },
        ],
        initial: (context) => (context.task && !context.task.derived.actions.review.approve ? 'changes_requested' : 'approved'),
      },
      { key: 'note', label: 'Review note', kind: 'textarea', required: true, rows: 3, placeholder: 'Review passed.' },
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/review'),
      body: { decision: values.decision, note: text(values, 'note'), actor: text(values, 'actor') },
    }),
    command: (values, context) =>
      alfredCommand(
        'task',
        'review',
        ['--task', number(context)],
        ['--decision', String(values.decision)],
        ['--note', text(values, 'note') || '<note>'],
        actorFlag(values),
      ),
  },
  merge: {
    id: 'merge',
    gate: 'merge',
    title: () => 'Record merge',
    submit: 'Record merge',
    description: () => 'Records that the change was merged and moves the task to testing and deployment. Alfred does not merge for you.',
    fields: [{ key: 'mr', label: 'Merge request', kind: 'text', placeholder: '123 or a URL', mono: true }, actorField],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/merge'),
      body: { mr: text(values, 'mr'), actor: text(values, 'actor') },
    }),
    command: (values, context) => alfredCommand('task', 'merge', ['--task', number(context)], ['--mr', text(values, 'mr')], actorFlag(values)),
  },
  deploy: {
    id: 'deploy',
    gate: 'deploy',
    title: () => 'Record deployment',
    submit: 'Record deployment',
    description: () => 'Marks the task Completed. Alfred records the environment and result; it does not run a deployment tool.',
    fields: [
      { key: 'env', label: 'Environment', kind: 'text', initial: () => 'production', mono: true },
      { key: 'result', label: 'Result', kind: 'text', initial: () => 'passed' },
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/deploy'),
      body: { env: text(values, 'env') || 'production', result: text(values, 'result') || 'passed', actor: text(values, 'actor') },
    }),
    command: (values, context) =>
      alfredCommand(
        'task',
        'deploy',
        ['--task', number(context)],
        ['--env', text(values, 'env') === 'production' ? '' : text(values, 'env')],
        ['--result', text(values, 'result') === 'passed' ? '' : text(values, 'result')],
        actorFlag(values),
      ),
  },
  archive: {
    id: 'archive',
    gate: 'archive',
    title: () => 'Archive task',
    submit: 'Archive task',
    fields: [{ key: 'note', label: 'Note', kind: 'textarea', rows: 2, placeholder: 'Archived after completion.' }, actorField],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/archive'),
      body: { note: text(values, 'note'), actor: text(values, 'actor') },
    }),
    command: (values, context) =>
      alfredCommand('task', 'archive', ['--task', number(context)], ['--note', text(values, 'note')], actorFlag(values)),
  },
  consolidate: {
    id: 'consolidate',
    gate: 'consolidate',
    title: () => 'Consolidate task',
    submit: 'Consolidate task',
    tone: 'danger',
    description: () => 'Use this when the work is absorbed into another task. Consolidation is final.',
    fields: [{ key: 'note', label: 'Note', kind: 'textarea', rows: 2, placeholder: 'Covered by task 57' }, actorField],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/consolidate'),
      body: { note: text(values, 'note'), actor: text(values, 'actor') },
    }),
    command: (values, context) =>
      alfredCommand('task', 'consolidate', ['--task', number(context)], ['--note', text(values, 'note')], actorFlag(values)),
  },
  cancel: {
    id: 'cancel',
    gate: 'cancel',
    title: () => 'Cancel task',
    submit: 'Cancel task',
    tone: 'danger',
    description: () =>
      'Abandons the task at any stage and archives it. A live run is stopped first and the UI saves its transcript. Cancellation is final.',
    fields: [
      { key: 'reason', label: 'Reason', kind: 'textarea', required: true, rows: 2, placeholder: 'No longer needed' },
      { key: 'cleanup', label: 'Also remove the task’s worktrees', kind: 'toggle', initial: () => false },
      {
        key: 'force',
        label: 'Discard uncommitted changes (force)',
        kind: 'toggle',
        initial: () => false,
        visible: (values) => flag(values, 'cleanup'),
        help: 'Without force, dirty worktrees make the cancel fail before anything changes.',
      },
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/cancel'),
      body: {
        reason: text(values, 'reason'),
        cleanup: flag(values, 'cleanup'),
        force: flag(values, 'cleanup') && flag(values, 'force'),
        actor: text(values, 'actor'),
      },
    }),
    command: (values, context) =>
      alfredCommand(
        'task',
        'cancel',
        ['--task', number(context)],
        ['--reason', text(values, 'reason') || '<reason>'],
        flag(values, 'cleanup') ? ['--cleanup', 'yes'] : false,
        flag(values, 'cleanup') && flag(values, 'force') ? '--force' : false,
        actorFlag(values),
      ),
    warning: (values, context) =>
      flag(values, 'cleanup') && flag(values, 'force') && context.task?.derived.dirty
        ? 'Uncommitted changes in the worktrees will be lost.'
        : null,
  },
  assign: {
    id: 'assign',
    gate: 'assign',
    title: () => 'Assign agent',
    submit: 'Assign agent',
    fields: [
      {
        key: 'agent',
        label: 'Agent',
        kind: 'select',
        required: true,
        options: agentOptions,
        initial: (context) => context.task?.assigned_agent_alias || context.config?.agents[0]?.alias || '',
      },
      {
        key: 'dispatch',
        label: 'Dispatch',
        kind: 'segmented',
        options: () => [
          { value: '', label: 'Keep as is' },
          { value: 'auto', label: 'Trigger explicitly' },
          { value: 'queued', label: 'Queue for trigger all' },
        ],
        initial: () => '',
      },
      ...modelFields,
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/assign'),
      body: {
        agent: text(values, 'agent'),
        dispatch: text(values, 'dispatch') || null,
        model: chosenModel(values, context) || null,
        actor: text(values, 'actor'),
      },
    }),
    command: (values, context) =>
      alfredCommand(
        'agent',
        'assign',
        ['--task', number(context)],
        ['--to', text(values, 'agent') || '<agent>'],
        ['--dispatch', text(values, 'dispatch')],
        ['--model', chosenModel(values, context)],
        actorFlag(values),
      ),
  },
  reassign: {
    id: 'reassign',
    gate: 'reassign',
    title: () => 'Reassign agent',
    submit: 'Reassign agent',
    fields: [
      { key: 'agent', label: 'New agent', kind: 'select', required: true, options: agentOptions, initial: () => '' },
      {
        key: 'mode',
        label: 'Existing session',
        kind: 'segmented',
        options: () => [
          { value: 'soft-switch', label: 'Keep it running (soft switch)' },
          { value: 'stop-and-switch', label: 'Stop it first' },
        ],
        initial: () => 'soft-switch',
        help: 'A soft switch only changes the assignment. Stopping first ends the active run and resets the task to Pending. Either way the new agent receives a handoff with the previous session’s context.',
      },
      ...modelFields,
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/reassign'),
      body: {
        agent: text(values, 'agent'),
        mode: values.mode,
        model: chosenModel(values, context) || null,
        actor: text(values, 'actor'),
      },
    }),
    command: (values, context) =>
      alfredCommand(
        'agent',
        'reassign',
        ['--task', number(context)],
        ['--to', text(values, 'agent') || '<agent>'],
        ['--mode', values.mode === 'soft-switch' ? '' : String(values.mode)],
        ['--model', chosenModel(values, context)],
        actorFlag(values),
      ),
  },
  trigger: {
    id: 'trigger',
    gate: 'trigger',
    title: (context) => (context.task?.derived.active_run?.run_status === 'queued' ? 'Dispatch queued run again' : 'Trigger task'),
    submit: 'Trigger',
    tone: 'primary',
    description: (context) =>
      context.task?.execution_mode === 'plan-execution' && context.task.planning_state === 'pending'
        ? 'Starts the planning phase in a new session without creating worktrees.'
        : 'Creates or reuses worktrees and starts the agent session with the execution prompt.',
    fields: [actorField],
    request: (values, context) => ({
      method: 'POST',
      path: '/runs/trigger',
      body: { tasks: [number(context)], parallel: 1, actor: text(values, 'actor') },
    }),
    command: (values, context) => alfredCommand('run', 'trigger', ['--tasks', number(context)], actorFlag(values)),
  },
  continue: {
    id: 'continue',
    gate: 'continue',
    title: () => 'Approve plan and start execution',
    submit: 'Approve and continue',
    tone: 'primary',
    description: () =>
      'Creates the worktrees and sends the execution prompt to the same session. Your note reaches the agent as the task’s latest note.',
    fields: [
      { key: 'note', label: 'Note to the agent', kind: 'textarea', rows: 3, placeholder: 'Plan approved.' },
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/run/continue'),
      body: { note: text(values, 'note'), actor: text(values, 'actor') },
    }),
    command: (values, context) =>
      alfredCommand('run', 'continue', ['--task', number(context)], ['--note', text(values, 'note')], actorFlag(values)),
  },
  stop: {
    id: 'stop',
    gate: 'stop',
    title: () => 'Stop run',
    submit: 'Stop run',
    tone: 'danger',
    description: () =>
      'Closes the agent session, marks the run stopped, and resets the task to Pending. The UI saves the session transcript first.',
    fields: [
      { key: 'reason', label: 'Reason', kind: 'text', initial: () => 'manual stop' },
      { key: 'cleanup', label: 'Also remove the task’s worktrees', kind: 'toggle', initial: () => false },
      {
        key: 'force',
        label: 'Discard uncommitted changes (force)',
        kind: 'toggle',
        initial: () => false,
        visible: (values) => flag(values, 'cleanup'),
        help: 'Without force, dirty worktrees make the stop fail before anything changes.',
      },
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/run/stop'),
      body: {
        reason: text(values, 'reason') || 'manual stop',
        cleanup: flag(values, 'cleanup'),
        force: flag(values, 'cleanup') && flag(values, 'force'),
        actor: text(values, 'actor'),
      },
    }),
    command: (values, context) =>
      alfredCommand(
        'run',
        'stop',
        ['--task', number(context)],
        ['--reason', text(values, 'reason') === 'manual stop' ? '' : text(values, 'reason')],
        ['--cleanup', flag(values, 'cleanup') ? 'yes' : 'no'],
        flag(values, 'cleanup') && flag(values, 'force') ? '--force' : false,
      ),
    warning: (values, context) =>
      flag(values, 'cleanup') && flag(values, 'force') && context.task?.derived.dirty
        ? 'Uncommitted changes in the worktrees will be lost.'
        : null,
  },
  reopen: {
    id: 'reopen',
    gate: 'reopen',
    title: () => 'Reopen with a new attempt',
    submit: 'Reopen',
    tone: 'primary',
    description: () => 'Starts a new execution attempt, reusing valid worktrees and the existing session when it is still alive.',
    fields: [actorField],
    request: (values, context) => ({ method: 'POST', path: taskPath(context, '/run/reopen'), body: { actor: text(values, 'actor') } }),
    command: (values, context) => alfredCommand('run', 'reopen', ['--task', number(context)], actorFlag(values)),
  },
  event: {
    id: 'event',
    gate: 'event',
    title: () => 'Record agent event',
    submit: 'Record event',
    description: () => 'The same lifecycle events an agent reports from its session.',
    fields: [
      {
        key: 'type',
        label: 'Event',
        kind: 'select',
        required: true,
        options: () =>
          EVENT_TYPES.map((value) => ({
            value,
            label: value.replace(/_/g, ' '),
            hint: EVENT_HINTS[value],
          })),
        initial: () => 'progress',
      },
      { key: 'note', label: 'Note', kind: 'textarea', rows: 2 },
      actingAsField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/run/event'),
      body: { type: values.type, note: text(values, 'note'), ...acting(values, context) },
    }),
    command: (values, context) => {
      const who = acting(values, context)
      return alfredCommand(
        'run',
        'event',
        ['--task', number(context)],
        ['--type', String(values.type)],
        ['--note', text(values, 'note')],
        ['--actor', who.actor],
        who.override ? '--override-manager' : false,
      )
    },
  },
  complete: {
    id: 'complete',
    gate: 'complete',
    title: () => 'Report completion',
    submit: 'Report completion',
    description: () =>
      'Ends the active run and writes the completion handoff. The coordinator turns it into a notification for review.',
    fields: [
      {
        key: 'result',
        label: 'Result',
        kind: 'segmented',
        options: () => [
          { value: 'success', label: 'Success' },
          { value: 'failed', label: 'Failed' },
          { value: 'blocked', label: 'Blocked' },
        ],
        initial: () => 'success',
      },
      { key: 'note', label: 'Summary', kind: 'textarea', required: true, rows: 3 },
      actingAsField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/run/complete'),
      body: { result: values.result, note: text(values, 'note'), ...acting(values, context) },
    }),
    command: (values, context) => {
      const who = acting(values, context)
      return alfredCommand(
        'run',
        'complete',
        ['--task', number(context)],
        ['--result', String(values.result)],
        ['--note', text(values, 'note') || '<summary>'],
        ['--actor', who.actor],
        who.override ? '--override-manager' : false,
      )
    },
    warning: (values, context) => {
      const required = context.config?.knowledge.required_completion_entries ?? 0
      const have = context.task?.derived.knowledge_count ?? 0
      return values.result === 'success' && have < required
        ? `This task has ${have} knowledge entr${have === 1 ? 'y' : 'ies'}; ${required} are required, so the review notification will carry a validation issue.`
        : null
    },
  },
  worktree_create: {
    id: 'worktree_create',
    gate: 'worktree_create',
    title: () => 'Create worktrees',
    submit: 'Create worktrees',
    description: (context) =>
      `Creates task-${number(context)} worktrees on branch ${context.task?.branch_name || '(no branch)'} from each repository’s default branch, or reuses them.`,
    fields: [
      {
        key: 'repos',
        label: 'Repositories',
        kind: 'checklist',
        options: repositoryOptions,
        initial: (context) => [...(context.task?.target_repositories ?? [])],
        help: 'With nothing selected, the task’s repositories (or the default selection) are used.',
      },
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/worktrees'),
      body: { repos: (values.repos as string[]) ?? [] },
    }),
    command: (values, context) =>
      `${alfredCommand('worktree', 'create', ['--task', number(context)])} --repos ${shellQuote(csv((values.repos as string[]) ?? []))}`,
  },
  commit: {
    id: 'commit',
    gate: 'commit',
    title: () => 'Commit worktree changes',
    submit: 'Commit',
    tone: 'primary',
    description: () => 'Stages every change in each selected dirty worktree and commits it with the configured tag.',
    fields: [
      {
        key: 'type',
        label: 'Type',
        kind: 'segmented',
        options: (context) =>
          Object.entries(context.config?.commit_tags ?? {}).map(([key, tag]) => ({ value: key, label: tag })),
        initial: (context) => Object.keys(context.config?.commit_tags ?? {}).includes('FEATURE') ? 'FEATURE' : Object.keys(context.config?.commit_tags ?? {})[0] ?? '',
      },
      { key: 'message', label: 'Message', kind: 'text', required: true, placeholder: 'Add health endpoint' },
      { key: 'repo', label: 'Worktree', kind: 'select', options: worktreeRepositoryOptions, initial: () => '' },
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/worktrees/commit'),
      body: { type: text(values, 'type'), message: text(values, 'message'), repo: text(values, 'repo') },
    }),
    command: (values, context) =>
      alfredCommand(
        'worktree',
        'commit',
        ['--task', number(context)],
        ['--type', text(values, 'type').toLowerCase()],
        ['--message', text(values, 'message') || '<message>'],
        ['--repo', text(values, 'repo')],
      ),
  },
  push: {
    id: 'push',
    gate: 'push',
    title: () => 'Push task branch',
    submit: 'Push',
    tone: 'brass',
    description: () => 'Pushes the task branch to each repository’s configured remote. This is Alfred’s only network operation.',
    fields: [
      { key: 'repo', label: 'Worktree', kind: 'select', options: worktreeRepositoryOptions, initial: () => '' },
      {
        key: 'merge_request',
        label: 'Open a merge request',
        kind: 'toggle',
        initial: () => false,
        help: 'GitLab opens one against the default branch, or reuses the open one. The link appears in Review.',
      },
      {
        key: 'title',
        label: 'Merge request title',
        kind: 'text',
        initial: (context) => context.task?.title ?? '',
        visible: (values) => flag(values, 'merge_request'),
      },
      {
        key: 'description',
        label: 'Description',
        kind: 'textarea',
        rows: 3,
        visible: (values) => flag(values, 'merge_request'),
      },
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/worktrees/push'),
      body: {
        repo: text(values, 'repo'),
        merge_request: flag(values, 'merge_request'),
        title: flag(values, 'merge_request') ? text(values, 'title') : '',
        description: flag(values, 'merge_request') ? text(values, 'description') : '',
      },
    }),
    command: (values, context) =>
      alfredCommand(
        'worktree',
        'push',
        ['--task', number(context)],
        ['--repo', text(values, 'repo')],
        flag(values, 'merge_request') ? '--merge-request' : false,
        ['--title', flag(values, 'merge_request') && text(values, 'title') !== (context.task?.title ?? '') ? text(values, 'title') : ''],
        ['--description', flag(values, 'merge_request') ? text(values, 'description') : ''],
      ),
    warning: (_, context) =>
      context.task?.derived.dirty ? 'Some worktrees have uncommitted changes; only committed work is pushed.' : null,
  },
  remove_worktrees: {
    id: 'remove_worktrees',
    gate: 'remove_worktrees',
    title: () => 'Remove worktrees',
    submit: 'Remove worktrees',
    tone: 'danger',
    description: () =>
      'Unregisters and deletes this task’s worktrees after its run has ended. Branches stay in the repositories. The CLI leaves this step to manual Git.',
    fields: [
      {
        key: 'force',
        label: 'Discard uncommitted changes (force)',
        kind: 'toggle',
        initial: () => false,
        help: 'Without force, dirty worktrees are kept and the removal is refused.',
      },
      actorField,
    ],
    request: (values, context) => ({
      method: 'POST',
      path: taskPath(context, '/worktrees/remove'),
      body: { force: flag(values, 'force'), actor: text(values, 'actor') },
    }),
    command: (values, context) => {
      const paths = (context.task?.derived.worktrees ?? []).map((item) => item.path)
      return paths.length
        ? paths.map((path) => `git worktree remove ${flag(values, 'force') ? '--force ' : ''}${path}`).join('\n')
        : null
    },
  },
  sync_task: {
    id: 'sync_task',
    title: () => 'Reconcile tracker rows',
    submit: 'Apply to tracker',
    description: () => 'Re-sends this task through the Markdown tracker transaction and records SYNC_APPLIED.',
    fields: [actorField],
    request: (values, context) => ({ method: 'POST', path: '/sync/apply', body: { task: number(context), actor: text(values, 'actor') } }),
    command: (_, context) => alfredCommand('sync', 'apply', ['--task', number(context)]),
  },
  knowledge: {
    id: 'knowledge',
    title: () => 'Add knowledge entry',
    submit: 'Add entry',
    description: (context) => {
      const required = context.config?.knowledge.required_completion_entries ?? 0
      return required
        ? `Successful completions need ${required} entr${required === 1 ? 'y' : 'ies'} for their task.`
        : 'Reusable project knowledge, stored as Markdown.'
    },
    fields: [
      {
        key: 'task',
        label: 'Task number',
        kind: 'text',
        required: true,
        placeholder: '42',
        visible: (_, context) => !context.task,
      },
      {
        key: 'category',
        label: 'Category',
        kind: 'segmented',
        options: () => KNOWLEDGE_CATEGORIES.map((value) => ({ value, label: value })),
        initial: () => 'patterns',
      },
      { key: 'title', label: 'Title', kind: 'text', required: true },
      { key: 'content', label: 'Learning', kind: 'textarea', required: true, rows: 4 },
      {
        key: 'agent',
        label: 'Agent',
        kind: 'select',
        options: (context) => [{ value: '', label: 'None' }, ...agentOptions(context)],
        initial: (context) => context.task?.assigned_agent_alias ?? '',
      },
      { key: 'files', label: 'Related files', kind: 'text', placeholder: 'src/health.py, tests/test_health.py', mono: true },
      {
        key: 'modules',
        label: 'Related repositories',
        kind: 'checklist',
        options: repositoryOptions,
        initial: (context) => [...(context.task?.target_repositories ?? [])],
      },
    ],
    request: (values, context) => ({
      method: 'POST',
      path: '/knowledge',
      body: {
        task: knowledgeTask(values, context),
        category: values.category,
        title: text(values, 'title'),
        content: text(values, 'content'),
        agent: text(values, 'agent'),
        files: splitList(text(values, 'files')),
        modules: (values.modules as string[]) ?? [],
      },
    }),
    command: (values, context) =>
      alfredCommand(
        'knowledge',
        'add',
        ['--task', knowledgeTask(values, context) || '<task>'],
        ['--category', String(values.category)],
        ['--title', text(values, 'title') || '<title>'],
        ['--content', text(values, 'content') || '<learning>'],
        ['--agent', text(values, 'agent')],
        ['--files', csv(splitList(text(values, 'files')))],
        ['--modules', csv((values.modules as string[]) ?? [])],
      ),
  },
}

const EVENT_HINTS: Record<string, string> = {
  plan_completed: 'The plan is ready for review (planning phase only)',
  plan_approved: 'Approve the plan and continue into execution',
  progress: 'Work is progressing; moves the task to In Progress',
  coding: 'Coding has started; moves the task to In Progress',
  execution_started: 'Execution began; moves the task to In Progress',
  blocked: 'Something outside the agent is needed',
  unblocked: 'The blocker is resolved; a blocked run resumes',
  review_requested: 'Ready for review; moves the task to MR in Review',
  fixing: 'Addressing review feedback; moves the task to In Progress',
}

function knowledgeTask(values: Values, context: ActionContext): number {
  return context.task ? context.task.task_number : Number(text(values, 'task')) || 0
}

export function splitList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

/** Initial form values for an action. */
export function initialValues(spec: ActionSpec, context: ActionContext, overrides: Values = {}): Values {
  const values: Values = {}
  for (const field of spec.fields) {
    values[field.key] = field.initial ? field.initial(context) : field.kind === 'toggle' ? false : field.kind === 'checklist' ? [] : ''
  }
  return { ...values, ...overrides }
}

/** Human description of the next step for a task, used to highlight the primary action. */
export function nextStep(task: Task): { action: string | null; label: string } {
  const actions = task.derived.actions
  const plan = task.derived.plan
  if (task.status === 'Cancelled') return { action: null, label: 'Cancelled' }
  if (!task.assigned_agent_alias) return { action: 'assign', label: 'Assign an agent' }
  if (plan.awaiting_approval && plan.reported && actions.continue.enabled) return { action: 'continue', label: 'Review and approve the plan' }
  if (plan.awaiting_approval) return { action: null, label: 'Agent is planning' }
  if (task.derived.completion_pending) return { action: null, label: 'Completion waiting for the coordinator' }
  if (task.status === 'Blocked') return { action: 'unblock', label: 'Resolve the blocker' }
  if (task.status === 'On Hold') return { action: 'unblock', label: 'Resume when ready' }
  if (task.derived.active_run?.run_status === 'queued') return { action: 'trigger', label: 'Dispatch the queued run' }
  if (task.derived.active_run) return { action: null, label: 'Agent is working' }
  if (task.lifecycle_phase === 'testing_deployment') {
    if (task.status === 'Completed') return { action: 'archive', label: 'Archive' }
    return { action: 'deploy', label: 'Record the deployment' }
  }
  if (task.status === 'MR in Review') {
    return task.derived.approved_since_last_work
      ? { action: 'merge', label: 'Record the merge' }
      : { action: 'review', label: 'Review the work' }
  }
  if (task.status === 'Completed' || task.status === 'Consolidated') return { action: null, label: 'Finished' }
  if (actions.reopen.enabled && task.derived.run_count > 0) return { action: 'reopen', label: 'Reopen with a new attempt' }
  if (actions.trigger.enabled) return { action: 'trigger', label: task.execution_mode === 'plan-execution' ? 'Start planning' : 'Trigger the agent' }
  return { action: null, label: '' }
}
