# Why This Scheduler Is Intentionally NOT Distributed

A common first reaction to the cost-aware scheduler is: "This should use Redis for distributed state." At first glance, this seems like the "production-ready" approach.

I explored this path carefully and deliberately chose not to implement it.

## What I Considered

During development, I investigated several distributed architectures:

- **Redis-backed shared state:** Global budget tracking across processes
- **Distributed token buckets:** Cross-process rate limiting coordination  
- **Leader election:** Single coordinator for budget refills
- **Message queues:** Kafka/RabbitMQ for task distribution

Each has production-grade implementations in well-known systems (Celery, Temporal, etc.).

## Why I Explicitly Did NOT Implement It

I decided against distributed architecture for three technical reasons:

### 1. False Sense of Correctness

Distributed state creates the *illusion* of correctness while introducing new failure modes:

- **Network partitions:** What happens when Redis is unreachable?
- **Clock skew:** Token bucket refills depend on synchronized time
- **Stale reads:** Budget checks may see outdated state
- **Split brain:** Two nodes think they're the coordinator

The original problem (budget enforcement) gets replaced with harder problems (distributed consensus).

### 2. Increased Failure Surface

Single-process scheduler failure modes:
- Process crashes (observable, restart solves it)
- Memory exhaustion (detectable, bounded)

Distributed scheduler failure modes:
- All of the above, plus:
- Network failures (transient, hard to detect)
- Distributed state inconsistency (silent, hard to debug)
- Cross-process race conditions (non-deterministic)
- Cascading failures across nodes

**The failure surface grew significantly while solving a problem I didn't yet have.**

### 3. Premature Coupling to Infrastructure

Distributed design forces infrastructure choices before understanding the problem:

- Which Redis? Cluster? Sentinel? 
- Which message queue? Kafka? RabbitMQ? SQS?
- How to handle partial failures?
- What's the recovery strategy?

These are valid production questions, but they're *downstream* of understanding single-process correctness first.

## The Key Insight

**A single-process system that fails transparently is often more reliable than a distributed system that fails opaquely under partial failure.**

When my scheduler crashes, it's obvious: tasks stop executing. Fix it, restart, continue.

When a distributed scheduler has a split-brain scenario, tasks might execute twice, budgets might be double-counted, and the failure is *silent until money is wasted*.

**Correctness before scalability.**

## When I WOULD Implement Distribution

I would move to distributed architecture only when *all three* conditions are met:

1. **Proven single-process bottleneck**
   - Not theoretical: "this could scale"
   - Actual: "this is the limiting factor in production"

2. **State must survive process restarts**
   - Task queue needs persistence
   - Budget tracking needs durability
   - Cost of losing state exceeds cost of coordination

3. **Global budget enforcement is required**
   - Multiple processes consuming shared budget
   - Budget limit must be enforced across all processes
   - Failure cost (overspending) exceeds coordination cost

Currently, none of these conditions are met. The system handles expected load, state loss is acceptable (tasks can be rescheduled), and single-process budget enforcement is sufficient.

## What This Design Enables

By keeping it single-process, I can:

- **Test deterministically:** No race conditions, no network flakiness
- **Debug easily:** Single stack trace, single log file
- **Verify correctness:** Prove budget logic without distributed systems complexity
- **Iterate quickly:** No infrastructure dependencies

Once correctness is proven at single-process scale, distribution becomes a *deployment strategy*, not a *correctness requirement*.

## Lessons for Production Systems

This reflects a broader principle I've learned building CIAI (10K+ TPS transaction system):

**Start with the simplest thing that could possibly work. Add complexity only when the cost of NOT adding it exceeds the cost of the complexity itself.**

Distributed systems aren't inherently better. They're a tool for a specific problem: coordination across independent failure domains.

If your problem doesn't require that coordination, you're paying distributed systems complexity tax for no benefit.

## References

- CIAI: Learned this lesson by initially over-engineering for "future scale"
- async-exception-semantics: Single-process async is already hard; distribution multiplies that
- Production debugging: Distributed failures are 10x harder to diagnose than local failures

---

**This note documents a deliberate architectural choice, not a limitation.**

When the problem demands distribution, I'll implement it. Until then, correctness first.