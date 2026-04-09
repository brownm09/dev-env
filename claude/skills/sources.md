# Source Library

Curated primary sources for Further Reading sections.
Organized by topic tag. Each entry: Title | Author/Org | URL | one-sentence relevance.

Do NOT use secondary summaries, blog posts interpreting these works, or articles without
a named author. Prefer: official documentation, peer-reviewed papers, practitioner-authored
posts on company engineering blogs, and books with canonical URLs.

---

## Architecture Patterns

**tags: hexagonal, ports-adapters, clean-architecture, domain, dependency-inversion**

- **Hexagonal Architecture** | Alistair Cockburn | https://alistair.cockburn.us/hexagonal-architecture/ |
  The original 2005 paper by the pattern's author; defines ports, adapters, and the application boundary — cite when any adapter, port interface, or dependency-inversion decision is made.

- **The Clean Architecture** | Robert C. Martin | https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html |
  Martin's canonical synthesis of hexagonal, onion, and screaming architectures; the concentric-rings diagram; cite when discussing layer boundaries or use-case isolation.

- **Domain-Driven Design reference** | Eric Evans | https://www.domainlanguage.com/ddd/reference/ |
  Evans's free DDD reference card (condensed from the blue book); cite when aggregate, bounded context, or ubiquitous language decisions come up.

- **Strangler Fig Application** | Martin Fowler | https://martinfowler.com/bliki/StranglerFigApplication.html |
  Fowler's pattern for incrementally replacing a legacy system by routing traffic to new implementation; cite for any GAS→cloud-native migration decision.

- **Domain-Oriented Microservice Architecture (DOMA)** | Uber Engineering | https://www.uber.com/blog/microservice-architecture/ |
  Uber's 2020 post describing how they restructured 2,200 microservices into domains, layers, and gateways to reduce coupling at scale; cite for service decomposition or inter-service boundary decisions.

- **Production Readiness Standards** | Stripe Engineering | https://stripe.com/blog/engineering-principles |
  Stripe's public engineering principles, including the "operational excellence" and "design for failure" tenets; cite for reliability or production-hardening decisions.

---

## Testing

**tags: testing, test-pyramid, integration-test, unit-test, contract-test, testcontainers, e2e**

- **The Practical Test Pyramid** | Ham Vocke / Martin Fowler | https://martinfowler.com/articles/practical-test-pyramid.html |
  Fowler's site hosts Vocke's definitive practitioner guide to the test pyramid — unit, integration, and UI layers with cost/speed tradeoffs; cite for any testing strategy or pyramid shape decision.

- **Just Say No to More End-to-End Tests** | Mike Wacker (Google Testing Blog) | https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html |
  Google's case for why the test pyramid inverts in practice and how to fix it; real data from Google projects; cite for E2E scope-limiting decisions.

- **Test Doubles (Mocks, Fakes, Stubs, Spies)** | Martin Fowler | https://martinfowler.com/bliki/TestDouble.html |
  Fowler's taxonomy of test doubles; the authoritative source for distinguishing mock vs. stub vs. fake vs. spy; cite when choosing between in-memory adapters and real infrastructure in tests.

- **Testcontainers guides** | Testcontainers | https://testcontainers.com/guides/ |
  Official guides for using real infrastructure (Postgres, Redis, etc.) in integration tests via Docker containers; cite when choosing Testcontainers for adapter tests over mocks.

- **How Google Tests Software** | James Whittaker, Jason Arbon, Jeff Carollo | https://books.google.com/books/about/How_Google_Tests_Software.html |
  Google's internal testing culture and SWE-in-Test role; context for large-scale test infrastructure decisions.

- **Software Engineering at Google: Testing** | Titus Winters et al. | https://abseil.io/resources/swe-book/html/ch11.html |
  Chapter 11 of the free SE@Google book; covers Google's unit testing philosophy, test size classification (small/medium/large), and test maintainability at scale.

---

## API Design

**tags: api, rest, graphql, grpc, versioning, api-design**

- **Architectural Styles and the Design of Network-based Software Architectures** | Roy Fielding | https://ics.uci.edu/~fielding/pubs/dissertation/top.htm |
  Fielding's 2000 dissertation that defined REST; cite for REST constraint decisions (statelessness, uniform interface, HATEOAS), not just "we chose REST."

