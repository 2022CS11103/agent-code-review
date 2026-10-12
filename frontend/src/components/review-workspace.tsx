"use client"

import dynamic from "next/dynamic"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"
import { DiffView } from "@/components/diff-view"
import { Button, buttonVariants } from "@/components/ui/button"
import { api, subscribe } from "@/lib/api"
import { shortSha, titleCase } from "@/lib/format"
import type { FileDiff, Finding, ReviewDetail } from "@/lib/types"

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false })

const STAGES = ["ingest", "parse", "analyze", "agents", "remediate", "validate", "ready"]
const CATEGORIES = ["security", "correctness", "performance", "tests"]

export function ReviewWorkspace() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const router = useRouter()
  const search = useSearchParams()
  const client = useQueryClient()
  const [mode, setMode] = useState<"split" | "unified">("split")
  const review = useQuery({
    queryKey: ["review", id],
    queryFn: () => api<ReviewDetail>(`/reviews/${id}`),
    refetchInterval: (query) => {
      const status = query.state.data?.job.status
      return status === "queued" || status === "running" ? 1200 : false
    },
  })

  useEffect(() => {
    return subscribe(`/reviews/${id}/stream`, () => {
      client.invalidateQueries({ queryKey: ["review", id] })
    })
  }, [client, id])

  const data = review.data
  const fileParam = search.get("file")
  const findingParam = search.get("finding")
  const category = search.get("category")
  const severity = search.get("severity")
  const selectedFile = data?.files.find((file) => file.path === fileParam) ?? data?.files.find((file) => file.finding_count > 0) ?? data?.files[0]
  const diff = useQuery({
    queryKey: ["diff", data?.pull_request.id, selectedFile?.id],
    enabled: Boolean(data && selectedFile),
    queryFn: () => api<FileDiff>(`/pulls/${data!.pull_request.id}/files/${selectedFile!.id}`),
  })

  const findings = useMemo(() => {
    return (data?.findings ?? []).filter((finding) => {
      if (category && finding.category !== category) return false
      if (severity && finding.severity !== severity) return false
      if (selectedFile && finding.file_path !== selectedFile.path && finding.category !== "tests") return false
      return true
    })
  }, [data?.findings, category, severity, selectedFile])

  const selected = findings.find((finding) => finding.id === findingParam) ?? findings[0] ?? null

  const setQuery = useCallback(
    (next: Record<string, string | null>) => {
      const params = new URLSearchParams(search.toString())
      for (const [key, value] of Object.entries(next)) {
        if (!value) params.delete(key)
        else params.set(key, value)
      }
      router.replace(`/reviews/${id}?${params.toString()}`)
    },
    [id, router, search],
  )

  const decide = useCallback(
    async (action: "confirm" | "dismiss") => {
      if (!selected) return
      try {
        await api(`/findings/${selected.id}/decision`, { method: "POST", body: JSON.stringify({ action }) })
        toast.success(action === "confirm" ? "Finding confirmed" : "Finding dismissed")
        client.invalidateQueries({ queryKey: ["review", id] })
      } catch (error) {
        toast.error((error as Error).message)
      }
    },
    [client, id, selected],
  )

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return
      if (!findings.length) return
      const index = Math.max(0, findings.findIndex((finding) => finding.id === selected?.id))
      if (event.key === "j") {
        event.preventDefault()
        const next = findings[Math.min(findings.length - 1, index + 1)]
        setQuery({ finding: next.id, file: next.file_path })
      }
      if (event.key === "k") {
        event.preventDefault()
        const next = findings[Math.max(0, index - 1)]
        setQuery({ finding: next.id, file: next.file_path })
      }
      if (event.key === "c" && selected) void decide("confirm")
      if (event.key === "d" && selected) void decide("dismiss")
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [decide, findings, selected, setQuery])

  async function publish() {
    try {
      const result = await api<{ delivery_status: string; github_called: boolean }>(`/reviews/${id}/report`, { method: "POST" })
      toast.success(
        result.github_called
          ? `GitHub was contacted (${result.delivery_status}). Nothing was merged.`
          : `Report stored (${result.delivery_status}). GitHub was not called.`,
      )
      client.invalidateQueries({ queryKey: ["review", id] })
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  async function retry() {
    try {
      const result = await api<{ job_id: string }>(`/reviews/${id}/retry`, { method: "POST" })
      toast.success("Retry queued")
      router.push(`/reviews/${result.job_id}`)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  async function reanalyze() {
    if (!selected) return
    try {
      const result = await api<{ reanalysis_job_id: string }>(`/findings/${selected.id}/decision`, {
        method: "POST",
        body: JSON.stringify({ action: "request_reanalysis" }),
      })
      toast.success("Reanalysis queued")
      router.push(`/reviews/${result.reanalysis_job_id}`)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  if (review.isLoading) return <div className="p-6 text-sm text-muted-foreground">Loading review…</div>
  if (review.isError || !data) return <div className="p-6 text-sm">This review could not be loaded. {(review.error as Error)?.message}</div>

  const stageIndex = STAGES.indexOf(data.job.stage)
  const finished = data.job.status === "completed"
  const stopped = data.job.status === "failed" || data.job.status === "budget_exhausted"

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
            {data.repository.owner}/{data.repository.name} #{data.pull_request.number}
          </p>
          <h1 className="text-base font-medium">{data.pull_request.title}</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {data.pull_request.author} · {data.pull_request.base_ref} ← {data.pull_request.head_ref} ·{" "}
            <span className="mono">{shortSha(data.pull_request.head_sha)}</span> · {data.job.origin} · {data.job.model_provider}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => router.push(`/reviews/${id}/trace`)}>Trace</Button>
          <Button variant="outline" size="sm" onClick={() => router.push(`/reviews/${id}/validation`)}>Validation</Button>
          {stopped || data.job.status === "completed" ? (
            <Button variant="outline" size="sm" onClick={retry}>Retry</Button>
          ) : null}
          <Button size="sm" onClick={publish} disabled={data.job.status !== "completed"}>Publish report</Button>
        </div>
      </div>
      <ol className="flex gap-3 overflow-x-auto border-b px-4 py-2 text-[11px] uppercase tracking-wider">
        {STAGES.map((stage, index) => (
          <li key={stage} className={index <= stageIndex ? "text-foreground" : "text-muted-foreground"}>
            {index < stageIndex || (finished && index === stageIndex) ? "●" : index === stageIndex ? "◐" : "○"} {stage}
          </li>
        ))}
        {data.job.status === "failed" ? <li className="text-destructive">failed</li> : null}
        {data.job.status === "budget_exhausted" ? <li className="text-destructive">budget exhausted</li> : null}
      </ol>
      {data.job.error ? <p className="border-b px-4 py-2 text-sm text-destructive">{data.job.error}</p> : null}
      {data.job.status === "queued" || data.job.status === "running" ? (
        <p className="border-b px-4 py-2 text-xs text-muted-foreground">Review is {data.job.status}. This page updates from the event stream.</p>
      ) : null}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_340px]">
        <aside className="min-h-0 overflow-auto border-b lg:border-b-0 lg:border-r">
          <div className="px-3 py-2 text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Changed files</div>
          <ul>
            {data.files.map((file) => (
              <li key={file.id}>
                <button
                  type="button"
                  className={`block w-full px-3 py-1.5 text-left text-xs hover:bg-muted ${selectedFile?.id === file.id ? "bg-muted" : ""}`}
                  onClick={() => setQuery({ file: file.path, finding: null })}
                >
                  <span className="mono">{file.path}</span>
                  {file.skipped_reason ? <span className="mt-0.5 block text-[10px] text-muted-foreground">skipped · {file.skipped_reason}</span> : null}
                  {file.finding_count ? <span className="mt-0.5 block text-[10px] text-muted-foreground">{file.finding_count} findings</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <section className="min-h-0 overflow-auto">
          <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b bg-background px-3 py-2 text-xs">
            <span className="mono text-muted-foreground">{selectedFile?.path}</span>
            <button type="button" className={mode === "split" ? "underline" : ""} onClick={() => setMode("split")}>Split</button>
            <button type="button" className={mode === "unified" ? "underline" : ""} onClick={() => setMode("unified")}>Unified</button>
            <Filter label="All categories" value={category} options={CATEGORIES} onChange={(value) => setQuery({ category: value })} />
            <Filter label="All severities" value={severity} options={["critical", "high", "medium", "low"]} onChange={(value) => setQuery({ severity: value })} />
            <span className="ml-auto text-muted-foreground">j/k findings · c confirm · d dismiss</span>
          </div>
          {diff.data?.coverage_note ? <p className="px-3 py-2 text-xs text-muted-foreground">{diff.data.coverage_note}</p> : null}
          {diff.isLoading ? <p className="p-4 text-sm text-muted-foreground">Loading diff…</p> : null}
          {diff.data ? (
            <DiffView
              rows={diff.data.split}
              findings={findings.filter((finding) => finding.file_path === diff.data?.path)}
              selectedId={selected?.id ?? null}
              mode={mode}
              onSelect={(findingId) => {
                const finding = data.findings.find((item) => item.id === findingId)
                setQuery({ finding: findingId, file: finding?.file_path ?? null })
              }}
            />
          ) : null}
        </section>
        <aside className="min-h-0 overflow-auto border-t xl:border-t-0 xl:border-l">
          {selected ? (
            <FindingPanel finding={selected} onConfirm={() => decide("confirm")} onDismiss={() => decide("dismiss")} onReanalyze={reanalyze} reviewId={id} />
          ) : (
            <p className="p-4 text-sm text-muted-foreground">
              {data.job.status === "completed" ? "No findings match these filters." : "Findings appear as the review records them."}
            </p>
          )}
          {data.report ? (
            <div className="border-t p-4">
              <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Stored report</h2>
              <p className="mt-1 text-xs text-muted-foreground">{data.report.delivery_status}. Redacted: {String(data.report.redacted)}.</p>
              <pre className="mono mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed">{data.report.body}</pre>
            </div>
          ) : null}
          <Coverage coverage={data.job.coverage} />
        </aside>
      </div>
      <EditorBridge data={data} reviewId={id} />
    </div>
  )
}

function FindingPanel({
  finding,
  onConfirm,
  onDismiss,
  onReanalyze,
  reviewId,
}: {
  finding: Finding
  onConfirm: () => void
  onDismiss: () => void
  onReanalyze: () => void
  reviewId: string
}) {
  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-wider">
        <span className={`sev-${finding.severity}`}>{finding.severity}</span>
        <span>{finding.category}</span>
        <span className="text-muted-foreground">{finding.claim_level}</span>
        <span className="text-muted-foreground">{Math.round(finding.confidence * 100)}%</span>
      </div>
      <h2 className="text-sm font-medium">{finding.title}</h2>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {finding.file_path}:{finding.start_line}
        {finding.enclosing_symbol ? ` · ${finding.enclosing_symbol}` : ""} · {finding.status} · validation {finding.validation_state}
      </p>
      <section>
        <h3 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Observed evidence</h3>
        <ul className="mt-2 space-y-2">
          {finding.evidence.map((item) => (
            <li key={item.id} className="rounded-md border p-2 text-xs">
              <div className="text-muted-foreground">{item.kind} · trusted {String(item.trusted)}</div>
              {item.snippet ? <pre className="mono mt-1 whitespace-pre-wrap">{item.snippet}</pre> : null}
              <p className="mt-1 leading-relaxed">{item.detail}</p>
            </li>
          ))}
        </ul>
      </section>
      <p className="text-sm leading-relaxed">{finding.explanation}</p>
      <p className="text-xs leading-relaxed text-muted-foreground">{finding.impact}</p>
      {finding.uncertain_reason ? <p className="text-xs text-muted-foreground">Uncertainty: {finding.uncertain_reason}</p> : null}
      <section className="rounded-md border border-dashed p-2">
        <h3 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Agent commentary</h3>
        <p className="mt-1 text-xs leading-relaxed">{finding.agent_note || "No agent note."}</p>
        <p className="mt-1 text-[10px] text-muted-foreground">Labeled separately from observed evidence. Mock provider does not make a model call.</p>
      </section>
      <p className="text-xs leading-relaxed"><span className="text-muted-foreground">Remediation. </span>{finding.remediation}</p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onConfirm} disabled={finding.status === "confirmed"}>Confirm</Button>
        <Button size="sm" variant="outline" onClick={onDismiss} disabled={finding.status === "dismissed"}>Dismiss</Button>
        <Button size="sm" variant="outline" onClick={onReanalyze}>Reanalyze</Button>
        <Link className={buttonVariants({ size: "sm", variant: "ghost" })} href={`/reviews/${reviewId}/validation`}>Open validation</Link>
      </div>
    </div>
  )
}

function Filter({ label, value, options, onChange }: { label: string; value: string | null; options: string[]; onChange: (value: string | null) => void }) {
  return (
    <select className="rounded-md border bg-card px-2 py-1" value={value ?? ""} onChange={(event) => onChange(event.target.value || null)}>
      <option value="">{label}</option>
      {options.map((option) => (
        <option key={option} value={option}>{titleCase(option)}</option>
      ))}
    </select>
  )
}

function Coverage({ coverage }: { coverage: ReviewDetail["job"]["coverage"] }) {
  if (!coverage?.skipped?.length && !coverage?.scanner_notes?.length) return null
  return (
    <div className="border-t p-4 text-xs">
      <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Coverage</h2>
      <ul className="mt-2 space-y-1 text-muted-foreground">
        {coverage.skipped?.map((item) => (
          <li key={item.path}><span className="mono text-foreground">{item.path}</span> — {item.reason}</li>
        ))}
        {coverage.scanner_notes?.map((note) => <li key={note}>{note}</li>)}
      </ul>
    </div>
  )
}

function EditorBridge({ data, reviewId }: { data: ReviewDetail; reviewId: string }) {
  const patch = data.patches.find((item) => item.strategy === "parameterize_sql") ?? data.patches[0]
  if (!patch) return null
  const path = patch.target_paths[0]
  const content = patch.patched_files[path] ?? ""
  return <PatchEditor key={`${patch.id}:${content}`} patchId={patch.id} title={patch.title} initial={content} reviewId={reviewId} status={patch.status} proof={patch.proof_scope} />
}

function PatchEditor({
  patchId,
  title,
  initial,
  reviewId,
  status,
  proof,
}: {
  patchId: string
  title: string
  initial: string
  reviewId: string
  status: string
  proof: string | null
}) {
  const [value, setValue] = useState(initial)
  const client = useQueryClient()
  return (
    <details className="border-t">
      <summary className="cursor-pointer px-4 py-2 text-xs text-muted-foreground">
        Edit proposed file · {title} · {status} · {proof ?? "no proof scope"}
      </summary>
      <div className="space-y-2 px-4 py-3">
        <p className="text-xs text-muted-foreground">Saving stores the edit and marks the patch proposed. The repository is not modified and validation is not re-run automatically.</p>
        <div className="overflow-hidden rounded-md border">
          <MonacoEditor
            height="260px"
            language="python"
            theme="vs-dark"
            value={value}
            onChange={(next) => setValue(next ?? "")}
            options={{ minimap: { enabled: false }, fontSize: 12, fontFamily: "IBM Plex Mono, ui-monospace, monospace", scrollBeyondLastLine: false }}
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={async () => {
            try {
              await api(`/patches/${patchId}`, { method: "PATCH", body: JSON.stringify({ content: value }) })
              toast.success("Patch edit stored. Validation was not re-run.")
              client.invalidateQueries({ queryKey: ["review", reviewId] })
            } catch (error) {
              toast.error((error as Error).message)
            }
          }}
        >
          Save edit
        </Button>
      </div>
    </details>
  )
}
