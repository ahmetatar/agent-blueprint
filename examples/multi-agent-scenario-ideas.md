# Examples Portfolio — Scenario Ideas

This document plans the rebuild of `examples/`. The goal is not "more demos" —
it is a small, curated portfolio where **every ABP capability is showcased by
exactly the example that makes it shine**, arranged as a complexity ladder from
hello-world to a fully gated, observable production workflow — with domain
stories taken from real life, not enterprise slideware.

Status: ideas for evaluation. Nothing here is implemented yet.

## Design principles

1. **Feature-driven, not domain-driven.** Each example exists to demonstrate a
   named cluster of ABP features. The domain story is the vehicle — but the
   vehicle should be fun to drive.
2. **A ladder, not a pile.** Examples escalate: each one introduces new
   concepts on top of the previous level, so the portfolio doubles as a tutorial path.
3. **Everything runs offline.** Every example ships harness scenarios with
   `llm_mode: mock` / `tool_mode: stub`, so `abp test examples/<x>.yml` passes
   with **no API key**. The examples are living tests, not just documentation.
4. **CI validates all of them.** The rebuild must extend the CI smoke step from
   `abp validate examples/basic-chatbot.yml` to validating (and `abp lint`-ing,
   ideally `abp test`-ing) every example. A broken example can never ship again.
5. **Each file opens with a feature manifest.** A YAML comment block listing
   exactly which features the example demonstrates, so a reader scanning the
   directory can pick by capability.

---

## The proposed ladder

| Level | File | Domain story | Feature cluster |
|---|---|---|---|
| L1 | `01-rubber-duck.yml` | Rubber-duck debugging companion | The smallest valid blueprint |
| L2 | `02-smart-home-butler.yml` | Home assistant that won't unlock the door on its own | Conditional routing · contracts · escalation · approvals · handoff |
| L3 | `03-meal-prep-chef.yml` | Weekly meal plan from your own recipe collection | Sequential pipeline · RAG retriever · artifacts · rubric evals |
| L4 | `04-wedding-planner.yml` | Wedding-planning war room (with a literal budget) | Parallel fan-out/join · nested subgraphs · supervisor · multi-channel handoff · budgets |
| L5 | `05-cafe-review-desk.yml` | Café owner's review-reply desk, under CI control | Harness scenarios · eval suites · gate baseline · traces flywheel · OTel · sandbox |
| L6 | `06-gym-buddy.yml` | A workout coach that remembers last week | `abp package` · sqlite memory · function `impl` tools · reasoning config |
| L7 | `07-morning-briefing.yml` | One command, your whole morning | API tools with auth (bearer/api_key) · function `impl` tools · tool-usage policies · retry/backoff |
| L8 | `08-downloads-janitor.yml` | Tames your Downloads folder via MCP | `mcp_servers` (stdio) · `mcp` tools · approval-gated file moves — **staged: lands with MCP generation** |

Eight files, ~full feature coverage, each one independently runnable and testable
(L8 ships together with MCP tool generation — see its section).

---

## L1 — `01-rubber-duck.yml` · Rubber Duck

The classic debugging companion: you explain your problem, the duck asks the
one question that makes you realize the answer. One agent, one edge, ~20 lines.
Exists so the first thing a newcomer copies is trivially understandable —
and immediately likable.

**Features:** `blueprint`, `settings`, `state`, single `agent`, `graph`, `memory: in_memory`.

**Demo:** `abp run examples/01-rubber-duck.yml "my tests pass locally but fail in CI"`

---

## L2 — `02-smart-home-butler.yml` · Smart-Home Butler

A home assistant routes requests across **lighting**, **climate**, and
**security**. Light switches flip freely — but `unlock_front_door` sits behind
an **approval policy** (`block`), because *no, the AI does not open your house
by itself*. The router emits `intent`, `room`, `confidence` under an **output
contract**; when confidence drops below threshold ("make it cozy"?), the
**escalation policy** reroutes mid-run to a console **handoff** — the butler
asks the human instead of guessing.

**Agents:** `butler` (router), `lighting`, `climate`, `security`, + `ask_owner` (handoff).

```text
butler ──(intent == 'lighting')──▶ lighting ──▶ END
       ──(intent == 'climate')───▶ climate ──▶ END
       ──(intent == 'security')──▶ security ──▶ END   ← unlock tool needs approval
       ──(default)───────────────▶ END
  ⚡ escalation: confidence < 0.75 → ask_owner (handoff/console)
```

**Features:** condition expressions · lint-clean routing · `contracts.outputs`
+ node contracts · `policies.approvals` (block) · `policies.escalation` ·
handoff node (console) · state `enum` fields · API tools (fake home API).

