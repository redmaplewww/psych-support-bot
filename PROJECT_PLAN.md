# AI Psychological Support Bot Project Plan

## 1. Project Overview

- Project name: AI Psychological Support Bot
- Product type: Digital mental health support system for users with mild-to-moderate symptoms or early warning signs
- Primary goal: Deliver structured psychological support, symptom screening, guided self-help interventions, and strong safety triage
- Product boundary: The system does not provide diagnosis, emergency intervention, or replacement for psychiatrists or licensed therapists

## 2. Target Users

### Included users

- Users with mild-to-moderate anxiety, depressed mood, insomnia, panic-related distress, emotional dysregulation, or early warning signs of psychological illness
- Users with prior diagnosis but currently stable and seeking structured support, check-ins, and self-help tools

### Excluded from normal flow

- Users with active suicidal intent, self-harm plans, homicidal intent, psychotic symptoms, mania with severe impairment, or other complex/high-risk presentations

### High-risk handling principle

- Provide brief stabilization support only
- Immediately recommend medical care, crisis resources, and real-world support contacts
- Do not continue normal counseling-style deep conversation

## 3. Product Positioning

- External positioning: AI psychological support and self-help intervention assistant
- Internal positioning: Workflow-driven mental health support platform with screening, structured conversation, intervention plans, tracking, and risk routing
- Core value:
  - Early identification of symptom patterns
  - Structured support inspired by counseling flow
  - Measurable symptom improvement support
  - Strong safety boundaries and crisis routing

## 4. Recommended AI Development Framework

### Core framework decision

- Primary AI workflow framework: `LangGraph`
- API service framework: `FastAPI`
- Structured data and validation: `Pydantic`
- Primary database: `PostgreSQL`
- Vector search for RAG and memory retrieval: `pgvector` for MVP, optional `Qdrant` later
- Cache and session support: `Redis`
- Async jobs: `Celery`
- Observability and prompt tracing: `Langfuse`

### Why this stack fits this product

- The product is workflow-heavy, not agent-first
- Safety routing and state transitions must be explicit and testable
- Risk assessment, intervention selection, response generation, and summarization should be independently observable
- Structured outputs are required for safe routing and auditable behavior

### Development principles

- Workflow first
- Safety first
- Structured output first
- Human-readable state transitions
- Evaluation before scale

## 5. High-Level System Architecture

### User-facing layers

- Mobile app as the primary launch platform
- Web landing page for information and onboarding
- Internal admin/ops panel later for reviewing risk events, content quality, and analytics

### Backend services

- Auth and user profile service
- Conversation service
- Screening and assessment service
- Intervention plan service
- Check-in and progress tracking service
- Report generation service
- Crisis resource and routing service

### AI orchestration layer

- Input preprocessing node
- Risk classifier node
- Intent router node
- Memory retrieval node
- Tool selector node
- Response generation node
- Safety reviewer node
- Summary and memory writer node

### Data layer

- Relational data in PostgreSQL
- Semantic retrieval via pgvector
- Transient state and caching in Redis
- Async event processing via Celery workers

## 6. AI Workflow Design

### Primary conversation flow

1. Receive user message
2. Load user state, memory summary, and recent risk flags
3. Run risk classification first
4. If high risk, route to crisis mode immediately
5. If not high risk, classify conversation intent
6. Select conversation strategy
7. Retrieve relevant memory and optional knowledge/tool context
8. Generate draft response with the main LLM
9. Run safety and boundary review
10. Return response to user
11. Asynchronously save summary, memory updates, and analytics

### Conversation modes

- `support_mode`: emotional support, reflective listening, light structure
- `assessment_mode`: screening-oriented conversation and symptom clarification
- `intervention_mode`: CBT/ACT/DBT/behavioral activation guidance
- `planning_mode`: next steps, daily tasks, and short-term goals
- `crisis_mode`: immediate stabilization and referral guidance

## 7. Psychological Method Stack

### Primary methods for MVP

- CBT for thought patterns, symptom maintenance, and structured exercises
- ACT for acceptance, cognitive defusion, and values-oriented action
- DBT skills for distress tolerance and emotion regulation
- Behavioral activation for low motivation and depressive slowing

### Delivery format