- **GraphQL Specification** | GraphQL Foundation | https://spec.graphql.org/October2021/ |
  The October 2021 release of the GraphQL specification; cite for schema design, execution behavior, or introspection decisions.

- **Designing APIs for Humans** | Paul Assman (Stripe Developer Experience) | https://dev.to/stripe/designing-apis-for-humans-object-ids-3o5a |
  Stripe's engineering blog series on API ergonomics from practitioners who run one of the most-cited developer APIs in the industry; cite for API naming, error format, or ID design decisions.

- **API Versioning** | Phil Sturgeon | https://apisyouwonthate.com/blog/api-versioning-has-no-right-way/ |
  Practitioner analysis of versioning strategies (URL path, header, query param) with tradeoffs; not a primary spec but a well-regarded practitioner reference.

---

## Monorepo & Build Systems

**tags: monorepo, turborepo, nx, build, workspace, ci-speed**

- **Why Google Stores Billions of Lines of Code in a Single Repository** | Rachel Potvin, Josh Levenberg | https://dl.acm.org/doi/10.1145/2854146 |
  The 2016 CACM paper by two Google engineers on Piper, Google's internal monorepo — covers scale (1 billion files, 35,000 commits/day), tooling (Bazel), and the cultural tradeoffs; cite for monorepo structure and dependency management decisions.

- **Monorepos in Git** | Atlassian Engineering | https://www.atlassian.com/git/tutorials/monorepos |
  Atlassian's practitioner guide covering sparse checkout, shallow clones, and CI strategies for large monorepos; cite for git performance or workspace configuration decisions.

- **Turborepo documentation: Caching** | Vercel | https://turbo.build/repo/docs/crafting-your-repository/caching |
  Authoritative reference for Turborepo's task caching model (inputs, outputs, remote cache); cite for Turborepo pipeline or cache configuration decisions.

---

## Database & Persistence

**tags: database, orm, prisma, migrations, repository-pattern, cqrs, event-sourcing**

- **Patterns of Enterprise Application Architecture — Repository** | Martin Fowler | https://www.martinfowler.com/eaaCatalog/repository.html |
  Fowler's catalog entry for the Repository pattern; the canonical reference distinguishing Repository from Data Mapper and Active Record; cite when designing repository interfaces or adapter boundaries.

- **Prisma documentation: Data modeling** | Prisma | https://www.prisma.io/docs/orm/prisma-schema/data-model/models |
  Official Prisma schema documentation; cite for Prisma model, relation, or migration decisions.

- **Online migrations at scale** | Jacqueline Xu (Stripe Engineering) | https://stripe.com/blog/online-migrations |
  Stripe's 4-step approach to zero-downtime database migrations (dual writing, backfill, switch reads, clean up) used at production scale; cite for any migration safety or backfill strategy decision.

---

## Engineering Process & Documentation

**tags: adr, rfc, prd, documentation, decision-records, issue-workflow**

- **Documenting Architecture Decisions** | Michael Nygard | https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions |
  The original 2011 post that introduced ADRs — the source of the format and the "why" behind keeping decisions close to code; cite for any ADR process or format decision.

- **RFC process at Rust** | Rust Lang | https://github.com/rust-lang/rfcs |
  The Rust community's RFC (Request for Comments) process; widely cited model for structured pre-implementation design review in open and enterprise settings; cite for RFC or design review process decisions.

- **A Software Development Life Cycle for ML Systems** | Google (PAIR Guidebook) | https://pair.withgoogle.com/guidebook/ |
  Google's People + AI Research guidebook for structured product development involving ML; broader reference for PRD and spec practices at Google scale.

---

## CI/CD & DevOps

**tags: ci, cd, devops, deployment, dora, accelerate, feature-flags, rollback**

- **DORA Research: 2023 State of DevOps Report** | Google Cloud / DORA | https://dora.dev/research/2023/dora-report/ |
  Annual research report measuring the four key DevOps metrics (deployment frequency, lead time, change failure rate, recovery time) across 36,000 professionals; the authoritative benchmark for CI/CD maturity; cite for any deployment pipeline or release strategy decision.