**Why it shines:** the approval gate has *visceral* intuition — everyone
understands why a door lock needs a human yes. The canonical "declared vs
enforced" demo. Replaces the broken `customer-support.yml` energy with
something you'd actually show a friend.

**Demo:** `abp test` scenario proving "unlock the door" triggers
`approvals_triggered: true`, and "make it cozy" provably lands on `ask_owner`
(route assertion).

---

## L3 — `03-meal-prep-chef.yml` · Meal-Prep Chef

Sunday evening: "plan my week, high protein, no cilantro, 30-minute dinners."
A planner extracts constraints, a chef agent retrieves matching dishes from
**your own recipe collection** (a RAG retriever with a local `impl` over a
`recipes/` folder of markdown files — no external service), and a writer
produces a **markdown artifact** (`weekly_plan.md`) bound to an output
contract. A **rubric eval suite** keeps the plan honest: must contain a
day-by-day section, a consolidated shopping list, and respect the no-cilantro
rule (forbidden-term check).

**Agents:** `planner`, `chef` (RAG hybrid mode), `meal_writer`.

```text
plan ──▶ pick_recipes ──▶ write_plan ──▶ END
                                        └─ produces artifact: weekly_plan.md
```

**Features:** `retrievers` + `retrieval` tool + `agents.*.rag` (context
injection) · `artifacts` with contract binding · `evals` rubric metric
(required sections, forbidden terms, min word count) · node `retry`.

**Why it shines:** "RAG over your own files" is the most-requested real-life
agent use case, and the rubric eval turns *meal plan quality* into a
CI-checkable number — which is both useful and quietly hilarious.

**Demo:** `abp eval examples/03-meal-prep-chef.yml` scoring the plan offline.

---

## L4 — `04-wedding-planner.yml` · Wedding Planner War Room

T-minus 90 days. A wedding-planner **supervisor** dynamically delegates to
scouts via generated transfer tools: `venue_scout`, `catering_scout`,
`band_scout` (`max_iterations: 6`, because scope creep is real). Vendor
research runs as a **parallel fan-out/join** (venues and caterers in
parallel, merged by state reducers). Every shortlist goes through a reusable
**nested subgraph** — `negotiate` (draft offer → critique → counter) — invoked
twice with different `input_map`s (venue vs. catering). The final plan goes to
the couple via a **handoff** (slack channel, console fallback). And the crown
jewel: a **budget policy** — `max_cost_usd` on the run *and* the wedding
budget in state, so a runaway planning loop dies loudly. Yes, the token budget
and the wedding budget are both enforced. That's the joke, and it's also true.

```text
brief ──▶ planner (supervisor: venue_scout | catering_scout | band_scout,
          max_iterations: 6)
            └─▶ scout_market (parallel: [venues, caterers] join: shortlist)
shortlist ──▶ negotiate_venue    (subgraph: draft → critique → counter)
          ──▶ negotiate_catering (same subgraph, different input_map)
          ──▶ couple_signoff (handoff/slack) ──▶ END
```

**Features:** supervisor (`workers`, `max_iterations`, `agent_handoff` traces) ·
parallel node + reducer-merged join · nested subgraph reuse with
`input_map`/`output_map` · handoff channels (slack/console) ·
`policies.budgets` · `settings.max_graph_steps` · multi-provider config
(a thinking model for the planner, a cheap fast model for the scouts).

**Why it shines:** every advanced node type in one believable, high-chaos,
universally understood story. `abp inspect` renders a Mermaid graph that makes
people grin.

**Demo:** harness scenario asserts the supervisor's delegation sequence via
`agent_handoff` events; the budget-blown scenario asserts a `policy_violation`.

---

## L5 — `05-cafe-review-desk.yml` · Café Review Desk

A neighborhood café gets online reviews — glowing, furious, and weird
("the wifi password has too many numbers"). The agent classifies each review
and drafts an on-brand reply under strict rules: warm tone, never argue,
and **never offer more than a 10% discount**. The graph is simple; the point
is the **operational lifecycle around it**:

- a `harness` block: golden-path replies + a hostile-review scenario (mock LLM, seeded)
- an `evals` block over a shipped `datasets/` of real-ish reviews:
  `exact_match` on the routing category, `policy_violations` on the discount
  rule — the "free croissant for life" regression is a failing test, not a tweet
- a committed gate baseline and a documented `abp gate` CI recipe
- the documented `abp traces export` loop: a failed reply becomes tomorrow's eval case
- `observability.tracing` (console exporter — works offline) and `run.sandbox`
  declared in the blueprint

**Features:** harness route/state/output-contract assertions · eval suites +
datasets (`exact_match`, `policy_violations`) · gate baseline + regression demo ·
traces flywheel · OTel console export · declarative sandbox.