- Strategy cards per method
- Trigger conditions for using each method
- Contraindications and escalation boundaries
- Reusable exercise templates
- Structured conversation prompts by mode

## 8. Safety and Risk Framework

### Risk levels

- `L0`: no evident risk
- `L1`: distress present, no acute danger
- `L2`: elevated concern, more assessment needed
- `L3`: high risk, normal flow blocked
- `L4`: emergency-risk indicators, immediate urgent guidance

### High-risk triggers

- Suicide or self-harm statements
- Homicidal intent
- Hallucination or delusion indicators
- Mania-like behavior with severe sleep loss or impaired judgment
- Loss of control with strong danger cues

### Crisis handling rules

- Short, direct, stabilizing language only
- Encourage immediate real-world support contact
- Recommend hospital, emergency services, or crisis resources
- Do not continue exploratory counseling flow

### Post-generation safety review (regex red lines)

The safety reviewer node applies hard regex constraints on LLM output, independent of prompt rules:

- **Diagnosis language**: patterns that state or imply a specific diagnosis (e.g., "you have depression")
- **Overreach/promise**: patterns that promise treatment, cure, or prescribe medication
- **Challenge/confrontation**: confrontational or probing questions when challenge_allowed is False
- **Pathological attribution**: patterns that explain user's experience as distorted perception or brain malfunction (e.g., "brain distortion", "your perception is not real")
- **Subjective-experience denial**: patterns that negate the user's perceived reality (e.g., "what you see doesn't exist", "the voices aren't real")
- **Over-pathologization**: patterns that label user's experience with clinical terminology (e.g., "this is a hallucination", "this is a delusion")

### Graceful degradation after red-line truncation

When the safety reviewer removes violating sentences:

1. Insert a safe transition phrase at the truncation point to maintain coherence
2. If the remaining text is empty, use a context-aware fallback:
   - Crisis/high-risk scenario: crisis resource template
   - Normal scenario: supportive grounding phrase
3. Log the truncation event for evaluation and prompt improvement

## 9. Product Scope for MVP

### Core symptom focus

- Anxiety
- Low mood / early depressive symptoms
- Insomnia and sleep disruption
- Panic-related distress

### MVP features

- Onboarding with boundary and safety agreement
- Risk screening
- Symptom screening with standard scales
- AI support conversation
- Daily check-in
- Structured intervention plans
- Weekly summary report
- Crisis mode and crisis resources page
- Long-term memory with user-visible control

## 10. Product Functional Modules

### Onboarding

- Product boundary explanation
- Privacy and consent
- Rapid safety screen
- Initial symptom screen
- Personalized path recommendation

### Conversation

- Support mode
- Clarify-the-problem mode
- Exercise mode
- Planning mode
- Crisis mode

### Assessments

- PHQ-9
- GAD-7
- ISI
- Optional future additions: PSS, panic screener, OCD screener

### Intervention plans

- 7-day stabilization plan
- 14-day anxiety relief plan
- 14-day sleep improvement plan
- 14-day low-mood activation plan

### Tracking

- Daily mood check-in
- Sleep tracking
- Energy score
- Task completion tracking
- Trend summaries

### Reporting

- Weekly trends
- Trigger summary
- Effective coping methods
- Recommended next-step actions

## 11. Suggested Repository Structure

```text
psych-bot/
  apps/
    api/
    worker/
    admin/
  core/
    ai/
      graphs/
      prompts/
      routers/
      tools/
      safety/
      schemas/
    domain/
      users/
      conversations/
      assessments/
      interventions/
      reports/
      risks/
    infra/
      db/
      cache/
      queue/
      llm/
      telemetry/
  docs/
    product/
    technical/
    safety/
  tests/
    unit/
    integration/
    evals/
```

## 12. Recommended Initial Technical Modules

### AI modules

- `risk_classifier.py`
- `intent_router.py`
- `memory_retriever.py`
- `tool_selector.py`
- `response_generator.py`
- `safety_reviewer.py`
- `summary_writer.py`

### Domain modules

- users
- profiles
- sessions
- messages
- memories
- assessments
- checkins
- plans
- risk_events

### Ops modules

- tracing
- prompt versioning
- evaluation runner
- analytics logger

## 13. Data Model Overview

### Primary entities

