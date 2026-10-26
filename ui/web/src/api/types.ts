/** Types mirroring the JSON the Alfred UI server returns. */

export type TaskStatus =
  | 'Pending'
  | 'Queued'
  | 'Running'
  | 'In Progress'
  | 'Blocked'
  | 'On Hold'
  | 'MR in Review'
  | 'Completed'
  | 'Consolidated'
  | 'Cancelled'

export type TaskType = 'development' | 'research' | 'analysis'
export type LifecyclePhase = 'active' | 'testing_deployment' | 'archived' | 'consolidated'
export type PlanningState = 'not_required' | 'pending' | 'started' | 'approved' | 'completed'
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'blocked' | 'stopped'
export type ExecutionMode = 'direct' | 'plan-execution'

export interface ActionState {
  enabled: boolean
  reason: string
  approve?: boolean
  request_changes?: boolean
}

export type ActionName =
  | 'trigger'
  | 'continue'
  | 'stop'
  | 'reopen'
  | 'event'
  | 'complete'
  | 'progress'
  | 'start'
  | 'block'
  | 'unblock'
  | 'hold'
  | 'review'
  | 'merge'
  | 'deploy'
  | 'archive'
  | 'cancel'
  | 'consolidate'
  | 'assign'
  | 'reassign'
  | 'update'
  | 'worktree_create'
  | 'commit'
  | 'push'
  | 'remove_worktrees'

export interface AgentRun {
  run_id: string
  task_number: number
  agent_alias: string
  runtime_target: string
  run_status: RunStatus
  started_at: string
  ended_at: string
  summary: string
  phase: string
  command_preview: string
  worktree_paths: Record<string, string>
  session_name: string
  session_status: string
  last_event_at: string
}

export interface TaskEvent {
  index: number
  task_number: number
  timestamp: string
  actor: string
  event_type: string
  details: string
}

export interface InvolvedAgent {
  alias: string
  configured: boolean
  assigned: boolean
  cli: string
  model: string
  runtime_target: string
  runs: number
  events: number
  last_seen: string
  live: boolean
  roles: string[]
}

export interface JourneyStep {
  key: string
  label: string
  state: 'done' | 'current' | 'blocked' | 'pending' | 'skipped'
}

export interface WorktreeSummary {
  repository: string
  path: string
  branch: string
  dirty: boolean
  changes: number
}

export interface TaskSession {
  name: string
  alive: boolean
  attached: number
  activity: number
  command: string
  pid: number
  dead: boolean
  width: number
  height: number
}

export interface TaskDerived {
  active_run: AgentRun | null
  latest_run: AgentRun | null
  run_count: number
  session: TaskSession | null
  agents: InvolvedAgent[]
  approved_since_last_work: boolean
  plan: {
    required: boolean
    state: PlanningState
    awaiting_approval: boolean
    reported: boolean
    last_report: TaskEvent | null
  }
  last_event: TaskEvent | null
  event_count: number
  notifications: { pending: number; types: string[] }
  worktrees: WorktreeSummary[]
  dirty: boolean
  knowledge_count: number
  queued: boolean
  completion_pending: boolean
  transcripts: number
  journey: JourneyStep[]
  actions: Record<ActionName, ActionState>
}

export interface Task {
  task_number: number
  title: string
  description: string
  category: string
  task_type: TaskType
  model: string
  priority: string
  status: TaskStatus
  deadline: string
  notes: string
  dependencies: number[]
  assigned_agent_alias: string
  dispatch_mode: 'auto' | 'queued'
  execution_mode: ExecutionMode
  worktree_mode: 'enabled' | 'disabled'
  branch_name: string
  planning_state: PlanningState
  lifecycle_phase: LifecyclePhase
  target_repositories: string[]
  created_at: string
  updated_at: string
  derived: TaskDerived
}

export interface Notification {
  notification_id: string
  notification_type: string
  task_number: number
  created_at: string
  details: {
    status?: string
    summary?: string
    repositories?: string[]
    commits?: string[]
    agent?: string
    validation_issues?: string[]
    session_name?: string
    message?: string
    [key: string]: unknown
  }
  acknowledged: boolean
  acknowledged_at: string
}

