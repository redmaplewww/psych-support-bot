# Project Progress Tracker

## Current Status

- Project state: MVP backend foundation actively implemented
- Delivery focus: AI framework foundation and execution-ready roadmap
- Recommended stack locked for first implementation:
  - `FastAPI`
  - `LangGraph`
  - `Pydantic`
  - `PostgreSQL`
  - `pgvector`
  - `Redis`
  - `Celery`
  - `Langfuse`
- Current implementation status:
  - Git repository initialized
  - Python project initialized with `uv`
  - FastAPI app scaffolded
  - LangGraph conversation workflow implemented with risk-first routing
  - Multi-school consultation planner added for diagnosis/treatment-style turns
  - Base config, DB, Redis, Celery, and tracing modules scaffolded
  - SQLite-backed persistence added for local MVP development
  - Assessment, check-in, plan, and weekly report APIs added
  - Alembic migration baseline added
  - Basic integration and evaluation tests added

## Milestones

| Milestone | Goal | Status | Target Output |
| --- | --- | --- | --- |
| M0 | Confirm scope and architecture | Done | Project plan and progress tracker |
| M1 | Initialize backend project skeleton | Done | Running FastAPI app and base folders |
| M2 | Implement AI workflow skeleton | Done | LangGraph flow with risk and routing nodes |
| M3 | Implement persistence layer | Done | DB models, migrations, repository layer |
| M4 | Implement core domain APIs | Done | Conversation, assessment, check-in APIs |
| M5 | Implement safety and observability | In progress | Risk logs, tracing, evaluation harness |
| M6 | MVP integration complete | In progress | End-to-end support flow |

## Workstreams

### 1. Foundation

| Task | Priority | Status | Notes |
| --- | --- | --- | --- |
| Create repository structure | High | Done | src package, docs, tests scaffolded |
| Set up Python environment and dependency management | High | Done | Using `uv` |
| Create FastAPI app entrypoint | High | Done | Health route and config loading |
| Add environment configuration | High | Done | `.env.example` and settings module added |

### 2. AI Orchestration

| Task | Priority | Status | Notes |
| --- | --- | --- | --- |
| Define graph state schema | High | Done | Shared LangGraph state object |
| Build risk classifier node | High | Done | Safety-first gate |
| Build intent router node | High | Done | support, assessment, intervention, planning, crisis |
| Build multidisciplinary consultation planner | High | Done | CBT, psychodynamic, humanistic, ACT, DBT orchestration |
| Build memory retrieval node | Medium | Done | Placeholder memory loading |
| Build response generator node | High | Done | Initial scaffolded reply generation |
| Build safety reviewer node | High | Done | Post-generation review hook |
| Build summary writer node | Medium | Done | Summary string scaffold |

### 3. Domain and Data

| Task | Priority | Status | Notes |
| --- | --- | --- | --- |
| Design core DB schema | High | Done | users, sessions, messages, assessments, checkins, risks, reports |
| Add migration tool | High | Done | Alembic baseline added |
| Implement repositories/services | Medium | Done | Conversation and domain repositories added |
| Add pgvector support | Medium | Not started | Keep for future memory retrieval/RAG phase |

### 4. Safety and Evaluation

| Task | Priority | Status | Notes |
| --- | --- | --- | --- |
| Define risk levels and contracts | High | Done | Low/elevated/high/critical scaffold added |
| Post-generation regex: diagnosis language | High | Done | P0-3: sentence-level truncation for diagnosis patterns |
| Post-generation regex: overreach/promise | High | Done | P0-3: sentence-level truncation for treatment promises |
| Post-generation regex: challenge detection | Medium | Done | B2.3: strip confrontational language when challenge_allowed=False |
| Post-generation regex: pathological attribution | High | Not started | Patterns like "brain distortion", "perception is not real" |
| Post-generation regex: subjective-experience denial | High | Not started | Patterns like "what you see doesn't exist", "voices aren't real" |
| Post-generation regex: over-pathologization | High | Not started | Patterns like "this is a hallucination", "this is a delusion" |
| Graceful degradation: transition phrase after truncation | High | Not started | Insert safe transition at truncation point to maintain coherence |
| Graceful degradation: context-aware fallback | High | Not started | Crisis scenario → crisis template; normal → grounding phrase |
| Create risk regression dataset | High | In progress | 17 cases in cases.json; covers basic routing only |
| Expand eval cases: early support scenarios | High | Not started | Psychosis, panic, grief, passive suicidal, somatic, burnout, etc. |
| Build evaluation runner (routing layer) | High | In progress | Checks mode + risk_level; does not check reply text quality |
| Build content-quality evaluation (regex layer) | High | Not started | Assert reply text passes all red-line regex + structure + language |
| Build content-quality evaluation (LLM-as-judge) | Medium | Not started | Different model scores attribution, safety, empathy, action |
| Add Langfuse tracing | Medium | In progress | Top-level span added; per-node spans not yet instrumented |
| Add Langfuse per-node spans | Medium | Not started | risk_classifier, intent_router, response_generator, safety_reviewer |
| Add flush_langfuse on request end | Medium | Not started | Ensure trace data is flushed at end of request lifecycle |
| Create release safety checklist | High | Not started | Required before launch |