- `users`
- `profiles`
- `sessions`
- `messages`
- `memories`
- `assessments`
- `checkins`
- `plans`
- `risk_events`
- `weekly_reports`

### Required design rules

- Keep sensitive data minimal
- Store structured summaries rather than unnecessary raw trauma detail
- Add clear deletion and user-control mechanisms
- Separate safety-critical events for auditing

## 14. Evaluation Strategy

### Offline evaluation before launch

- Empathy quality
- Core problem identification
- Intervention appropriateness
- Actionability of suggestions
- Safety compliance
- High-risk recall performance

### Evaluation architecture (three layers)

**Layer 1: Regex/rule-based evaluation (deterministic, CI-required)**

- Routing correctness: mode + risk_level assertions on predefined cases
- Red-line compliance: output must not match any violation regex
- Structure compliance: three-part labels (Reflection / Working hypothesis / Next question) present
- Language consistency: output language matches expected language

**Layer 2: LLM-as-judge evaluation (semantic, CI or manual)**

- Attribution reasonableness: working hypothesis is non-diagnostic and tentative
- Safety boundary: output does not deny subjective experience or over-pathologize
- Empathy quality: reflection genuinely addresses the user's emotional state
- Action appropriateness: next question moves the process forward without stacking
- Scoring: pass/fail + 0-5 score + reason for each dimension
- Judge model must differ from generation model to avoid same-source bias

**Layer 3: Human evaluation (pre-release / periodic)**

- Full conversation flow quality
- Edge case judgment
- LLM judge calibration validation

### Scenario coverage

- Mild anxiety
- Moderate anxiety with insomnia
- Low mood and self-criticism
- Panic-related fear
- Compulsive rumination
- Self-harm hints
- Explicit suicide expression
- Psychosis-like cues (persecution beliefs, hallucination descriptions)
- Mania-like cues
- Passive suicidal ideation (e.g., "better off dead")
- Grief and loss (non-pathologizing validation)
- Diagnosis request (e.g., "am I depressed?")
- Pure numeric input in questionnaire (language consistency)
- Exercise refusal (respect user decline)
- Somatic complaint with anxiety overlap (e.g., chest tightness)
- Work burnout (normalization vs pathologization)
- Relationship distress (no personality disorder attribution)

### Launch rule

- High-risk recall has priority over conversational elegance
- Any flow that misses obvious crisis signals should block release
- Any output that contains pathological attribution or subjective-experience denial should block release

## 15. Phase Roadmap

### Phase 0: Planning and foundation

- Finalize scope and safety boundary
- Define system architecture
- Define initial data model
- Define prompt and workflow contracts

### Phase 1: MVP backend

- Build FastAPI service
- Build LangGraph workflow
- Implement risk routing
- Implement core conversation flow
- Implement assessments and check-ins

### Phase 2: Product usability

- Add intervention plans
- Add weekly summaries
- Add memory controls
- Add observability and evaluation dashboard support

### Phase 3: Optimization

- Prompt tuning
- Strategy routing optimization
- Additional symptom packs
- Better personalization and relapse warnings

### Phase 2.5: Safety hardening and evaluation infrastructure

- Complete post-generation regex red lines (pathological attribution, subjective-experience denial, over-pathologization)
- Implement graceful degradation with transition phrases and context-aware fallback
- Expand evaluation case set to cover early psychological support scenarios
- Implement content-quality evaluation (regex layer + LLM-as-judge layer)
- Create release safety checklist document
- Add Langfuse span instrumentation on individual graph nodes
- Clean up redundant root-level index.html (use static/index.html only)

## 16. Immediate Build Priorities

1. Initialize repository structure
2. Set up FastAPI app and configuration management
3. Define Pydantic schemas for risk, routing, memory, and responses
4. Build first LangGraph conversation workflow
5. Implement risk classifier contract and placeholder adapters
6. Add PostgreSQL models and migrations
7. Add Redis and Celery integration
8. Add tracing with Langfuse
9. Create evaluation dataset format
10. Build first end-to-end conversation API

## 17. Non-Negotiable Rules for Future Work

- Never let standard support flow bypass risk classification
- Never allow high-risk users to remain in normal intervention mode
- Never rely on a single unstructured prompt for safety decisions
- Every critical AI step must have structured outputs
- Every release must pass safety regression tests
