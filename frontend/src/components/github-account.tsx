"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Button, buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { GithubConnect } from "@/lib/types"

export function GithubAccount() {
  const queryClient = useQueryClient()
  const [banner, setBanner] = useState<string | null>(null)
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
      setBanner(
        `Synced ${result.repositories} repositories and queued ${result.pulls_queued} pull requests from ${result.installations} installations.`,
      )
      await queryClient.invalidateQueries({ queryKey: ["repositories"] })
    },
    onError: (error: Error) => setBanner(error.message),
  })
  const github = connect.data

  return (
    <section className="border-b px-4 py-4">
      <h2 className="text-sm font-medium">Add GitHub account</h2>
      <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
        {github?.note ??
          "This connects your GitHub account by creating the PRISM app. You then choose which repositories that account may share."}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {github && !github.app_configured ? (
          <form action={github.form_action} method="post">
            <input type="hidden" name="manifest" value={github.manifest} />
            <button type="submit" className={buttonVariants()}>
              Add GitHub account
            </button>
          </form>
        ) : null}
        {connect.isLoading ? <span className="text-xs text-muted-foreground">Loading GitHub account…</span> : null}
        {github?.app_configured ? (
          <span className="text-xs text-muted-foreground">GitHub account connected ({github.credential_source}).</span>
        ) : null}
        {github?.install_url ? (
          <a className={buttonVariants()} href={github.install_url} target="_blank" rel="noreferrer">
            Choose repositories
          </a>
        ) : null}
        <Button type="button" variant="outline" disabled={!github?.app_configured || sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? "Syncing" : "Sync repositories"}
        </Button>
      </div>
      {banner ? <p className="mt-3 text-sm">{banner}</p> : null}
      {connect.isError ? (
        <p className="mt-3 text-sm text-destructive">
          GitHub account controls did not load. Restart the API, then reload this page. {(connect.error as Error).message}
        </p>
      ) : null}
    </section>
  )
}