- **Accelerate: The Science of DevOps** | Nicole Forsgren, Jez Humble, Gene Kim | https://itrevolution.com/product/accelerate/ |
  The book behind DORA's research; connects software delivery practices to organizational outcomes with statistical rigor; cite for any "move fast vs. stability" tradeoff or branching strategy decision.

- **Continuous Delivery** | Jez Humble, David Farley | https://continuousdelivery.com/ |
  The canonical book on CD pipelines, deployment patterns, and feature toggles; the authors' site has free chapters; cite for trunk-based development, blue/green, or canary deployment decisions.

- **Ship / Show / Ask** | Rouan Wilsenach (ThoughtWorks) | https://martinfowler.com/articles/ship-show-ask.html |
  ThoughtWorks practitioner's three-tier PR strategy (merge directly, raise for visibility, request review); hosted on Fowler's site; cite for PR workflow or branch strategy decisions.

---

## Observability & Production Engineering

**tags: observability, logging, tracing, metrics, sre, reliability, incident**

- **Google Site Reliability Engineering** | Betsy Beyer et al. | https://sre.google/sre-book/table-of-contents/ |
  Free online book from Google's SRE team; covers error budgets, SLOs, toil, postmortems, and on-call practices from practitioners running Google-scale systems; cite for reliability, on-call, or SLO decisions.

- **Observability Engineering** | Charity Majors, Liz Fong-Jones, George Miranda | https://www.oreilly.com/library/view/observability-engineering/9781492076438/ |
  The definitive practitioner book on structured events, distributed tracing, and high-cardinality observability; Majors co-founded Honeycomb; cite for logging schema, tracing, or debugging strategy decisions.

- **Logging vs. Instrumentation** | Peter Bourgon | https://peter.bourgon.org/blog/2016/02/07/logging-v-instrumentation.html |
  Concise practitioner post distinguishing logs (discrete events) from metrics (aggregated measurements) and when to use each; cite for any logging/metrics architecture decision.

---

## Engineering Management & Team Process

**tags: management, team, productivity, technical-leadership, planning, estimation**

- **An Elegant Puzzle: Systems of Engineering Management** | Will Larson | https://lethain.com/elegant-puzzle/ |
  Larson's book (author was VP Eng at Stripe, then Calm, then Carta); covers team sizing, succession planning, technical debt, and reorgs — practitioner-authored from named enterprise contexts; cite for team process or engineering organization decisions.

- **The Manager's Path** | Camille Fournier | https://www.oreilly.com/library/view/the-managers-path/9781491973882/ |
  Fournier (former CTO of Rent the Runway) maps the engineering career ladder from IC to CTO; cite for career development, mentoring, or role definition decisions.

- **StaffEng: Stories of Reaching Staff Engineer** | Will Larson | https://staffeng.com/stories/ |
  Named practitioner stories from staff+ engineers at companies including Stripe, Dropbox, Fastly, Squarespace; cite for technical leadership or staff-level scope decisions.

---

## Claude Code & AI-Assisted Development

**tags: claude-code, llm, ai-dev, token, hooks, skills, mcp**

- **Claude Code hooks documentation** | Anthropic | https://docs.anthropic.com/en/docs/claude-code/hooks |
  Primary reference for Stop, PreToolUse, PostToolUse, and SubagentStop hook events, stdin payload format, and exit code semantics; cite for any hook implementation decision.

- **Claude Code skills documentation** | Anthropic | https://docs.anthropic.com/en/docs/claude-code/skills |
  Authoritative reference for skill file format, frontmatter schema, `$ARGUMENTS` substitution, `context: fork`, and `allowed-tools`; cite for skill design decisions.

- **Anthropic API pricing** | Anthropic | https://www.anthropic.com/pricing |
  Per-token rates for all Claude models including cache read/write pricing; cite whenever token cost estimates or cache economics decisions are discussed.

- **Prompt caching (beta)** | Anthropic | https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching |
  Official documentation on how prompt caching works, what qualifies as a cache hit, and the 5-minute TTL; cite for any token optimization or cache strategy decision.
