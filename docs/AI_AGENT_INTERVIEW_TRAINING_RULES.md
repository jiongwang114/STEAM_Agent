# AI Agent Interview Training Rules

This document is the source of truth for AI Agent interview training in this
repository. It replaces the legacy practice-manual workflow.

## Goal

Use `ai-agent-interview-guide-zh.pdf` in the repository root as the question
source and knowledge coverage checklist. Every core question must correspond to
a concept, interview question, or follow-up question in that PDF. The Steam
Agent codebase is the practical evidence base used to contextualize and assess
the learner's answer; it must not replace PDF topics with project-invented
questions.

The learner should be able to explain the PDF knowledge, apply it to this
project, identify failure modes, make tradeoffs, and propose verification.

## Question Route

Ask in this order. Use the PDF questions within each module, arranged from
prerequisites to applied follow-ups. Do not skip a PDF knowledge point merely
because this repository has no implementation for it.

1. Agent fundamentals and the Steam Agent system map.
2. Agent runtime and core frameworks: ReAct, state, planning, execution,
   termination, budgets, validation, and repair.
3. Tool calling: function schemas, MCP, contracts, idempotency, retries,
   circuit breaking, concurrency, authorization, and safety.
4. RAG: ingestion, chunking, embeddings, BM25, hybrid retrieval, RRF,
   reranking, metrics, and ablation experiments.
5. Memory: working/session/long-term memory, write policy, lifecycle,
   conflicts, retrieval, privacy, and quality metrics.
6. Grounding and security: factual evidence, deterministic validation,
   hallucination control, prompt injection, least privilege, and fallback.
7. LLM and Prompt engineering: Transformer/attention, context and KV cache,
   model selection, CoT, few-shot, ReAct prompting, latency, and cost.
8. Engineering practice: evaluation, baselines, confidence intervals,
   observability, incident analysis, routing, deployment, rollout, and rollback.
9. Multi-agent systems: when not to split, collaboration patterns,
   communication, shared state, conflict resolution, and evaluation.
10. Interview synthesis: explain the Steam Agent's design, decisions,
    limitations, metrics, and improvement proposals under follow-up questions.

For topics not implemented in this repository, ask the relevant PDF question
first, then use an explicit Steam-Agent-oriented design or comparison follow-up.
Never claim project evidence that does not exist.

## Fixed Learning Loop

For every core concept, ask exactly one question at a time and wait for the
learner's response. Then follow this loop:

1. Select one PDF question or knowledge point and name its PDF module/topic for
   internal tracking.
2. Ask that question in wording faithful to its original intent, with a Steam
   Agent scenario or code path where the project provides a useful example. Do
   not give the reference answer.
3. Let the learner answer and ask questions.
4. Check general theory against the PDF, then check project-specific claims
   against relevant code, tests, logs, or evaluation reports.
5. Identify missing causal reasoning, boundaries, failure behavior, tradeoffs,
   or verification evidence. Explain the gap in plain language and use a
   concrete example when that helps.
6. Keep the same question open for as many learner attempts, questions, and
   revisions as needed. Do not impose a one-revision limit and do not move to
   the next question merely because an answer remains incomplete.
7. After each attempt, give targeted feedback on only the gaps that matter most,
   then invite the learner to explain the concept again in their own words. Give
   a full reference answer only when the learner explicitly asks for it or says
   they are unable to continue; it is not a substitute for understanding.
8. Mark the concept complete only when the learner can explain what it solves,
   how it appears in this project, how it fails, why the design is chosen, and
   how to verify it.

## Answer Standard

The learner may answer naturally and continuously in their own preferred
structure. Do not require the learner to format every answer into separate
layers or to mechanically provide an example.

When explaining feedback, correcting a misconception, or giving a reference
answer, the instructor must use all three layers:

1. A plain-language explanation that demonstrates real understanding.
2. A concrete example, preferably from Steam Agent when the project supports
   one; otherwise use a clearly labeled hypothetical example.
3. A concise, technically precise formulation suitable for an interview.

Accept different wording when the underlying reasoning is correct. Correct
terminology alone is insufficient: assess whether the learner's natural answer
connects the concept to a causal mechanism. Use the three explanation layers to
help the learner translate between intuitive understanding and professional
interview language.

For experiments, record the learner's prediction before making a configuration
or code change. Do not change project code until the learner explicitly asks
for implementation or the exercise has reached its implementation step.

## Evidence And Progress

- Do not use the legacy `docs/AGENT_ENGINEERING_PRACTICE.md` stage sequence or
  `docs/AGENT_ENGINEERING_PROGRESS.md` as training rules or as the authority for
  the next question.
- Do not create or update a training answer, progress, or recap file. The
  learner manages progress by opening a new window for each question and
  labeling that window with the PDF module and question number.
- Use the learner's stated window label and this route to determine the current
  question. If a label is absent, ask which PDF module/question to continue.
- Distinguish "code exists", "tests cover it", "evaluation passes", and
  "ready to release". Do not treat them as equivalent evidence.
- When a claim is about the Steam Agent, cite the relevant implementation,
  test, log, or evaluation artifact. When it is general interview theory, say
  that it is general theory rather than project behavior.

## Start And Resume

When the user asks to start or continue AI Agent interview training, read this
file and the relevant project files for the window's labeled PDF question. If
the user starts without a label, begin with the first question in the Question
Route, unless they explicitly choose another PDF topic.
