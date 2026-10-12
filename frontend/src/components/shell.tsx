"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command"
import { api } from "@/lib/api"
import type { CommandCenter } from "@/lib/types"
import { cn } from "@/lib/utils"

const NAV = [
  { href: "/", label: "Command" },
  { href: "/repositories", label: "Repositories" },
  { href: "/evaluations", label: "Evaluation" },
  { href: "/audit", label: "Audit" },
  { href: "/settings", label: "Settings" },
]

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [queue, setQueue] = useState<CommandCenter["queue"]>([])

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        setOpen(true)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  useEffect(() => {
    if (!open) return
    api<CommandCenter>("/command-center")
      .then((data) => setQueue(data.queue))
      .catch(() => setQueue([]))
  }, [open])

  function toggleTheme() {
    const next = document.documentElement.classList.contains("dark") ? "light" : "dark"
    document.documentElement.classList.toggle("dark", next === "dark")
    window.localStorage.setItem("prism-theme", next)
  }

  return (
    <div className="flex h-dvh min-h-0 bg-background text-foreground">
      <aside className="hidden h-full w-56 shrink-0 flex-col overflow-y-auto border-r border-border md:flex">
        <Link href="/" className="flex items-center gap-2 px-4 py-4">
          <Mark />
          <div>
            <div className="text-sm font-medium tracking-tight">PRISM</div>
            <div className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Review system</div>
          </div>
        </Link>
        <nav className="flex flex-1 flex-col gap-0.5 px-2" aria-label="Primary">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground",
                  active && "bg-muted text-foreground",
                )}
              >
                {item.label}
              </Link>
            )
          })}
        </nav>
        <div className="border-t px-4 py-3 text-[11px] leading-relaxed text-muted-foreground">
          Demo fixtures only. Human approval required. Tests are not proof of safety.
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center gap-3 border-b px-3 md:px-4">
          <nav className="flex gap-2 overflow-x-auto md:hidden" aria-label="Primary mobile">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className="shrink-0 text-xs text-muted-foreground">
                {item.label}
              </Link>
            ))}
          </nav>
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="ml-auto flex h-8 min-w-44 items-center justify-between rounded-md border bg-card px-2 text-xs text-muted-foreground"
          >
            Jump to…
            <kbd className="mono text-[10px]">⌘K</kbd>
          </button>
          <button type="button" onClick={toggleTheme} className="h-8 rounded-md border px-2 text-xs">
            <span className="hidden dark:inline">Light</span>
            <span className="dark:hidden">Dark</span>
          </button>
        </header>
        <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
      </div>
      <CommandDialog open={open} onOpenChange={setOpen}>
        <Command>
          <CommandInput placeholder="Jump to a review, repository, or action" />
          <CommandList>
            <CommandEmpty>No matches.</CommandEmpty>
            <CommandGroup heading="Navigate">
              {NAV.map((item) => (
                <CommandItem
                  key={item.href}
                  onSelect={() => {
                    setOpen(false)
                    router.push(item.href)
                  }}
                >
                  {item.label}
                </CommandItem>
              ))}
              <CommandItem
                onSelect={() => {
                  toggleTheme()
                  setOpen(false)
                }}
              >
                Toggle theme
                <CommandShortcut>theme</CommandShortcut>
              </CommandItem>
            </CommandGroup>
            <CommandGroup heading="Pull requests">
              {queue.map((item) => (
                <CommandItem
                  key={item.pull_request_id}
                  onSelect={() => {
                    setOpen(false)
                    if (item.job) router.push(`/reviews/${item.job.id}`)
                    else router.push(`/pulls/${item.pull_request_id}`)
                  }}
                >
                  {item.repository.name} #{item.number} {item.title}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </CommandDialog>
    </div>
  )
}

function Mark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" className="text-primary">
      <path d="M12 2.5 21 20.5H3L12 2.5Z" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M7.2 15.5h9.6" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}
