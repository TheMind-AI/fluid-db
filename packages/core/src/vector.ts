// Vectors are unit length, so cosine is the dot product.
export function dot(a: Float32Array, b: Float32Array) {
  if (a.length !== b.length) throw new RangeError("vector dimensions differ; re-embed with one model and dimension")
  const size = a.length
  const acc = { sum: 0 }
  for (let i = 0; i < size; i++) acc.sum += (a[i] as number) * (b[i] as number)
  return acc.sum
}

export function unit(vector: Float32Array) {
  const norm = Math.sqrt(dot(vector, vector)) || 1
  return vector.map((x) => x / norm)
}

export * as Vector from "./vector"
