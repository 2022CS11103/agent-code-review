"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import { Notice, PageFrame } from "@/components/page-frame"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { shortSha } from "@/lib/format"
import type { Policy, PullDetail, RepositorySummary } from "@/lib/types"

type RepoDetail = RepositorySummary & { pulls: PullDetail["pull_request"][] }

const SEVERITIES = ["low", "medium", "high", "critical"]

export default function RepositoryPage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ["repository", id],
    queryFn: () => api<RepoDetail>(`/repositories/${id}`),
  })

  if (query.isLoading) return <Notice title="Loading repository policy" />
  if (query.isError || !query.data) return <Notice title="Repository could not be loaded" detail={(query.error as Error)?.message} />
  const repo = query.data

  return (
    <PageFrame
      eyebrow={repo.connection_status}
      title={`${repo.owner}/${repo.name}`}
      note={`${repo.trust_tier} · ${repo.primary_language}. The model preference stored on the policy does not switch the running provider. This build always uses the deterministic mock.`}
    >
      <div className="grid lg:grid-cols-[minmax(0,1fr)_320px]">
        {repo.policy ? (
          <PolicyForm
            policy={repo.policy}
            onSave={async (body) => {
              try {
                await api(`/repositories/${id}/policy`, { method: "PATCH", body: JSON.stringify(body) })
                toast.success("Policy saved")
                client.invalidateQueries({ queryKey: ["repository", id] })
              } catch (error) {
                toast.error((error as Error).message)
              }
            }}
          />
        ) : (
          <p className="p-4 text-sm text-muted-foreground">This repository has no review policy.</p>
        )}
        <aside className="border-t lg:border-t-0 lg:border-l">
          <h2 className="border-b px-4 py-2 text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Pull requests</h2>
          <ul>
            {repo.pulls.map((pull) => (
              <li key={pull.id} className="border-b px-4 py-3 text-sm">
                <Link href={`/pulls/${pull.id}`} className="hover:underline">
                  #{pull.number} {pull.title}
                </Link>
                <div className="text-xs text-muted-foreground">
                  {pull.author} · {pull.state} · {shortSha(pull.head_sha)}
                </div>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </PageFrame>
  )
}

function PolicyForm({ policy, onSave }: { policy: Policy; onSave: (body: Record<string, unknown>) => Promise<void> }) {
  const [severity, setSeverity] = useState(policy.severity_threshold)
  const [githubMin, setGithubMin] = useState(policy.github_min_severity)
  const [exclude, setExclude] = useState(policy.exclude_globs.join("\n"))
  const [checks, setChecks] = useState(policy.required_checks.join(", "))
  const [budget, setBudget] = useState(String(policy.token_budget))
  const [repairs, setRepairs] = useState(String(policy.max_repair_attempts))
  const [timeout, setTimeoutSec] = useState(String(policy.validation_timeout_seconds))
  const [post, setPost] = useState(policy.post_github_comments)
  const [saving, setSaving] = useState(false)

  return (
    <form
      className="space-y-4 px-4 py-4 text-sm"
      onSubmit={async (event) => {
        event.preventDefault()
        setSaving(true)
        await onSave({
          severity_threshold: severity,
          github_min_severity: githubMin,
          exclude_globs: exclude.split("\n").map((item) => item.trim()).filter(Boolean),
          required_checks: checks.split(",").map((item) => item.trim()).filter(Boolean),
          token_budget: Number(budget),
          max_repair_attempts: Number(repairs),
          validation_timeout_seconds: Number(timeout),
          post_github_comments: post,
        })
        setSaving(false)
      }}
    >
      <Field label="Severity threshold">
        <Select value={severity} onChange={setSeverity} options={SEVERITIES} />
      </Field>
      <Field label="GitHub comment minimum">
        <Select value={githubMin} onChange={setGithubMin} options={SEVERITIES} />
      </Field>
      <Field label="Exclude globs, one per line">
        <textarea className="min-h-24 w-full rounded-md border bg-card px-2 py-1 font-mono text-xs" value={exclude} onChange={(event) => setExclude(event.target.value)} />
      </Field>
      <Field label="Required checks, comma separated">
        <input className="w-full rounded-md border bg-card px-2 py-1 text-xs" value={checks} onChange={(event) => setChecks(event.target.value)} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Token budget">
          <input className="w-full rounded-md border bg-card px-2 py-1" type="number" min={1000} max={200000} value={budget} onChange={(event) => setBudget(event.target.value)} />
        </Field>
        <Field label="Repair attempts (max 3)">
          <input className="w-full rounded-md border bg-card px-2 py-1" type="number" min={0} max={3} value={repairs} onChange={(event) => setRepairs(event.target.value)} />
        </Field>
        <Field label="Validation timeout (s)">
          <input className="w-full rounded-md border bg-card px-2 py-1" type="number" min={1} max={120} value={timeout} onChange={(event) => setTimeoutSec(event.target.value)} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={post} onChange={(event) => setPost(event.target.checked)} />
        Allow GitHub comments when a real installation exists. Demo reviews still stay local.
      </label>
      <p className="text-xs text-muted-foreground">Stored model preference: {policy.model_provider}. The running provider is the deterministic mock.</p>
      <Button type="submit" size="sm" disabled={saving}>
        {saving ? "Saving…" : "Save policy"}
      </Button>
    </form>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}

function Select({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: string[] }) {
  return (
    <select className="rounded-md border bg-card px-2 py-1" value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  )
}
