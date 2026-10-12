"use client"

import { useQuery } from "@tanstack/react-query"
import { Notice, PageFrame } from "@/components/page-frame"
import { api } from "@/lib/api"
import type { Me, SettingsView } from "@/lib/types"

export default function SettingsPage() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsView>("/settings") })
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") })
  if (settings.isLoading || me.isLoading) return <Notice title="Loading settings" />
  if (settings.isError || !settings.data) return <Notice title="Settings could not be loaded" detail={(settings.error as Error)?.message} />
  const data = settings.data
  const user = me.data

  return (
    <PageFrame
      eyebrow={data.workspace.name}
      title="Settings"
      note="Values come from the running process. A missing credential is reported as not configured."
    >
      <div className="grid gap-0 md:grid-cols-2">
        <Section title="Session">
          {me.isError ? <p className="text-sm text-destructive">{(me.error as Error).message}</p> : null}
          {user ? (
            <List
              rows={[
                ["Name", user.user.name],
                ["Email", user.user.email],
                ["Role", user.role],
                ["Workspace", user.workspace.name],
                ["Demo workspace", String(user.workspace.demo)],
              ]}
            />
          ) : null}
        </Section>
        <Section title="Model">
          <List
            rows={[
              ["Active provider", data.llm.active_provider],
              ["Model", data.llm.model],
              ["Gemini key configured", String(data.llm.gemini_configured)],
              ["Groq key configured", String(data.llm.groq_configured)],
              ["OpenAI key configured", String(data.llm.openai_configured)],
              ["Timeout", `${data.llm.timeout_seconds}s`],
              ["Call budget", String(data.llm.max_calls)],
              ["Input budget", `${data.llm.max_input_chars} characters`],
              ["Analyzer", data.analyzer_version],
              ["Prompt version", data.prompt_version],
              ["Repair cap", String(data.repair_cap)],
            ]}
          />
          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{data.llm.note}</p>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{data.llm.workspace_isolation}</p>
        </Section>
        <Section title="Sandbox">
          <List
            rows={[
              ["Driver", data.sandbox.driver],
              ["Image", data.sandbox.image],
            ]}
          />
          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{data.sandbox.limitations}</p>
        </Section>
        <Section title="GitHub and runtime">
          <List
            rows={[
              ["API base", data.github.api_base],
              ["App configured", String(data.github.app_configured)],
              ["App id configured", String(data.github.app_id_configured)],
              ["Private key configured", String(data.github.private_key_configured)],
              ["Install URL", data.github.install_url ?? "Set PRISM_GITHUB_APP_SLUG"],
              ["Permissions", data.github.permissions.join(", ")],
              ["Webhook secret is the dev default", String(data.github.webhook_secret_is_default)],
              ["Database", data.database_dialect],
              ["Worker", data.worker_mode],
              ["Demo mode", String(data.demo_mode)],
            ]}
          />
          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{data.github.note}</p>
        </Section>
      </div>
    </PageFrame>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b px-4 py-4 md:border-r">
      <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function List({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="space-y-2 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-3">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="mono text-xs">{value}</dd>
        </div>
      ))}
    </dl>
  )
}
