export type Severity = "critical" | "high" | "medium" | "low" | "info"

export type JobBrief = {
  id: string
  status: string
  stage: string
  origin: string
  trigger: string
  head_sha: string
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
} | null

export type QueueItem = {
  pull_request_id: string
  number: number
  title: string
  author: string
  head_ref: string
  head_sha: string
  state: string
  repository: { id: string; owner: string; name: string; trust_tier: string }
  job: JobBrief
  open_findings: Record<Severity, number>
}

export type Metrics = {
  median_review_seconds: number | null
  completed_reviews: number
  failed_reviews: number
  running_reviews: number
  confirmed_rate: number | null
  decisions: number
  confirmed: number
  validation_success_rate: number | null
  validation_runs: number
  validation_passed: number
  sample_size: number
  origin: string
  note: string
}

export type Activity = {
  id: number
  sequence: number
  job_id: string
  stage: string
  level: string
  agent: string | null
  message: string
  payload: Record<string, unknown> | null
  created_at: string
}

export type CommandCenter = {
  workspace: { id: string; name: string; slug: string; demo: boolean }
  metrics: Metrics
  severity: Record<string, number>
  queue: QueueItem[]
  activity: Activity[]
  repositories: RepositorySummary[]
}

export type Policy = {
  severity_threshold: string
  github_min_severity: string
  required_checks: string[]
  exclude_globs: string[]
  model_provider: string
  token_budget: number
  max_repair_attempts: number
  validation_timeout_seconds: number
  post_github_comments: boolean
}

export type RepositorySummary = {
  id: string
  owner: string
  name: string
  provider: string
  connection_status: string
  trust_tier: string
  primary_language: string
  html_url: string
  policy: Policy | null
}

export type Evidence = {
  id: string
  kind: string
  file_path: string | null
  start_line: number | null
  end_line: number | null
  snippet: string
  detail: string
  trusted: boolean
}

export type Finding = {
  id: string
  stable_key: string
  category: string
  severity: Severity
  confidence: number
  claim_level: string
  status: string
  validation_state: string
  title: string
  explanation: string
  impact: string
  remediation: string
  file_path: string
  start_line: number
  end_line: number
  source: string
  rule_id: string | null
  agent: string | null
  observed: boolean
  uncertain_reason: string | null
  enclosing_symbol: string | null
  agent_note: string | null
  evidence: Evidence[]
}

export type TestCase = {
  name: string
  outcome: string
  duration_ms: number | null
  output: string | null
  phase: string
}

export type Validation = {
  id: string
  attempt: number
  status: string
  tests_selected: string[]
  tests_passed: number
  tests_failed: number
  baseline_passed: number
  baseline_failed: number
  security_status: string
  lint_status: string
  stdout: string
  stderr: string
  duration_ms: number
  exit_code: number | null
  timed_out: boolean
  notes: string
  tests: TestCase[]
}

export type Patch = {
  id: string
  finding_id: string | null
  status: string
  title: string
  diff_text: string
  patched_files: Record<string, string>
  target_paths: string[]
  attempt: number
  strategy: string
  proof_scope: string | null
  policy_notes: string | null
  disclaimer: string
  validations: Validation[]
}

export type ReviewFile = {
  id: string
  path: string
  status: string
  language: string
  additions: number
  deletions: number
  skipped_reason: string | null
  finding_count: number
}

export type ReviewDetail = {
  job: {
    id: string
    status: string
    stage: string
    origin: string
    trigger: string
    head_sha: string
    error: string | null
    correlation_id: string
    plan: Record<string, unknown> | null
    coverage: {
      skipped?: { path: string; reason: string }[]
      markers?: { path: string; marker: string }[]
      scanner_notes?: string[]
    } | null
    usage: {
      token_input: number
      token_output: number
      estimated_cost_usd: number
      billed: boolean
      rate_card: string
      provider: string
      note: string
    } | null
    prompt_version: string
    analyzer_version: string
    model_provider: string
    created_at: string
    started_at: string | null
    finished_at: string | null
  }
  pull_request: {
    id: string
    number: number
    title: string
    body: string
    author: string
    base_ref: string
    head_ref: string
    base_sha: string
    head_sha: string
    state: string
  }
  repository: RepositorySummary
  files: ReviewFile[]
  findings: Finding[]
  patches: Patch[]
  report: { id: string; body: string; delivery_status: string; destination: string; redacted: boolean } | null
}

export type SplitRow = {
  left_kind: string
  left_no: number | null
  left_text: string
  right_kind: string
  right_no: number | null
  right_text: string
}

export type FileDiff = {
  file_id: string
  path: string
  language: string
  status: string
  skipped_reason: string | null
  patch: string
  base_content: string
  head_content: string
  split: SplitRow[]
  coverage_note: string | null
}

export type Trace = {
  id: string
  agent: string
  node: string
  status: string
  duration_ms: number
  input_summary: string | null
  output: Record<string, unknown> | null
  tool_calls: { tool: string; status: string }[] | null
  token_input: number
  token_output: number
  estimated_cost_usd: number
  billed: boolean
  retry_count: number
  error: string | null
  correlation_id: string
}

export type EvaluationCase = {
  id: string
  expected: string[]
  found: string[]
  false_positive: string[]
  false_negative: string[]
}

export type EvaluationRun = {
  id: string
  fixture_set: string
  analyzer_version: string
  model_provider: string
  prompt_version: string
  precision: number | null
  recall: number | null
  f1: number | null
  false_positive_rate: number | null
  true_positive: number
  false_positive: number
  false_negative: number
  duration_ms: number
  details: { cases?: EvaluationCase[]; scope?: string } | null
  residual_risks: string[] | null
  created_at: string
}

export type AuditItem = {
  id: string
  action: string
  target_type: string | null
  target_id: string | null
  details: Record<string, unknown> | null
  created_at: string
  actor_id: string | null
}

export type SettingsView = {
  demo_mode: boolean
  workspace: { id: string; name: string; demo: boolean }
  llm: { active_provider: string; openai_configured: boolean; note: string }
  sandbox: { driver: string; image: string; limitations: string }
  github: { api_base: string; token_configured: boolean; webhook_secret_is_default: boolean }
  database_dialect: string
  worker_mode: string
  analyzer_version: string
  prompt_version: string
  repair_cap: number
}

export type PullDetail = {
  pull_request: ReviewDetail["pull_request"]
  repository: RepositorySummary
  files: {
    id: string
    path: string
    status: string
    additions: number
    deletions: number
    language: string
    skipped_reason: string | null
  }[]
  jobs: { id: string; status: string; stage: string; origin: string; created_at: string }[]
}

export type TraceResponse = {
  job_id: string
  correlation_id: string
  usage: ReviewDetail["job"]["usage"]
  plan: Record<string, unknown> | null
  traces: Trace[]
}

export type Me = {
  user: { id: string; email: string; name: string }
  workspace: { id: string; name: string; slug: string; demo: boolean }
  role: string
}