export interface PaneInfo {
  session_name: string
  window_index: number
  pane_index: number
  active: boolean
  pane_id: string
  pid: number
  current_command: string
  dead: boolean
  dead_status: number | null
  width: number
  height: number
  cursor_x: number
  cursor_y: number
  history_size: number
  in_mode: boolean
  alternate_screen: boolean
  current_path: string
  title: string
}

export interface SessionInfo {
  name: string
  created: number
  activity: number
  attached: number
  windows: number
  pane: PaneInfo | null
  pane_count: number
  kind: 'task' | 'coordinator' | 'learner' | 'other'
  task_number: number | null
  agent_alias: string
  run_id: string
  run_status: string
}

export interface AgentDescription {
  alias: string
  runtime_target: string
  commands: { direct: string[]; plan: string[]; execution: string[] }
  executable: string
  cli: string
  model: string
  models: string[]
  default_model: string
  prompt_delivery: Record<'direct' | 'plan' | 'execution', 'argument' | 'paste'>
  placeholders: Record<string, string[]>
  unknown_placeholders: Record<string, string[]>
}

export interface RepositoryDescription {
  name: string
  path: string
  default_branch: string
  remote: string
  selected_by_default: boolean
  exists: boolean
  is_git: boolean
}

export interface ConfigDescription {
  config_path: string
  version: number
  workspace_root: string
  runtime: {
    timezone: string
    state_directory: string
    temp_directory: string
    worktree_directory: string
    session_prefix: string
    tmux_unavailable_policy: 'queue' | 'error'
  }
  repositories: RepositoryDescription[]
  agents: AgentDescription[]
  tracker: { enabled: boolean; canonical: string; agents: string; daily_notes: string }
  commit_tags: Record<string, string>
  knowledge: { directory: string; required_completion_entries: number }
}

export interface SnapshotError {
  kind: 'no_workspace' | 'config' | 'state'
  message: string
}

export interface Snapshot {
  server_time: number
  generated_at: string
  versions: { alfred: string; ui: string }
  workspace: { config_path: string; discovery_error: string; generation: number }
  tmux: { available: boolean; server_running?: boolean; foreign_sessions?: number }
  error: SnapshotError | null
  config?: ConfigDescription
  tasks?: Task[]
  runs?: AgentRun[]
  queue?: number[]
  notifications?: Notification[]
  events?: TaskEvent[]
  event_count?: number
  sessions?: SessionInfo[]
  coordinator?: { session_name: string; running: boolean }
  learner?: { session_name: string; running: boolean; agent: string }
  completions?: { pending: number; processed: number; invalid: number; oldest_pending?: string | null }
  knowledge?: { total: number; by_task: Record<string, number> }
}

export interface FileInfo {
  name: string
  path: string
  relative: string
  size: number
  modified: number
}

export interface PromptFile extends FileInfo {
  phase: 'plan' | 'execution'
  content: string
}

export interface CompletionEntry extends FileInfo {
  bucket: 'pending' | 'processed' | 'invalid'
  task_number: number | null
  report: Record<string, unknown> | null
  raw: string
  error: string
}

export interface CompletionListing {
  directory: string
  pending: CompletionEntry[]
  processed: CompletionEntry[]
  invalid: CompletionEntry[]
}

export interface KnowledgeEntry extends FileInfo {
  title: string
  task_number: number | null
  category: string
  created: string
  agent: string
  summary: string
  files: string[]
  repositories: string[]
  content: string
}

export interface Transcript extends FileInfo {
  task_number: number | null
  reason: string
  session_name: string
  captured_at: string
  content?: string
}

export interface RelatedTask {
  task_number: number
  title: string
  status: string
  phase?: string
  exists: boolean
}

export interface TaskDetail {
  task: Task
  runs: AgentRun[]
  events: TaskEvent[]
  notifications: Notification[]
  prompts: PromptFile[]
  completions: CompletionListing
  knowledge: KnowledgeEntry[]
  transcripts: Transcript[]
  drift: string[]
  dependencies: RelatedTask[]
  dependents: RelatedTask[]
}

export interface ChangeEntry {
  code: string
  path: string
  label: string
  original?: string
}

export interface CommitEntry {
  hash: string
  short: string
  author: string
  date: string
  subject: string
}

export interface CappedText {
  text: string
  truncated: boolean
}

