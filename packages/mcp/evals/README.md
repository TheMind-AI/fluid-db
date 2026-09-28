# Read-only MCP evaluation

`fixture.ts` seeds thirteen invented explicit memories, including three corrections. `questions.xml` contains ten
independent read-only questions requiring source/history inspection, temporal reasoning or arithmetic. The test
in `test/evaluation.test.ts` verifies all expected values using actual MCP inspection/evidence responses and paging.

This is a protocol-grounded answer-key check. No answering LLM was run, so it is not an agent success-rate claim.
Expose `FluidMcp.create({ memory: await fixture() })` through a test-only transport to run the questions with your
chosen model. Do not substitute real private conversations for this distributable fixture.
