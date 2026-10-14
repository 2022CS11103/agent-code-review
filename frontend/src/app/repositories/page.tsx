"use client"

import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Suspense, useState } from "react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Notice, PageFrame } from "@/components/page-frame"
import { api } from "@/lib/api"
import type { GithubConnect, RepositorySummary } from "@/lib/types"

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
      ? "GitHub App saved on this server. Install it on a repository, then sync."
      : githubStatus === "failed"
        ? "GitHub did not finish connecting. No credential was stored."
        : null
  const queryClient = useQueryClient()
  const [syncBanner, setSyncBanner] = useState<string | null>(null)
  const banner = syncBanner ?? returned
  const repos = useQuery({
    queryKey: ["repositories"],
    queryFn: () => api<{ items: RepositorySummary[] }>("/repositories"),
  })
  const connect = useQuery({
    queryKey: ["github-connect"],
    queryFn: () => {
      const origin = typeof window === "undefined" ? "http://127.0.0.1:43123" : window.location.origin
      return api<GithubConnect>(`/github/connect?browser_origin=${encodeURIComponent(origin)}`)
    },
  })
  const sync = useMutation({
    mutationFn: () =>
      api<{ installations: number; repositories: number; pulls_queued: number }>("/github/sync", { method: "POST" }),
    onSuccess: async (result) => {
      setSyncBanner(
        `Synced ${result.repositories} repositories and queued ${result.pulls_queued} pull requests from ${result.installations} installations.`,
      )
      await queryClient.invalidateQueries({ queryKey: ["repositories"] })
    },
    onError: (error: Error) => setSyncBanner(error.message),
  })

  if (repos.isLoading) return <Notice title="Loading repositories" />
  if (repos.isError) return <Notice title="Repositories could not be loaded" detail={(repos.error as Error).message} />
  const items = repos.data?.items ?? []
  const github = connect.data

  return (
    <PageFrame
      eyebrow="Policy studio"
      title="Repositories"
      note="acme/billing-service and acme/auth-gateway are local demos. Your GitHub repositories are added from the box below."
    >
      <section className="border-b px-4 py-4">
        <h2 className="text-sm font-medium">Add a repository from GitHub</h2>
        <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
          {github?.note ??
            "Add GitHub repository opens GitHub so you can create the PRISM app, install it on your repos, then sync open pull requests."}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {github && !github.app_configured ? (
            <form action={github.form_action} method="post">
              <input type="hidden" name="manifest" value={github.manifest} />
              <button type="submit" className={buttonVariants()}>
                Add GitHub repository
              </button>
            </form>
          ) : null}
          {connect.isLoading ? <span className="text-xs text-muted-foreground">Loading the GitHub button…</span> : null}
          {github?.install_url ? (
            <a className={buttonVariants()} href={github.install_url} target="_blank" rel="noreferrer">
              Install on a repository
            </a>
          ) : null}
          <Button type="button" variant="outline" disabled={!github?.app_configured || sync.isPending} onClick={() => sync.mutate()}>
            {sync.isPending ? "Syncing" : "Sync from GitHub"}
          </Button>
        </div>
      </section>
      {banner ? <p className="border-b px-4 py-3 text-sm">{banner}</p> : null}
      {connect.isError ? (
        <p className="border-b px-4 py-3 text-sm text-destructive">
          The GitHub button did not load. Pull the latest code, then restart the API window and this site. {(connect.error as Error).message}
        </p>
      ) : null}
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
