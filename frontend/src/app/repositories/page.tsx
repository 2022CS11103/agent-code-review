"use client"

import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Suspense } from "react"
import { GithubAccount } from "@/components/github-account"
import { Notice, PageFrame } from "@/components/page-frame"
import { api } from "@/lib/api"
import type { RepositorySummary } from "@/lib/types"

export default function RepositoriesRoute() {
  return (
    <Suspense fallback={<Notice title="Loading repositories" />}>
      <RepositoriesPage />
    </Suspense>
  )
}

function RepositoriesPage() {
  const params = useSearchParams()
  const githubStatus = params.get("github")
  const returned =
    githubStatus === "connected"
      ? "GitHub account saved on this server. Choose repositories, then sync."
      : githubStatus === "failed"
        ? "GitHub did not finish connecting the account. No credential was stored."
        : null
  const repos = useQuery({
    queryKey: ["repositories"],
    queryFn: () => api<{ items: RepositorySummary[] }>("/repositories"),
  })

  if (repos.isLoading) return <Notice title="Loading repositories" />
  if (repos.isError) return <Notice title="Repositories could not be loaded" detail={(repos.error as Error).message} />
  const items = repos.data?.items ?? []

  return (
    <PageFrame
      eyebrow="Workspace"
      title="Repositories"
      note="Demo fixtures stay in this list. Repositories from a GitHub account appear after you connect that account and sync."
    >
      <GithubAccount />
      {returned ? <p className="border-b px-4 py-3 text-sm">{returned}</p> : null}
      {items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No repositories are connected to this workspace.</p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Repository</th>
              <th className="px-2 py-2 font-medium">Connection</th>
              <th className="px-2 py-2 font-medium">Trust</th>
              <th className="px-2 py-2 font-medium">Threshold</th>
            </tr>
          </thead>
          <tbody>
            {items.map((repo) => (
              <tr key={repo.id} className="border-t">
                <td className="px-4 py-3">
                  <Link href={`/repositories/${repo.id}`} className="font-medium hover:underline">
                    {repo.owner}/{repo.name}
                  </Link>
                  <div className="text-xs text-muted-foreground">{repo.primary_language}</div>
                </td>
                <td className="px-2 py-3 text-xs">{repo.connection_status}</td>
                <td className="px-2 py-3 text-xs">{repo.trust_tier}</td>
                <td className="px-2 py-3 text-xs">{repo.policy?.severity_threshold ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </PageFrame>
  )
}
