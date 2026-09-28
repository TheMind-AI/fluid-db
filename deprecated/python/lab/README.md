# FluidDB lab (2026)

Experiments that revisit FluidDB, the self-organizing database from 2023-24,
with 2026 models and Jev (TypeSafe's System One decision model).
Findings are in [REPORT.md](REPORT.md). Design and research decisions (per subsystem and per round, plus the agenda)
are LLP documents in [`llp/`](../../../llp/0000-fluiddb.explainer.md).

## Layout

```
lab/
  common/llm.py        one client for OpenAI, Anthropic and OpenRouter models; disk cache + cost ledger
  common/jev.py        Jev through OpenRouter's /api/v1/systemone endpoint (choice / score / noul)
  common/judge.py      Claude Opus 5.5 grades answers against gold (correct / partial / incorrect)
  systems/legacy2024.py  the 2024 SQL pipeline, prompts copied verbatim; only the model changes
  systems/engine.py    FluidDB v2 storage engine: raw log, catalog with descriptions, typed ops -> SQLite,
                       row history, forget (hard delete + log redaction), readable view (links shown as names)
  systems/fluid_v2.py  FluidDB v2 planner + readers, variants v2 / v2jev / v21 / v21jev / v22 / v23
  systems/jev_layer.py the Jev decisions: write gate, source (user vs document) guard, rows to show the planner / answer from
  systems/jev_query.py LLM-free querying: an LLM compiles SQL templates once, Jev picks template + parameters per question
  systems/reader.py    agentic reader: sql / lookup / search_log tools, one step at a time
  systems/consolidate.py  offline "sleep": replay dropped messages, move misplaced rows, merge duplicates
  systems/mem0_baseline.py  Mem0 OSS (mem0ai 2.x) wired for replaying dated conversations, as a baseline
  systems/det_reader.py  reads with no LLM: a semantic layer profiled from the DB, Jev fills a closed query form,
                       SQL + code answer, Jev verifies (falls back to an LLM reader when unsure)
  systems/supersede.py async write pass: Jev marks rows a newer row replaced (valid_to), so "current" is a query
  systems/typing.py    async write pass: an LLM compiles regex extractors once; code fills typed columns forever
  systems/compiled_writer.py  tier A of the writer: the LLM compiles regex patterns from the planner's own rows
                       (test-driven, with feedback); code then writes matching messages in ~1 ms, no model
  systems/query_log.py the query log (`_queries`): each question's plan, verifier score and label-free signals
  systems/schema_advisor.py  evolves the schema from the query log: the LLM proposes migrations, code tests each on a
                       scratch copy (data checks, Jev spot-check, replay of the questions it serves) and applies it
  systems/derived.py   derived columns (rules, association, Jev; backfilled from _log, derived again on every
                       insert) and value canonicalization
  systems/compiled_reader.py  compiled questions: recurring question shapes -> regex templates that run as SQL + code
  systems/confidence.py per-row confidence (code checks + three generic Jev questions) and one-click fix suggestions
  common/grade.py      cheap judges: Jev as judge (96.7% agreement with Opus) and a rule-based grader
  datasets/            alex_rivera (the 2024 eval), life_stream (new, harder), life_stream_cs (Czech), components
                       (labelled decisions), life_year (a simulated year: 817 messages, 90 questions; simulate_year.py),
                       life_scale (5,050 messages over 2.5 years with per-message truth, 72 questions; simulate_scale.py),
                       workload (80 logged + 105 future questions over life_scale, gold from the truth; workload.py),
                       external/ (LoCoMo, LongMemEval_S: downloaded, git-ignored)
  run_e2e.py           ingest a dataset into a system, answer its QA, judge, write lab/runs/<run>/result.json
  bench/components.py  Jev vs LLMs on single decisions (gate, entity resolution, retrieval, answerability)
  bench/scale.py       retrieval with 1k / 5k distractor memories (listwise.py: one Jev Choice per question)
  bench/structure.py   is the generated database correct? fact audit + broken links / types / duplicates
  bench/query_modes.py every way to query a database, timed one question at a time (cache off)
  bench/jev_queries.py Jev-routed compiled SQL templates: coverage, correctness, latency
  bench/injection.py   prompt injection through emails / receipts / invites, with and without the Jev guard
  bench/schema_critic.py  Jev ranking table pairs that should be merged
  bench/baselines.py   FluidDB vs markdown memory vs raw log in the prompt vs database + raw log
  bench/czech.py       the same life stream in Czech (Jev gate + end to end)
  bench/year.py        a simulated year: ingest with schema-growth checkpoints, evaluate at 6 and 12 months
  bench/sleep.py       offline consolidation on a finished year database; year_chart.py draws schema growth
  bench/mem0_year.py   Mem0 on the same simulated year
  bench/forget.py      does "forget X" really erase X? planner alone, + a Jev audit, + deterministic propagation
  bench/judge_pairs.py rebuild (answer, Opus verdict) pairs from the cache to validate cheaper judges ($0)
  bench/det_read.py    the deterministic reader on the year's questions, with LLM-agent fallback curves
  bench/scale5k.py     5,000+ memories: tiered ingestion (compiled code / Jev gate / planner), write accuracy vs truth,
                       async passes, and reads (deterministic, agent, hybrid)
  bench/workload.py    round 5 (llp/0003): query log -> schema advisor -> compiled questions, scored on future questions
  bench/app.py         round 6 (llp/0012): confidence calibration, the oracle inbox, the generated app, the year test
  memory/              round 7 (llp/0013): a memory with several stores, like a brain, built from swappable parts;
                       round 12 (llp/0019) adds log_embed, log_hybrid, statements and dossier
    core.py            Evidence, Store, Context (db + log + saved consolidations), Memory (router -> stores -> answerer)
    stores.py          log, facts, episodes (day gists), routines (code), intentions, preferences, periods (+ month
                       summaries); erase_forgotten and the Jev aspect pass that consolidation ("sleep") shares
    deciders.py        closed-set decisions by Jev or by an LLM, same interface (the "Jev is why now" swap)
    routers.py         all / oracle / a decider's yes-no per store (threshold = the recall knob)
    answerers.py       working memory: evidence in a fixed store order under a budget, one LLM answer; the round-4 hybrid
    configs.py         every experiment is a config: stores + router + answerer (+ lesion); one line adds a variant
  datasets/memory_types.py  94 questions in 12 memory types (+ 36 held out, written after the system was frozen)
  bench/brain.py       round 7: setup (erase, build stores), run configs / lesions / full context, summary, audit
  bench/brain_deciders.py  H5: Jev vs GPT-6 Luna on the same closed decisions (aspects, row checks, verifier, routing)
  datasets/simulate_scenarios.py  round 8 (llp/0014): a support desk, a sales pipeline, a team's projects, with truth
  bench/scenarios.py   round 8: ingest a scenario (tiered writer), write accuracy vs truth, memory configs, the gate test;
                       round 9: writer variants (support@v25), the crossover summary
  datasets/crossover.py  round 9 (llp/0016): the life stream at 6, 12 and 30 months, 36 questions about its last 6 months
  datasets/private_chat.py  round 10 (llp/0017): a person's chats -> windows, probes and cuts; real data, private dir only
  bench/real_return.py round 10: what was re-told after each cut, briefings, recall on cue (the product's memory as stores: memory/stores.py)
  bench/other_users.py round 11 (llp/0018): other people's accounts, OpenAI only: genuine-use check, cuts, summaries
  bench/replay.py      round 13 (llp/0020): conversations replayed turn by turn; searches, the router and the picker
  bench/locomo.py      LoCoMo under mem0's memory-benchmarks protocol: FluidDB vs Mem0 vs full context
  bench/longmemeval.py LongMemEval_S (stratified sample) with the benchmark's original prompts and judge
  bench/external/      prompts vendored from mem0ai/memory-benchmarks (Apache-2.0) and LongMemEval (MIT)
  report.py            table of all end-to-end runs
  fluid.py             try FluidDB v2 interactively
  explorer.py          generate a browsable HTML explorer from any FluidDB database (views picked by Jev)
  appgen.py            generate an app: tiles from the query log, cards and breakdowns from the schema, the inbox
  mcp_server.py        FluidDB as an MCP server (remember / ask / sql / schema / explore) for Claude Code and others
```

## Setup

Run all commands in this guide from `deprecated/python/`. This is the archived Python research bench;
new development and TypeScript evaluations run from the repository root.

Keys live in the repository-root `../../.env` (relative to `deprecated/python/`) (git-ignored): `OPENROUTER_API_KEY` (Jev and other OpenRouter models),
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python openai anthropic httpx pydantic python-dotenv mcp
# only for the Mem0 baseline:
uv pip install --python .venv/bin/python "mem0ai[nlp]" fastembed && .venv/bin/python -m spacy download en_core_web_sm
```

## Run

```bash
# play with it: plain lines are remembered, "?..." asks, ".schema" shows the tables
.venv/bin/python -m lab.fluid --user "Adam Zvada"

# one end-to-end run (system: legacy2024 | v2 | v2jev | v21 | v21jev)
.venv/bin/python -m lab.run_e2e --system v21jev --model openai:gpt-6-luna --dataset life_stream

# component benchmarks and the scale test
.venv/bin/python -m lab.bench.components all
.venv/bin/python -m lab.bench.scale --sizes 1000 5000

# summary table of every run in lab/runs
.venv/bin/python -m lab.report

# is the structure right? how fast is each way of querying? (latency runs need the cache off)
.venv/bin/python -m lab.bench.structure
LAB_NO_CACHE=1 .venv/bin/python -m lab.bench.query_modes
.venv/bin/python -m lab.bench.jev_queries
.venv/bin/python -m lab.bench.injection

# generate the explorer for a database and open it
.venv/bin/python -m lab.explorer --db lab/runs/v21jev__openai_gpt-6-luna__life_stream/db.sqlite --user "Adam Zvada"
open lab/explorer/index.html

# the simulated year (one ingest process per config), consolidation, evaluation
.venv/bin/python -m lab.bench.year ingest --variant v23 --effort low
.venv/bin/python -m lab.bench.sleep --variant v23 --effort low
LAB_ANTHROPIC_VIA=openrouter .venv/bin/python -m lab.bench.year evaluate --efforts v23/low v23/low:sleep --only rdb sql agent
.venv/bin/python -m lab.bench.forget --variant v23 --effort low

# public benchmarks
.venv/bin/python -m lab.bench.locomo ingest --system fluid     # and --system mem0
.venv/bin/python -m lab.bench.locomo evaluate --modes full mem0:top200 fluid:rdb fluid:rdb+log fluid:agent
.venv/bin/python -m lab.bench.longmemeval sample --n 100 && .venv/bin/python -m lab.bench.longmemeval ingest
.venv/bin/python -m lab.bench.longmemeval evaluate --modes full fluid:rdb fluid:rdb+log fluid:agent

# round 3: deterministic reads and cheap judging (Jev only; LLM calls would raise)
LAB_CACHE_ONLY=1 .venv/bin/python -m lab.bench.judge_pairs
LAB_CACHE_ONLY=llm .venv/bin/python -m lab.bench.det_read --db db_typed.sqlite --out bench_det_read_typed.json

# round 4: 5,000+ memories (~$0.85 fresh; stops at --budget dollars of real spend)
.venv/bin/python -m lab.datasets.simulate_scale
.venv/bin/python -m lab.bench.scale5k ingest --bootstrap 400 --budget 1.5
.venv/bin/python -m lab.bench.scale5k writes      # no model: every stored row checked against the truth
.venv/bin/python -m lab.bench.scale5k passes      # dedupe, supersession, typed columns (Jev, <1 cent)
.venv/bin/python -m lab.bench.scale5k reads

# round 5: a schema shaped by its queries (llp/0003-query-driven-schema.rfc.md; ~$1 with development runs)
.venv/bin/python -m lab.datasets.workload
.venv/bin/python -m lab.bench.workload log          # past questions -> _queries in runs/workload/db_base.sqlite
.venv/bin/python -m lab.bench.workload optimize     # schema advisor -> runs/workload/db_opt.sqlite, migrations.json
.venv/bin/python -m lab.bench.workload compile      # question templates from trusted plans -> templates.json
.venv/bin/python -m lab.bench.workload evaluate     # future questions: base / evolved / + compiled; 72-question check
.venv/bin/python -m lab.bench.workload latency      # live timings (cache off)
.venv/bin/python -m lab.bench.workload physical     # indexes at 5k and 1M rows (no model)

# round 6: the generated app, per-row confidence and the inbox (llp/0012; ~$0.12)
.venv/bin/python -m lab.bench.app setup && .venv/bin/python -m lab.bench.app truth
.venv/bin/python -m lab.bench.app confidence        # code checks + Jev for every dated row -> _confidence
.venv/bin/python -m lab.bench.app calibrate         # AUROC, wrong rows caught at 1/2/5/10% per strategy (no model)
.venv/bin/python -m lab.bench.app inbox             # an oracle user answers the top N; wrong rows and app answers after
.venv/bin/python -m lab.bench.app app               # lab/app/index.html + page2..5.html, screenshots, SQL checks
.venv/bin/python -m lab.bench.app evaluate          # future questions read off the app, before/after the inbox
.venv/bin/python -m lab.bench.app year              # the same generator on the year database
open lab/app/index.html

# round 7: a memory with several stores, like a brain (llp/0013; ~$1.5 including development)
.venv/bin/python -m lab.bench.brain setup           # copy the round-6 db, erase forgotten data, build the 7 stores
.venv/bin/python -m lab.bench.brain run brain_jev brain_all facts_log hybrid_r4   # any configs from lab/memory/configs.py
.venv/bin/python -m lab.bench.brain run lesion_episodes full_context
.venv/bin/python -m lab.bench.brain summary         # accuracy by memory type x config; H1-H4
LAB_BRAIN_SET=holdout .venv/bin/python -m lab.bench.brain run brain_jev hybrid_r4   # the 36 held-out questions
.venv/bin/python -m lab.bench.brain deciders        # H5: Jev vs Luna as the decider
.venv/bin/python -m lab.bench.brain audit           # no forgotten string in any store; one-time build cost

# round 8: the same system on different scenarios (llp/0014; ~$2.45 including development)
.venv/bin/python -m lab.datasets.simulate_scenarios        # -> lab/datasets/scenario_{support,sales,projects}.json
.venv/bin/python -m lab.bench.scenarios ingest support     # tiered writer; "sales@v24" = the link-aware write context
.venv/bin/python -m lab.bench.scenarios writes support     # write accuracy against the truth (no model)
.venv/bin/python -m lab.bench.scenarios memory support     # erase, build the 7 stores, run the configs, Jev judge
.venv/bin/python -m lab.bench.scenarios gate               # the round-4 Jev gate vs a domain-neutral wording
.venv/bin/python -m lab.bench.scenarios summary            # every scenario; H1-H6

# round 9: writer v2.5 (compiled updates) and the crossover (llp/0016; ~$2.25)
.venv/bin/python -m lab.bench.scenarios ingest support@v25 # neutral gate + link-aware context + compiled update patterns
.venv/bin/python -m lab.datasets.crossover                 # one life at 6, 12 and 30 months; the same 36 questions
.venv/bin/python -m lab.bench.scenarios ingest life6m      # (life30m reuses round 4's database)
.venv/bin/python -m lab.bench.scenarios memory life6m full_context facts_log brain_jev_recall brain_all full_facts
.venv/bin/python -m lab.bench.scenarios crossover          # the curve, with paired CIs

# round 10: real conversations (llp/0017; ~$2.37). Real data: LAB_PRIVATE_DIR (git-ignored, outside this repo) holds
# the export, datasets, databases and results; nothing real is written under lab/ except the git-ignored cache.
export LAB_PRIVATE_DIR=/path/to/private/dir LAB_PRODUCT_MEMORY=$LAB_PRIVATE_DIR/export.json
.venv/bin/python -m lab.datasets.private_chat $LAB_PRIVATE_DIR/export.json $LAB_PRIVATE_DIR --user "Name" \
    --cuts 2024-08-01:2024-09-01,2024-09-01:2024-12-01,2026-08-01:2026-10-01
.venv/bin/python -m lab.bench.scenarios ingest own@v25nog  # v2.5 without the document guard; own2408@v25nog etc. replay
.venv/bin/python -m lab.bench.scenarios memory own@v25nog product_injected product facts_log brain_all brain_jev_recall full_context
.venv/bin/python -m lab.bench.real_return gold own2408     # what was re-told after the cut, verified in the history
.venv/bin/python -m lab.bench.real_return lasting own2408  # Jev: lasting facts vs the moment
LAB_PRODUCT_CUTOFF=2024-08-01 .venv/bin/python -m lab.bench.scenarios memory own2408@v25nog product_injected product ...
.venv/bin/python -m lab.bench.real_return score own2408@v25nog      # briefings: recall of the re-told statements
.venv/bin/python -m lab.bench.real_return cue own2408               # recall on cue: questions added to the dataset
.venv/bin/python -m lab.bench.real_return cue_summary own2408@v25nog own2409@v25nog own2608@v25nog
.venv/bin/python -m lab.bench.real_return side own@v25nog           # the 12 probes side by side, for the person to read

# round 11: other people's conversations (llp/0018; ~$7.66). OpenAI only: LAB_PROCESSORS=openai makes any other provider
# raise and answers Jev's questions with the OpenAI model (lab/common/jev.py ask_llm); u<number> datasets refuse to load
# without it. Print aggregates only; the exports and logs stay in LAB_PRIVATE_DIR.
export LAB_PROCESSORS=openai
.venv/bin/python -m lab.bench.real_return judge_check own2408@v25nog own2409@v25nog own2608@v25nog  # shim judge vs Jev
.venv/bin/python -m lab.bench.other_users genuine U1 U5 ...     # genuine use vs testing, from a 40-message sample
.venv/bin/python -m lab.bench.other_users prepare U5            # one cut: <= 150 windows and 60% before, <= 15 chats after
.venv/bin/python -m lab.bench.real_return gold u5               # then lasting u5, cue u5 (no controls for u<number>)
.venv/bin/python -m lab.bench.scenarios ingest u5@v25nog
LAB_PRODUCT_MEMORY=$LAB_PRIVATE_DIR/U5_export.json LAB_PRODUCT_CUTOFF=2025-02-18 \
    .venv/bin/python -m lab.bench.scenarios memory u5@v25nog product_injected product facts_log brain_all brain_jev_recall full_context
.venv/bin/python -m lab.bench.other_users summary six u5 u1 u19 u11 u8 u25   # pooled, per account, paired CIs, languages
.venv/bin/python -m lab.bench.other_users tables u5 u1 ...      # H4: rows and the largest table's share (no names)

# round 12: memory for conversation (llp/0019; ~$3.55): meaning search (OpenAI embeddings, lab/common/llm.py embed),
# a statement store and a dossier, as new stores in lab/memory/stores.py; same benchmark and rules as round 11
.venv/bin/python -m lab.bench.scenarios memory u5@v25nog log_rag log_embed log_hybrid statements dossier conv_all
.venv/bin/python -m lab.bench.other_users compare all18 u5 u1 u19 ...   # pooled, per account, the round's pairs, Czech, tokens

# round 13: search and creation done properly, Jev picks (llp/0020; ~$5.20)
.venv/bin/python -m lab.bench.replay run u5 u1 ...              # every turn replayed, hit@k by provenance (no judge)
.venv/bin/python -m lab.bench.replay summary u5 u1 ...
.venv/bin/python -m lab.bench.replay route u5 ...               # router vs picker; own* accounts may use real Jev:
.venv/bin/python -m lab.bench.replay route_summary shim u5 ...  #   unset LAB_PROCESSORS and pass own2408 own2409 own2608
.venv/bin/python -m lab.bench.scenarios memory u5@v25nog statements_merged dossier_inc conv_best conv_router

# round 14: a better picker, every wording kept, a live replay (llp/0021; ~$11.08)
.venv/bin/python -m lab.bench.replay pick u5 ...                # four pickers over meaning's top 20 (own*: real Jev)
.venv/bin/python -m lab.bench.replay pick_summary shim u5 ...
.venv/bin/python -m lab.bench.scenarios memory u5@v25nog statements_linked conv_best2 conv_best2_jev
LAB_PRODUCT_MEMORY=... LAB_PRODUCT_CUTOFF=... .venv/bin/python -m lab.bench.replay live u5      # the assistant writes
LAB_PRODUCT_MEMORY=... LAB_PRODUCT_CUTOFF=... .venv/bin/python -m lab.bench.replay live_active u5   # told to use memory
.venv/bin/python -m lab.bench.replay strict u5 ...              # the same fact, strictly
.venv/bin/python -m lab.bench.replay live_summary u5 ...

# FluidDB as memory for Claude Code (MCP over stdio)
# Round 15's existing detector results and protocol: llp/0022 and llp/0022.000.
# Re-analysis of cached research must use LAB_CACHE_ONLY=1.
claude mcp add fluiddb -- $PWD/.venv/bin/python $PWD/lab/mcp_server.py --db ~/fluiddb/memory.sqlite --user "Your Name"
```

Every model call is cached in `lab/.cache/`, so re-running anything that already ran costs nothing
and reproduces the same numbers (latency and cost come from the original call). `LAB_NO_CACHE=1` forces live
calls (used for latency). `LAB_CACHE_ONLY=1` makes any uncached model call raise instead of being paid for
(`LAB_CACHE_ONLY=llm` still allows Jev, which costs ~$0.04 per 1M tokens). `LAB_ANTHROPIC_VIA=openrouter` sends Claude models through OpenRouter instead of the
Anthropic API (same models; used after the direct account ran out of credits).
