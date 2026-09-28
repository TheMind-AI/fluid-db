import path from "node:path"
import { mkdir } from "node:fs/promises"

const root = path.resolve(import.meta.dir, "..")
const destination = path.join(root, "dist", "tarballs")
await mkdir(destination, { recursive: true })
const proc = Bun.spawn(["npm", "pack", "--ignore-scripts", "--pack-destination", destination], {
  cwd: path.join(root, "dist", "fluiddb"),
  stdout: "inherit",
  stderr: "inherit",
})
if (await proc.exited) throw new Error("Packing FluidDB failed")