**Why it shines:** "my café bot is under CI regression control" is the
funniest possible way to make ABP's deepest point: agent behavior as a
gateable, versioned artifact. README's lifecycle diagram becomes a working
example with espresso.

**Demo:** `abp gate examples/05-cafe-review-desk.yml` — green; loosen the
discount prompt, watch the gate go red.

---

## L6 — `06-gym-buddy.yml` · Gym Buddy

A personal workout coach you install as a real command: `gym-buddy "leg day,
45 minutes, my knee is acting up"`. A function `impl` tool reads/writes a
local training log; **sqlite memory** means it *remembers last week* across
invocations — `--thread-id` is your training journal. Reasoning config
(`llm_kwargs` passthrough) lets the coach actually think about progressive
overload instead of pattern-matching. Packaged end-to-end with **`abp package`**:

```bash
abp package examples/06-gym-buddy.yml
pipx install ./gym-buddy-cli
gym-buddy "what did we do on Monday, and what's next?"
```

**Features:** `abp package` end-to-end · `memory.backend: sqlite` (persistent
threads across processes) · function tools with `impl` · `reasoning` config ·
`${env.*}` interpolation · `.env` loading.

**Why it shines:** answers "can I build a real local agentic tool with this?"
with a command you genuinely might keep using. The sqlite-persistence demo has
an obvious *why* — a coach that forgets your squat PR is no coach. Pairs with
`docs/cli-packaging.md`.

---

## L7 — `07-morning-briefing.yml` · Morning Briefing

One command before coffee: `abp run examples/07-morning-briefing.yml "brief me"`.
The agent talks to the world around it:

- `get_weather` — **API tool**, plain GET with an `api_key` query auth
- `get_headlines` — **API tool** with **bearer auth** (`token_env: NEWS_API_TOKEN`)
- `read_calendar` — **function `impl` tool** over a local `.ics`/markdown agenda file
- `commute_check` — **API tool** with **basic auth**, because some transit APIs
  are stuck in 2009 (which is exactly why `AuthDef` supports it)

It fuses all four into a single brief: weather-aware outfit hint, top 3
headlines, first meeting, leave-by time. The point is the **tool governance**
around the calls: `policies.tool_usage` caps `max_calls_per_node` /
`max_calls_per_run` (a briefing agent that calls the news API 40 times is a
bill, not a briefing), `require_explicit_arguments: true`, `on_unknown_tool:
fail`. Flaky external APIs make this the natural home for **node `retry`**
with backoff — and in the harness, every tool is a deterministic stub, so the
whole morning runs offline.

**Agents:** `briefer` (tool-calling loop), `editor` (formats the final brief).

```text
briefer (tools: weather, headlines, calendar, commute) ──▶ editor ──▶ END
         caps: max_calls_per_node: 6, max_calls_per_run: 8
```

**Features:** API tools across all three auth types (bearer/api_key/basic) ·
function `impl` tools · `policies.tool_usage` (caps + explicit args + unknown-tool
fail) · `retry` with backoff · `${env.*}` interpolation · tool stubs in harness.

**Why it shines:** the densest "agent ↔ environment" example — four real
integrations, every auth flavor, and the guardrails that keep tool loops from
becoming invoices. The harness scenario asserts `tools_called` order and that
the call caps hold.

---

## L8 — `08-downloads-janitor.yml` · Downloads Janitor (MCP)

> **Staged:** `mcp` tool generation is not implemented yet — `abp generate`
> rejects it by design. This example is written as the **acceptance demo for
> the MCP-generation roadmap item** and ships in the same PR that implements it.

Everyone's Downloads folder is a crime scene. The janitor connects to the
**filesystem MCP server** (`@modelcontextprotocol/server-filesystem`, stdio
transport) — no custom tool code at all, the tools come *from the server*:
`list_directory`, `read_file`, `move_file`. It proposes a cleanup plan
(screenshots → `/Pictures/Screenshots`, invoices → `/Documents/Receipts`,
the 9 copies of `setup(3).dmg` → trash list), and the destructive step is
**approval-gated**: `move_file` sits behind `policies.approvals` with
`mode: selective`, so the plan is free but the execution needs a human yes.

**Agents:** `janitor` (mcp tools), + `confirm_plan` (handoff/console).

```text
janitor (mcp: list_directory, read_file, move_file*) ──▶ confirm_plan ──▶ END
         * move_file requires approval
```

**Features:** `mcp_servers` (stdio transport, `command`/`args`) · `mcp` tool
type bound to server-provided tools · approvals on MCP tools · the
schema-validation + doctor story for MCP (cross-checked refs, clear
diagnostics) — and, once generation lands, the runtime binding itself.