### 5. Product Logic

| Task | Priority | Status | Notes |
| --- | --- | --- | --- |
| Implement PHQ-9, GAD-7, ISI schemas | High | Done | Assessment schema and severity service added |
| Implement daily check-in model | Medium | Done | API and persistence added |
| Implement intervention plan templates | Medium | Done | Starter plan service added |
| Implement weekly report generator | Medium | Done | Summary generation and report endpoint added |

## Suggested Execution Order

1. Repository skeleton and dependency setup
2. FastAPI app and config system
3. Pydantic contracts and AI state schemas
4. LangGraph workflow skeleton
5. Risk classifier and crisis routing
6. Persistence layer and migrations
7. Core conversation API
8. Assessments and check-ins
9. Tracing and evaluation harness
10. Weekly summaries and intervention plans

## Definition of Done for MVP Foundation

- FastAPI service starts locally
- Conversation endpoint runs through LangGraph
- Risk classification always executes before normal response generation
- Crisis mode blocks normal support flow when required
- Core entities persist to PostgreSQL
- Basic tracing is visible in Langfuse
- Offline evaluation command can run predefined safety cases

## Immediate Next Actions

- [x] Create repository folders and initial backend app
- [x] Add Python project config and dependencies
- [x] Add base settings module
- [x] Add AI schema contracts
- [x] Add first workflow graph
- [x] Add Alembic migration setup
- [x] Replace scaffolded response generator with LLM-backed adapter
- [x] Add persistent session/message storage
- [x] Add risk evaluation dataset and runner (routing layer)
- [x] Post-generation regex: diagnosis language + overreach + challenge detection
- [x] Language consistency: expected_language detection + enforcement
- [x] Questionnaire answer parsing enhancement (zero-width, BOM, invisible chars)
- [x] Docker deployment with Alibaba Cloud mirror sources
- [ ] Post-generation regex: pathological attribution red lines
- [ ] Post-generation regex: subjective-experience denial red lines
- [ ] Post-generation regex: over-pathologization red lines
- [ ] Graceful degradation: transition phrase + context-aware fallback
- [ ] Expand eval cases: early psychological support scenarios
- [ ] Content-quality evaluation: regex layer (red-line + structure + language)
- [ ] Content-quality evaluation: LLM-as-judge layer
- [ ] Create release safety checklist document
- [ ] Add Langfuse per-node spans + flush on request end
- [ ] Clean up redundant root-level index.html
- [ ] Add richer memory retrieval (P2, not blocking for early support MVP)
- [ ] Add vector memory and knowledge retrieval layer (P2)

## Progress Log

### 2026-04-10

- Created `PROJECT_PLAN.md`
- Created `PROJECT_PROGRESS.md`
- Locked recommended AI framework and first-stage stack
- Recorded execution order for implementation
- Initialized git repository
- Initialized Python project with `uv`
- Added FastAPI app scaffold and core routes
- Added LangGraph workflow scaffold with risk-first routing
- Added settings, DB, cache, queue, and tracing scaffolds
- Added initial assessment, check-in, and plan schema files
- Added basic integration tests
- Added SQLite-backed persistence and repositories
- Added assessment, check-in, plan, and weekly report APIs
- Added Alembic baseline migration files
- Added LLM-backed response generation with safe fallback
- Added API and risk evaluation tests

### 2026-08-22 (dev branch, PR #8/#9/#10/#13)

- P0 safety fixes: fallback bug fix, Alembic migration, diagnosis interception, crisis keyword alignment, mode-based temperature, safety_reviewer diagnosis/overreach detection, high-risk LLM with crisis prompt
- B-line AI enhancement: cross-turn contradiction detection, challenge review, exercise refusal tracking, negation proximity, exhaustion subtypes, focus keywords, structured fallback
- D-line: P0 test coverage, CI/lint integration, Docker mirror source optimization
- Language consistency: expected_language detection from conversation history, _enforce_language tolerance fix, questionnaire answer parsing enhancement
- Docker deployment: Alibaba Cloud mirrors, port 9958, docker-compose with image loading
- Updated PROJECT_PLAN.md: added post-generation regex red lines, graceful degradation, three-layer evaluation architecture, Phase 2.5 safety hardening
- Updated PROJECT_PROGRESS.md: expanded workstream 4, updated next actions with completed and new items