export interface WorktreeDetail {
  repository: string
  path: string
  branch: string
  head: string
  head_subject: string
  base: string
  ahead: number | null
  behind: number | null
  commits: CommitEntry[]
  changes: ChangeEntry[]
  dirty: boolean
  remote: { remote: string; ref: string; exists: boolean; unpushed?: number | null; behind?: number | null }
  diff?: CappedText
  diff_stat?: string
  committed_diff?: CappedText
  committed_stat?: string
  untracked?: { path: string; size: number; binary: boolean; truncated: boolean; content: string }[]
}

export interface DoctorCheck {
  key: string
  title: string
  status: 'ok' | 'info' | 'warn' | 'error'
  detail: string
  fix: { action: string; label: string } | null
}

export interface ReportRow {
  task_number: number
  title: string
  status: string
  priority: string
  agent: string
  phase: string
  dependencies?: number[]
}

export interface Reports {
  today: ReportRow[]
  risk: ReportRow[]
  dependency: ReportRow[]
  velocity: { completed: number; total: number }
  by_status: Record<string, number>
  by_phase: Record<string, number>
  by_priority: Record<string, number>
  by_category: Record<string, number>
  by_agent: Record<string, number>
  throughput: { day: string; count: number }[]
  cycle_times: { task_number: number; title: string; hours: number }[]
  runs: {
    by_status: Record<string, number>
    by_agent: Record<string, number>
    average_hours_by_agent: Record<string, number>
  }
}

export interface TrackerFile {
  path: string
  exists: boolean
  content: string
  name?: string
  modified?: number
}

export interface TrackerListing {
  enabled: boolean
  canonical: TrackerFile
  agents: TrackerFile
  daily_notes: { path: string; exists: boolean; files: FileInfo[] }
  issues: string[]
}

export interface ConfigValidation {
  valid: boolean
  error: string
  summary?: ConfigDescription
}

export interface ConfigPayload {
  path: string
  text: string
  validation: ConfigValidation
  backup_exists: boolean
  presets: Record<
    string,
    {
      label: string
      runtime_target: string
      commands: { direct: string[]; plan: string[]; execution: string[] }
    }
  >
}

export interface SkillEntry {
  phase: 'plan' | 'execution'
  path: string
  exists: boolean
  content: string
}

export interface SkillListing {
  directory: string
  max_bytes: number
  skills: SkillEntry[]
}

export interface ServerStatus {
  versions: { alfred: string; ui: string }
  checkout: string
  packages: { alfred: string; alfred_ui: string }
  git: { available: boolean; branch?: string; commit?: string; subject?: string; changes?: number }
  started_at: string
  frontend: { static_directory: string; built_at: string; sources_changed_at: string; stale: boolean }
  backend: { changed_at: string; stale: boolean }
  rebuild: { available: boolean; reason: string }
  restart: { available: boolean; reason: string }
}

export interface RebuildResult {
  ok: boolean
  message: string
  output: string
  seconds: number
}

export interface ActionResult {
  ok: boolean
  message: string
  [key: string]: unknown
}

export interface ReviewComment {
  id: string
  repository: string
  path: string
  /** ``null`` for a comment on the whole file. */
  line: number | null
  side: 'old' | 'new'
  body: string
  author: string
  created_at: string
  updated_at: string | null
  resolved: boolean
  sent_at: string | null
}

export interface ReviewLink {
  repository: string
  branch: string
  url: string
  at: string
}

export interface ReviewDocument {
  version: number
  task_number: number
  comments: ReviewComment[]
  verdict: { decision: 'approved' | 'changes_requested'; actor: string; at: string } | null
  links: ReviewLink[]
}

export type PipelineStatus = 'running' | 'passed' | 'failed' | 'cancelled' | 'interrupted'
export type PipelineStepStatus = 'pending' | 'running' | 'passed' | 'failed' | 'skipped' | 'cancelled'

export interface PipelineStepSpec {
  repository: string
  name: string
  command: string[]
  timeout_minutes: number
}

export interface PipelineStepRun extends PipelineStepSpec {
  index: number
  status: PipelineStepStatus
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
  log: string
}

export interface PipelineRun {
  id: string
  task_number: number
  actor: string
  status: PipelineStatus
  started_at: string
  finished_at: string | null
  heads: Record<string, string>
  steps: PipelineStepRun[]
  error?: string
}

export interface PipelineConfig {
  path: string
  exists: boolean
  text: string
  steps: PipelineStepSpec[]
}
