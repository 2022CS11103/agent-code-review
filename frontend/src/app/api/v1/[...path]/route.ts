const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8741"

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params
  const incoming = new URL(request.url)
  const target = `${BACKEND}/api/v1/${path.join("/")}${incoming.search}`
  const headers = new Headers()
  const contentType = request.headers.get("content-type")
  if (contentType) headers.set("content-type", contentType)
  const lastEvent = request.headers.get("last-event-id")
  if (lastEvent) headers.set("last-event-id", lastEvent)
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    cache: "no-store",
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body
    init.duplex = "half"
  }
  const response = await fetch(target, init)
  const outgoing = new Headers()
  const responseType = response.headers.get("content-type")
  if (responseType) outgoing.set("content-type", responseType)
  const location = response.headers.get("location")
  if (location) outgoing.set("location", location)
  outgoing.set("cache-control", "no-cache, no-transform")
  outgoing.set("x-accel-buffering", "no")
  return new Response(response.body, { status: response.status, headers: outgoing })
}

export const GET = proxy
export const POST = proxy
export const PATCH = proxy
export const PUT = proxy
export const DELETE = proxy
