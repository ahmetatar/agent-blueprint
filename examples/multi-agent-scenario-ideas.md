# Multi-Agent Scenario Ideas

This document collects real-world multi-agent blueprint ideas for future `examples/` files. These are not implemented blueprints yet. Use this list to choose which scenario to turn into YAML next.

## 1. Customer Support Triage And Resolution

A support assistant classifies a customer request, routes it to the right specialist, drafts a response, and escalates to a human when confidence is low or the issue is sensitive.

Agents:

- `triage_agent`: detects intent, urgency, department, and confidence.
- `billing_agent`: handles invoice, subscription, refund, and payment issues.
- `technical_agent`: handles bug reports and troubleshooting.
- `retention_agent`: handles cancellation, churn risk, and angry customers.
- `quality_agent`: reviews the final response for correctness and tone.

Possible graph:

```text
triage -> billing | technical | retention -> quality -> END
quality -> human_handoff when confidence is low
```

Why it is a good ABP example:

- Clear conditional routing.
- Realistic human handoff.
- Good fit for `output_schema` fields such as `department`, `urgency`, and `confidence`.
- Native reasoning can improve triage and response quality without replacing the graph routing.

## 2. Incident Response War Room

An operations assistant handles a production incident report, classifies severity, analyzes signals, proposes mitigation, drafts status updates, and prepares a postmortem summary.

Agents:

- `incident_commander`: determines severity, priority, owner, and next action.
- `log_analyst`: summarizes logs, traces, metrics, and symptoms.
- `runbook_agent`: maps symptoms to likely runbook steps.
- `comms_agent`: drafts Slack, email, or status page updates.
- `postmortem_agent`: prepares a timeline and lessons learned after resolution.

Possible graph:

```text
classify_incident -> analyze_signals -> propose_mitigation -> draft_comms -> human_approval -> END
```

Why it is a good ABP example:

- Strong real-world DevOps use case.
- Natural state fields: `severity`, `affected_services`, `mitigation_steps`, `customer_message`.
- Good place to show tool definitions for logs, metrics, and status pages.
- Human approval is realistic before external communication.
- Shows how graph-level workflow control and native reasoning can work together.

## 3. Sales Proposal Builder

A sales assistant turns a customer brief into a structured proposal with scope, pricing assumptions, risks, and final customer-facing language.

Agents:

- `discovery_agent`: extracts customer goals, constraints, timeline, and stakeholders.
- `solution_architect`: proposes solution scope and implementation phases.
- `pricing_agent`: estimates package, effort, and pricing assumptions.
- `risk_agent`: identifies delivery, technical, and commercial risks.
- `proposal_writer`: writes the final proposal.
- `reviewer`: checks completeness and consistency.

Possible graph:

```text
discovery -> solution -> pricing -> risk -> proposal -> review
review -> proposal when score is below threshold
review -> END when score is acceptable
```

Why it is a good ABP example:

- Strong business value.
- Natural multi-agent role separation.
- Good fit for review loops and quality gates.
- Native reasoning can improve each specialist agent while the graph controls the proposal workflow.

## 4. Contract Review Assistant

A review assistant analyzes a contract, extracts important clauses, identifies risk, suggests negotiation points, and creates an executive summary.

Agents:

- `clause_extractor`: extracts key clauses and obligations.
- `risk_reviewer`: flags risky or unusual terms.
- `negotiation_agent`: suggests alternative language or negotiation points.
- `compliance_agent`: checks against company policy.
- `summary_agent`: writes a concise executive summary.

Possible graph:

```text
extract_clauses -> review_risks -> compliance_check -> negotiation_notes -> summary -> human_review
```

Why it is a good ABP example:

- Shows high-stakes workflow boundaries.
- Human review is mandatory and natural.
- Good fit for structured outputs and risk scoring.
- Demonstrates that the system assists review but does not provide legal advice.

## 5. Travel Concierge Planner

A travel planner collects preferences, proposes a destination, builds an itinerary, checks budget, adjusts for weather, and produces a final plan.

Agents:

