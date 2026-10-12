export class ApiError extends Error {
  constructor(message: string) {
    super(message)
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has("content-type")) headers.set("content-type", "application/json")
  const response = await fetch(`/api/v1${path}`, { ...init, headers, cache: "no-store" })
  const text = await response.text()
  const data = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new ApiError(data?.error?.message || `Request failed (${response.status})`)
  }
  return data as T
}

export function subscribe(path: string, onEvent: (data: unknown) => void) {
  const source = new EventSource(`/api/v1${path}`)
  source.onmessage = (event) => {
    try {
      onEvent(JSON.parse(event.data))
    } catch {
      /* ignore malformed frames */
    }
  }
  source.addEventListener("end", () => source.close())
  return () => source.close()
}