**Why it shines:** MCP is the ecosystem's connector standard; this shows ABP
consuming third-party tool servers declaratively — and proves the approval
policy composes with tools ABP didn't define. Also: genuinely useful.

**Demo (post-implementation):**
```bash
abp run examples/08-downloads-janitor.yml "clean up my downloads"
# plan printed → approval prompt → moves execute
```

---

## Feature coverage matrix

| Feature | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 |
|---|---|---|---|---|---|---|---|---|
| Minimal blueprint / settings / state | ● | ● | ● | ● | ● | ● | ● | ● |
| Conditional routing + expressions | | ● | | ● | | | | |
| Output/node/state contracts | | ● | ● | | ● | | | |
| Approval policies | | ● | | | | | | ● |
| Escalation (low confidence) | | ● | | | | | | |
| Handoff node (console/slack) | | ● | | ● | | | | ● |
| Parallel fan-out/join | | | | ● | | | | |
| Nested subgraphs (reuse) | | | | ● | | | | |
| Supervisor + transfer tools | | | | ● | | | | |
| Budgets / max_graph_steps | | | | ● | ● | | | |
| Tool-usage policies (caps, explicit args) | | | | | | | ● | |
| Retry policies | | | ● | | | | ● | |
| RAG retriever + context injection | | | ● | | | | | |
| Artifacts + contract binding | | | ● | ● | | | | |
| Rubric evals | | | ● | | | | | |
| exact_match / policy evals + datasets | | | | | ● | | | |
| Harness scenarios (mock/stub) | (●) | ● | ● | ● | ● | ● | ● | ● |
| Gate baseline + CI recipe | | | | | ● | | | |
| Traces flywheel | | | | | ● | | | |
| Observability / OTel | | | | | ● | | | |
| Sandbox (`run.sandbox`) | | | | | ● | | | |
| `abp package` + sqlite memory | | | | | | ● | | |
| Function `impl` tools | | | ● | | ● | ● | ● | |
| API tools (bearer / api_key / basic auth) | | ● | | ● | | | ● | |
| MCP servers + `mcp` tools | | | | | | | | ◐ |
| Reasoning / llm_kwargs | | | | ● | ● | ● | | |
| Model providers (multi) | | | | ● | | ● | | |

(●) = present but minimal. ◐ = staged: L8 is written against the MCP schema
today but ships together with MCP tool generation (roadmap). With it, the
matrix has no uncovered feature.

## Alternate domains (swappable per level)

Kept on the bench in case a story doesn't land during evaluation:

| Level | Alternates |
|---|---|
| L1 | fortune-cookie generator · compliment bot |
| L2 | pizza-ordering agent (placing the order = approval) · plant-watering butler |
| L3 | D&D dungeon-master's assistant (RAG over campaign lore, session-recap artifact) · trip itinerary from your own travel notes |
| L4 | surprise-birthday-party war room · house-move coordinator |
| L5 | horoscope generator under regression control (mystical content, rigorous CI) · fantasy-league recap writer |
| L6 | language-learning flashcard coach · houseplant care journal |
| L7 | garden watering advisor (weather API + soil sensor impl) · personal finance pulse (bank API, strict call caps) |
| L8 | repo janitor (GitHub MCP server: stale branches, label triage) · notes gardener (Obsidian-vault MCP) |

## Migration plan (when we build)

1. Build L1–L7 one PR each (or pairs), each example validating, linting, and
   `abp test`-passing offline.
2. Extend CI smoke: `for f in examples/*.yml; do abp validate $f && abp lint $f; done`
   plus `abp test` for the ones with harness blocks. **Note:** CI currently
   smoke-tests `examples/basic-chatbot.yml` by name — the workflow must be
   updated in the same PR that renames/deletes it.
3. Delete the old files (`basic-chatbot`, `customer-support`, `research-team`,
   `incident-response`, `prd-factory`) as each successor lands.
4. Update README's Examples table to the ladder format.
5. L8 is deferred: it lands in (or right after) the PR that implements MCP tool
   generation, serving as its acceptance demo. Until then it exists only here.

## Open questions for evaluation

1. Is eight the right size? (Merge candidates: L3+L5 could fold into one
   "documents under test" example; L1 could be just the README snippet.)
2. Numbered filenames (`01-…`) — good for the ladder story, or too tutorial-ish
   for an `examples/` directory?
3. Should L5 ship its `datasets/` + committed gate baseline inside `examples/`
   (self-contained) or in a dedicated `examples/cafe-review-desk/` directory
   (one example = one dir, scales better)?
4. Any story swaps from the alternates bench?
5. Does L8's "staged until MCP generation lands" framing work — or should the
   MCP example wait entirely until the feature exists?
