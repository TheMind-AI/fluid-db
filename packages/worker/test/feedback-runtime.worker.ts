import { Feedback } from "@fluiddb/feedback"

// Workerd verification only: the harness intercepts HiveNet, so this never reports live.
export default {
  async fetch() {
    const receipt = await Feedback.create({ clientName: "fluiddb-runtime-test" }).submit({
      feedback: "Synthetic runtime wiring check",
      category: "api",
      resume: "runtime-thread",
      idempotencyKey: "runtime-retry",
      eval: { task: "Send a synthetic report", expected: "Owner guidance", actual: "Testing the local harness" },
    })
    return Response.json(receipt)
  },
}
