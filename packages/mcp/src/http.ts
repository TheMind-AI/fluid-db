import { createMcpHandler, type AuthInfo } from "@modelcontextprotocol/server"
import { FluidMcp, type Options } from "./server"

export interface HttpOptions {
  /** Authenticate each request and resolve its person and capabilities. Return null for an invalid credential. */
  authorize(request: Request): Promise<Options | null>
  /** Exact URL hosts (including ports). The proxy must preserve the public request URL. */
  allowedHosts: readonly string[]
  /** Browser origins allowed to call this endpoint. Requests without Origin are allowed. */
  allowedOrigins?: readonly string[]
  /** OAuth protected-resource metadata URL, if the product offers OAuth discovery. */
  resourceMetadataUrl?: string
}

export function createHttpHandler(options: HttpOptions) {
  if (!options.allowedHosts.length) throw new Error("Configure allowedHosts for the MCP endpoint")
  if (options.resourceMetadataUrl) {
    const url = new URL(options.resourceMetadataUrl)
    if (url.protocol !== "https:" || /["\r\n]/.test(options.resourceMetadataUrl))
      throw new Error("Resource metadata must be an HTTPS URL")
  }
  const bindings = new WeakMap<AuthInfo, Options>()
  const handler = createMcpHandler(
    ({ authInfo }) => {
      const binding = authInfo && bindings.get(authInfo)
      if (!binding) throw new Error("Missing authorized memory binding")
      return FluidMcp.create(binding)
    },
    { legacy: "stateless", maxRequestBodySize: 2_000_000 },
  )
  return {
    close: () => handler.close(),
    fetch: async (request: Request): Promise<Response> => {
      const origin = request.headers.get("origin")
      if (
        !options.allowedHosts.includes(new URL(request.url).host) ||
        (origin !== null && !(options.allowedOrigins ?? []).includes(origin))
      )
        return new Response("Forbidden", { status: 403 })
      let binding: Options | null
      try {
        binding = await options.authorize(request)
      } catch {
        return new Response("Authentication unavailable", { status: 503 })
      }
      if (!binding)
        return new Response("Unauthorized", {
          status: 401,
          headers: {
            "www-authenticate": `Bearer${options.resourceMetadataUrl ? ` resource_metadata="${options.resourceMetadataUrl}"` : ""}`,
          },
        })
      const authInfo: AuthInfo = { token: "", clientId: "fluiddb-host", scopes: [] }
      bindings.set(authInfo, binding)
      try {
        return await handler.fetch(request, { authInfo })
      } finally {
        bindings.delete(authInfo)
      }
    },
  }
}
