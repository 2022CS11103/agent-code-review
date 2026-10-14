"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { PageFrame, Notice } from "@/components/page-frame"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { Patch, ReviewDetail, TestCase } from "@/lib/types"

const ACCEPTABLE = new Set(["passed", "needs_review"])

export default function ValidationPage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const client = useQueryClient()
  const review = useQuery({
    queryKey: ["review", id],
    queryFn: () => api<ReviewDetail>(`/reviews/${id}`),
  })

  async function decide(patchId: string, action: "accept" | "reject") {
    try {
      const result = await api<{ message: string; repository_modified: boolean }>(`/patches/${patchId}/decision`, {
        method: "POST",
        body: JSON.stringify({ action }),
      })
      toast.success(result.message)
      client.invalidateQueries({ queryKey: ["review", id] })
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  if (review.isLoading) return <Notice title="Loading validation runs" />
  if (review.isError || !review.data) {
    return <Notice title="Validation could not be loaded" detail={(review.error as Error)?.message} />
  }
  const data = review.data

  return (
    <PageFrame
      eyebrow={`${data.repository.name} #${data.pull_request.number}`}
      title="Patch validation"
      note="A passing test is not proof that a patch is production-safe. Test, lint, and security results are reported separately. Confirm and dismiss on the review page do not wait for these sandbox attempts. Accepting a patch records a decision and does not modify the repository."
      actions={
        <Link href={`/reviews/${id}`} className="text-xs underline">
          Back to review
        </Link>
      }
    >
      {data.patches.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">
          {data.job.status === "completed"
            ? "This review did not produce a patch. Declined findings stay as evidence without a mechanical rewrite."
            : "Patches appear after the remediation stage."}
        </p>
      ) : (
        <div className="divide-y">
          {data.patches.map((patch) => (
            <PatchBlock key={patch.id} patch={patch} onDecide={decide} />
          ))}
        </div>
      )}
    </PageFrame>
  )
}

function PatchBlock({ patch, onDecide }: { patch: Patch; onDecide: (id: string, action: "accept" | "reject") => void }) {
  const latest = patch.validations[patch.validations.length - 1]
  const canAccept = latest != null && ACCEPTABLE.has(latest.status) && patch.status !== "accepted"
  return (
    <article className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="border-b px-4 py-4 xl:border-r xl:border-b-0">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-medium">{patch.title}</h2>
          <span className="text-xs text-muted-foreground">{patch.status}</span>
          <span className="text-xs text-muted-foreground">
            {patch.attempt} {patch.attempt === 1 ? "attempt" : "attempts"}
          </span>
          <span className="mono text-xs text-muted-foreground">{patch.strategy}</span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Proposed file only. Not applied to the repository. Proof scope: {patch.proof_scope ?? "unspecified"}.
        </p>
        {patch.policy_notes ? <p className="mt-2 text-xs leading-relaxed">{patch.policy_notes}</p> : null}
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{patch.disclaimer}</p>
        <pre className="mono mt-3 max-h-80 overflow-auto rounded-md border p-3 text-[11px] leading-5">
          {patch.diff_text.split("\n").map((line, index) => (
            <div key={index} className={line.startsWith("+") && !line.startsWith("+++") ? "diff-add" : line.startsWith("-") && !line.startsWith("---") ? "diff-del" : ""}>
              {line || " "}
            </div>
          ))}
        </pre>
        <div className="mt-3 flex gap-2">
          <Button size="sm" disabled={!canAccept} onClick={() => onDecide(patch.id, "accept")}>
            Accept decision
          </Button>
          <Button size="sm" variant="outline" disabled={patch.status === "rejected"} onClick={() => onDecide(patch.id, "reject")}>
            Reject
          </Button>
        </div>
        {!canAccept && patch.status !== "accepted" ? (
          <p className="mt-2 text-[11px] text-muted-foreground">
            Accept stays disabled until the latest validation is passed or needs review.
          </p>
        ) : null}
      </div>
      <div className="px-4 py-4">
        <h3 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Attempts</h3>
        {patch.validations.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">
            No validation run recorded. Finding confirm and dismiss stay available on the review page.
          </p>
        ) : null}
        <ol className="mt-2 space-y-3">
          {patch.validations.map((run) => (
            <li key={run.id} className="rounded-md border p-3 text-xs">
              <div className="flex flex-wrap gap-x-3">
                <span>attempt {run.attempt}</span>
                <span className="font-medium">{run.status}</span>
                <span className="text-muted-foreground">{run.duration_ms} ms</span>
              </div>
              <p className="mt-1 text-muted-foreground">
                tests {run.tests_passed} passed / {run.tests_failed} failed · baseline {run.baseline_passed} passed / {run.baseline_failed} failed
              </p>
              <p className="text-muted-foreground">
                lint {run.lint_status} · security {run.security_status}
                {run.timed_out ? " · timed out" : ""}
                {run.exit_code != null ? ` · exit ${run.exit_code}` : ""}
              </p>
              {run.notes ? <p className="mt-1 leading-relaxed">{run.notes}</p> : null}
              <TestList tests={run.tests} />
              {run.stderr ? <Log label="stderr" text={run.stderr} /> : null}
              {run.stdout ? <Log label="stdout" text={run.stdout} /> : null}
            </li>
          ))}
        </ol>
      </div>
    </article>
  )
}

function TestList({ tests }: { tests: TestCase[] }) {
  if (!tests.length) return null
  return (
    <ul className="mt-2 space-y-1">
      {tests.map((test) => (
        <li key={`${test.phase}-${test.name}`}>
          <span className="mono">{test.phase}</span> · {test.outcome} · {test.name}
          {test.output ? <pre className="mono mt-1 whitespace-pre-wrap text-[11px] text-muted-foreground">{test.output}</pre> : null}
        </li>
      ))}
    </ul>
  )
}

function Log({ label, text }: { label: string; text: string }) {
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-muted-foreground">{label}</summary>
      <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[11px]">{text}</pre>
    </details>
  )
}
