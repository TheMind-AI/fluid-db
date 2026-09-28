// Runs the functions it is given one at a time, in the order given; a failure doesn't stop the ones after it.
export function serial() {
  const state = { tail: Promise.resolve() as Promise<unknown> }
  return <T>(fn: () => Promise<T>): Promise<T> => {
    const run = state.tail.then(fn, fn)
    state.tail = run.catch(() => {})
    return run
  }
}

export * as Lock from "./lock"
