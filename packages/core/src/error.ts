// Errors carry a stable `name`, so callers can branch on it after serialization.

export class ConflictError extends Error {
  override readonly name = "ConflictError"
}

export class InputError extends Error {
  override readonly name = "InputError"
}

export class ProcessorError extends Error {
  override readonly name = "ProcessorError"
  constructor(
    readonly provider: string,
    readonly processor: string,
    readonly allowed: string[],
  ) {
    super(`${provider} sends data to ${processor}, which is not among the allowed processors: ${allowed.join(", ")}`)
  }
}

export class ProviderError extends Error {
  override readonly name = "ProviderError"
  constructor(
    readonly provider: string,
    readonly status: number,
    detail: string,
  ) {
    super(`${provider} failed with ${status}: ${detail}`)
  }
}

export class OutputError extends Error {
  override readonly name = "OutputError"
  constructor(
    readonly provider: string,
    detail: string,
  ) {
    super(`${provider} returned output that does not match the schema: ${detail}`)
  }
}
