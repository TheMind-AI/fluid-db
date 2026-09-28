// Map with at most `size` calls in flight, keeping the order of the input.
export async function map<T, R>(items: T[], size: number, fn: (item: T, index: number) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length)
  const next = { index: 0 }
  const worker = async () => {
    while (next.index < items.length) {
      const index = next.index++
      out[index] = await fn(items[index] as T, index)
    }
  }
  await Promise.all(Array.from({ length: Math.min(Math.max(1, size), items.length) }, worker))
  return out
}

export function chunks<T>(items: T[], size: number): T[][] {
  return Array.from({ length: Math.ceil(items.length / size) }, (_, i) => items.slice(i * size, (i + 1) * size))
}

export * as Limit from "./limit"