- `preference_agent`: extracts dates, budget, interests, constraints, and travel style.
- `destination_agent`: recommends destination options.
- `itinerary_agent`: creates a day-by-day plan.
- `budget_agent`: checks whether the itinerary fits the budget.
- `weather_agent`: adjusts activities for expected weather.
- `final_writer`: presents the final trip plan.

Possible graph:

```text
preferences -> destination -> itinerary -> budget_check
budget_check -> revise_itinerary when over budget
budget_check -> weather_check when budget is ok
weather_check -> final
```

Why it is a good ABP example:

- Easy to understand.
- Good fit for optional tools such as weather, maps, and search.
- Natural conditional branching when budget or weather constraints fail.
- Useful for showing user-friendly final output formatting.

## 6. Recruiting Screening Pipeline

A hiring assistant compares a resume to a job description, scores fit, identifies gaps, and generates interview questions.

Agents:

- `resume_parser`: extracts structured candidate profile data.
- `job_matcher`: compares candidate profile against job requirements.
- `risk_checker`: identifies gaps, missing evidence, and uncertainty.
- `interview_designer`: creates targeted interview questions.
- `hiring_summary`: writes a decision-support summary for a recruiter.

Possible graph:

```text
parse_resume -> match_job -> risk_check -> interview_questions -> summary -> END
```

Why it is a good ABP example:

- Strong fit for structured output.
- Clear human decision boundary: the system assists but does not make the hiring decision.
- Good place to document bias and fairness constraints.
- Practical HR workflow without requiring many external tools.

## 7. Product Feedback Analyst

A product assistant analyzes user feedback, groups it into themes, estimates impact, and turns it into roadmap suggestions.

Agents:

- `feedback_classifier`: classifies theme, sentiment, product area, and urgency.
- `trend_agent`: finds recurring patterns and clusters.
- `impact_agent`: estimates user impact and business impact.
- `roadmap_agent`: suggests features, fixes, and priorities.
- `release_notes_agent`: drafts customer-facing follow-up language.

Possible graph:

```text
classify_feedback -> detect_trends -> score_impact -> propose_roadmap -> draft_release_notes -> END
```

Why it is a good ABP example:

- Strong product management use case.
- Good fit for aggregation and prioritization state.
- Can show how multiple agents transform raw feedback into roadmap decisions.
- Useful for demonstrating output schemas and scoring.

## 8. Healthcare Symptom Intake Assistant

A safety-focused intake assistant collects symptoms, checks for red flags, and prepares a doctor-facing summary without diagnosing the user.

Agents:

- `intake_agent`: gathers symptoms, duration, severity, and context.
- `red_flag_agent`: checks for urgent warning signs.
- `doctor_summary_agent`: creates a concise summary for a clinician.
- `safety_agent`: writes safe next-step guidance.

Possible graph:

```text
intake -> red_flag_check
red_flag_check -> emergency_guidance when urgent
red_flag_check -> doctor_summary when not urgent
doctor_summary -> safety_guidance -> END
```

Why it is a good ABP example:

- Strong safety and escalation story.
- Human/clinician boundary is explicit.
- Good fit for risk routing and clear disclaimers.
- Must be framed as intake support, not diagnosis or medical advice.

## Recommended First Picks

| Rank | Scenario | Reason |
|---|---|---|
| 1 | Incident Response War Room | Best technical demo for graph control, native reasoning, tools, state, and human approval |
| 2 | Sales Proposal Builder | Strong business workflow with review loops and specialist agents |
| 3 | Customer Support Triage And Resolution | Easy to understand, clear conditional routing, realistic handoff |

## Selection Criteria

Choose a scenario based on what the example should demonstrate:

| Goal | Best scenario |
|---|---|
| Technical operations and tool usage | Incident Response War Room |
| Business workflow and review loop | Sales Proposal Builder |
| Conditional routing and support automation | Customer Support Triage And Resolution |
| Safety and human boundaries | Contract Review Assistant or Healthcare Symptom Intake Assistant |
| Friendly consumer-facing flow | Travel Concierge Planner |
| Structured scoring and decision support | Recruiting Screening Pipeline |
| Product analytics workflow | Product Feedback Analyst |
