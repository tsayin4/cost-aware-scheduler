# Cost-Aware Task Scheduler

> Intelligent async task scheduler that balances priority with resource budget constraints

A production-focused task scheduler that makes economic trade-offs between task urgency and resource costs. Unlike traditional priority queues that only consider "when" or "how important," this scheduler asks: **"Can we afford to run this now?"**

## TL;DR

This scheduler makes one core trade-off explicit:

> Not every task that is important can be afforded right now.

It balances:
- Task priority (urgency)
- Resource cost (budget)
- Waiting time (starvation prevention)

to keep systems productive under budget constraints.

**Designed for production environments where API quotas, cloud spend, and mixed workloads coexist.**

## Why This Exists

Traditional async task schedulers prioritize by:
- Time (FIFO)
- Priority level (Critical > Normal > Low)
- Resource availability (blocking if busy)

But they ignore a critical production reality: **resources cost money**.

### The Real-World Problem

While building CIAI , I encountered this scenario:

- **API quota limits:** 1000 calls/minute to external services
- **Compute budget:** Fixed monthly cloud spend
- **Mixed workload:** User requests (urgent) + analytics (can wait) + cleanup (best-effort)

Traditional schedulers would:
1. Execute high-priority analytics job (expensive: 50 API calls)
2. Starve user requests waiting in queue
3. Hit API rate limit
4. Fail user-facing requests

**We needed:** Priority-aware **AND** cost-aware scheduling.

This scheduler emerged from that production constraint.

## Quick Start
```python
import asyncio
from scheduler import (
    CostAwareScheduler,
    ResourceBudget,
    TaskCost,
    TaskPriority
)

# Configure resource budget
budget = ResourceBudget(
    api_calls_per_minute=60,
    compute_units=100.0,
    memory_mb=512
)

scheduler = CostAwareScheduler(budget=budget, name="production")
await scheduler.start()

# Schedule tasks with cost awareness
await scheduler.schedule(
    expensive_api_call,
    priority=TaskPriority.CRITICAL,
    cost=TaskCost(api_calls=10, compute_units=5.0)
)

await scheduler.schedule(
    cheap_background_task,
    priority=TaskPriority.LOW,
    cost=TaskCost(api_calls=0, compute_units=1.0)
)

# Execute tasks (balances priority vs cost)
await scheduler.execute_next()  # Likely runs CRITICAL first

# But if budget exhausted, might skip expensive task
# and run cheap task instead (prevents starvation)

await scheduler.stop()
```

## Key Design Decisions

### Decision 1: Weighted Priority Score (Not Pure Priority)

**Problem:** Should we always execute highest priority task, even if it's expensive?

**Chosen:** Priority score = f(priority, cost, age)

**Why:**
- Pure priority ignores resource efficiency
- Expensive high-priority task might starve cheap critical tasks
- Age factor prevents indefinite starvation

**Alternative Considered:** Strict priority queue
- Simpler implementation
- But can't handle budget constraints intelligently
- Real production systems need cost awareness

**Trade-off:**
- ✅ More production-realistic scheduling
- ❌ Slightly more complex priority calculation
- ✅ Prevents resource exhaustion

**Implementation:**
```python
def _calculate_score(self) -> float:
    base_priority = self.priority.value  # 1-4
    cost_factor = self.compute_units + (self.api_calls * 0.5)
    age_factor = (time.time() - self.created_at) / 60.0
    
    # Lower score = higher priority
    score = (base_priority * 10.0) + (cost_factor * 0.1) - (age_factor * 0.5)
    return score
```

**Why these weights?**
- `base_priority * 10`: Priority dominates (as it should)
- `cost_factor * 0.1`: Cost influences but doesn't override
- `age_factor * 0.5`: Prevents starvation (accumulates over minutes)

**Important:** These weights are intentionally heuristic, not mathematically optimal. In production, they should be tuned based on real cost data, latency requirements, and business priorities. The current values represent reasonable defaults from CIAI's workload distribution.

**At 10x scale:** Tune weights based on actual cost data

### Decision 2: Token Bucket Rate Limiting (Not Hard Limit)

**Problem:** How to enforce API rate limits without rejecting bursty traffic?

**Chosen:** Token bucket with continuous refill

**Why:**
- Allows burst capacity (e.g., 10 calls in 1 second, then wait)
- Smooth refill prevents "thundering herd" at minute boundary
- More user-friendly than hard cutoff

**Alternative Considered:** Simple counter reset every minute
- Easier to implement
- But all requests at 0:59 succeed, all at 1:01 fail (poor UX)
- Doesn't handle burst gracefully

**Trade-off:**
- ✅ Better UX for bursty workloads
- ❌ Slightly more code (token refill background task)
- ✅ Industry-standard approach (used by AWS, GCP)

**Implementation:**
```python
# Refill tokens smoothly (per second, not per minute)
tokens_to_add = (api_calls_per_minute / 60.0) * elapsed_seconds
self._api_tokens = min(tokens + tokens_to_add, max_tokens)
```

### Decision 3: Proactive Admission Control (Reject Early vs Fail Late)

**Problem:** Should we queue all tasks and fail during execution, or reject upfront?

**Chosen:** Optional admission control with `reject_if_no_budget` flag

**Why:**
- Fast feedback to caller (fail-fast principle)
- Prevents unbounded queue growth
- Caller can implement fallback logic

**Alternative Considered:** Queue everything, fail during execution
- Simpler scheduler code
- But wastes memory on tasks that can't run
- Delayed feedback to application

**Trade-off:**
- ✅ Fast failure detection
- ❌ Some tasks rejected that might've fit later (after refill)
- ✅ Prevents memory exhaustion

**Usage:**
```python
# Strict admission (production user requests)
try:
    await scheduler.schedule(
        critical_user_request,
        reject_if_no_budget=True
    )
except BudgetExhaustedError:
    return cached_response()  # Immediate fallback

# Lenient admission (background tasks)
await scheduler.schedule(
    analytics_job,
    reject_if_no_budget=False  # Queue and wait for budget
)
```

### Decision 4: Check Top N Tasks (Not Just First)

**Problem:** What if highest-priority task is expensive but there's a cheap task below it?

**Chosen:** Check top 5 tasks, execute first affordable one

**Why:**
- Prevents head-of-line blocking
- Keeps system productive even under budget pressure
- Balances priority with pragmatism

**Alternative Considered:** Strictly process queue head-to-tail
- Simpler code
- But single expensive task blocks entire queue
- Poor resource utilization

**Trade-off:**
- ✅ Better resource utilization
- ❌ Might execute lower-priority task if higher one is expensive
- ✅ But age factor in scoring mitigates priority inversion

**Why 5?**
- Empirically tested: Checking 10+ provides minimal benefit
- 5 gives good balance vs overhead
- Prevents scanning entire queue (O(n) problem)

## Edge Cases Handled

### 1. Budget Exhaustion Mid-Execution

**Scenario:** Task starts with sufficient budget, but takes longer than expected

**Handling:**
- Budget consumed at task **start** (optimistic)
- Released at task **end** (or failure)
- If task exceeds estimate: Next task sees reduced budget

**Why not pessimistic (block full estimate)?**
- Would be too conservative
- Most tasks finish faster than estimate
- Production systems need optimistic concurrency

**Mitigation:**
```python
# Add buffer to cost estimates
estimated_cost = TaskCost(
    api_calls=5,
    compute_units=actual_cost * 1.2  # 20% buffer
)
```

### 2. Starvation of Low-Priority Tasks

**Scenario:** Continuous stream of high-priority tasks prevents low-priority execution

**Handling:**
- Age factor in priority score: `- (age_in_minutes * 0.5)`
- Task that waits 20 minutes gains 10 priority points
- Eventually outranks newer high-priority tasks

**Detection:**
```python
metrics = scheduler.get_metrics()
if metrics['tasks_deferred'] > 100:
    logger.warning("Possible starvation: %d deferrals", metrics['tasks_deferred'])
```

### 3. Token Refill During Task Execution

**Scenario:** Task executes for 10 seconds, tokens refill meanwhile

**Handling:**
- Background refill task runs independently
- Tokens available for next task immediately
- No need to wait for current task completion

**Alternative:** Refill only between tasks
- Simpler but less efficient
- Current approach maximizes throughput

## What Can Go Wrong

### Failure Mode 1: Underestimated Task Costs

**What Happens:**
- Tasks consume more resources than declared in `TaskCost`
- Budget exhausted faster than expected
- Later tasks starved even though "budget remains"

**Detection:**
- Monitor actual vs estimated cost
- `metrics['total_cost_spent']` diverges from budget

**Mitigation:**
```python
# Add instrumentation to measure actual cost
async def instrumented_task():
    start_tokens = scheduler._api_tokens
    result = await actual_task()
    consumed = start_tokens - scheduler._api_tokens
    
    if consumed > estimated_cost * 1.5:
        logger.warning("Task exceeded estimate: %d vs %d", 
                      consumed, estimated_cost)
```

### Failure Mode 2: Priority Inversion Under Budget Pressure

**What Happens:**
- High-priority expensive task queued
- Low-priority cheap tasks execute instead
- User-facing requests starved

**Detection:**
- Monitor `get_queue_status()` for old CRITICAL tasks
- Alert if CRITICAL task waits >30 seconds

**Mitigation:**
- Tune priority score weights (increase base_priority multiplier)
- Reserve minimum budget for CRITICAL tasks:
```python
# Reserve 20% budget for CRITICAL
if task.priority == TaskPriority.CRITICAL:
    reserved_budget = budget * 0.2
    can_execute = (budget_remaining >= task_cost) or (task_cost <= reserved_budget)
```

### Failure Mode 3: Thundering Herd After Budget Refill

**What Happens:**
- Budget exhausted, many tasks queued
- Minute boundary: Budget refills to 60 calls
- All queued tasks try to execute simultaneously
- Overwhelm downstream services

**Detection:**
- Spike in concurrent executions
- Downstream service errors

**Mitigation:**
- Token bucket smooths refill (already implemented)
- Add execution rate limiting:
```python
# Limit concurrent executions
self._max_concurrent = 10
self._current_executing = 0

# In execute_next():
if self._current_executing >= self._max_concurrent:
    return None  # Defer even if budget available
```

## Design Philosophy

**Design note:** [Why this scheduler is intentionally NOT distributed](docs/why-not-distributed.md)

This architectural choice reflects a deliberate prioritization of correctness and debuggability over premature scalability.

## At Scale / Production Considerations

### Current Implementation (Single Process)

**Suitable for:**
- Single-instance applications
- Microservices with local task management
- Development/testing

**State:** In-memory, process-local

**Limitations:**
- No shared state across processes
- Restarts lose queue
- Cannot distribute load

### At 10x Scale (Multi-Process)

**Changes needed:**
- Distributed queue (Redis/RabbitMQ)
- Centralized budget tracking
- Leader election for token refill

**Implementation sketch:**
```python
class DistributedScheduler(CostAwareScheduler):
    def __init__(self, redis_client, *args, **kwargs):
        self.redis = redis_client
    
    async def _can_afford(self, cost):
        # Atomic budget check via Redis Lua script
        return await self.redis.eval(budget_check_script, cost)
    
    async def execute_next(self):
        # Pop from distributed priority queue
        task = await self.redis.zpopmin("tasks")
```

**Why not implemented now:**
- Adds Redis dependency (violates zero-dependency goal)
- Most use cases don't need distributed scheduling
- Easy to extend when scale demands it

### At 100x Scale (Global Distribution)

**Changes needed:**
- Regional schedulers with local budgets
- Global budget aggregation
- Cross-region task migration for load balancing

**Architecture:**
```
Region US:  Local Scheduler (budget: 60/min)
Region EU:  Local Scheduler (budget: 60/min)
Region APAC: Local Scheduler (budget: 60/min)
            ↓
    Central Budget Manager
    (global limit: 180/min)
            ↓
    Metrics Aggregation (Prometheus)
```

**Why not now:**
- Premature optimization
- Current design allows easy migration (budget/queue encapsulated)
- Regional budgets can be provisioned independently

### Production Deployment Checklist

**Before production:**
- [ ] Tune priority score weights based on actual workload
- [ ] Add metrics export (Prometheus/Datadog)
- [ ] Implement dead-letter queue for rejected tasks
- [ ] Set up alerting on starvation metrics
- [ ] Load test with realistic cost distribution
- [ ] Add task timeout enforcement
- [ ] Implement graceful shutdown (drain queue)

## Running the Examples
```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/cost-aware-scheduler.git
cd cost-aware-scheduler

# Run demonstrations
python3 example.py
```

The examples demonstrate:
1. Priority-based scheduling
2. Budget-constrained behavior
3. Cost vs priority trade-offs
4. Admission control policies
5. Realistic mixed workload

## Use Cases

### 1. API Gateway with Rate Limits
Schedule requests to third-party APIs while respecting rate limits

### 2. Cloud Cost Optimization
Defer batch jobs when compute budget low, prioritize user requests

### 3. Multi-Tenant Systems
Fair resource allocation across tenants with different quotas

### 4. Microservice Communication
Prevent cascade failures by throttling based on downstream capacity

## Metrics

Access real-time metrics:
```python
metrics = scheduler.get_metrics()
print(metrics)
# {
#     'name': 'production',
#     'queue_size': 12,
#     'tasks_queued': 150,
#     'tasks_executed': 138,
#     'tasks_rejected': 5,
#     'tasks_deferred': 42,
#     'current_usage': {'api_calls': 8, 'compute_units': 23.5, ...},
#     'total_cost_spent': {'api_calls': 892.0, 'compute_units': 1250.3, ...},
#     'api_tokens_available': 12.5
# }
```

## Key Questions This Project Answers

### "Why not maximize throughput?"

**Answer:** Because resources cost money.

Running 1000 requests/sec might be possible technically, but if:
- Each request costs $0.01 in API calls
- Monthly budget is $10,000
- That's 1M requests/month max

Throughput must respect budget. This scheduler enforces that.

### "Why is fairness sometimes bad?"

**Answer:** Because not all tasks are equal.

Fair scheduling (round-robin) treats:
- User login request
- Background analytics job

...identically. But user login is **time-sensitive**, analytics can wait.

This scheduler prioritizes based on **business value**, not fairness.

### "What happens when budget hits zero?"

**Answer:** System gracefully degrades.

Traditional scheduler: Keeps executing, accumulates debt, gets bill shock

This scheduler:
1. Stops expensive tasks
2. Queues them for later
3. Executes cheap tasks (keeps system productive)
4. Refills budget gradually
5. Resumes expensive tasks

## Related Work

**Inspired by:**
- AWS Lambda throttling (concurrency limits + cost awareness)
- Kubernetes priority classes (resource quotas)
- Token bucket algorithm (RFC 6040)

**Differs from existing Python schedulers:**
- `APScheduler`: Time-based only, no cost awareness
- `Celery`: Task queue, no priority+cost balancing
- `asyncio.Queue`: FIFO only, no resource constraints

**Motivated by:**
- Production experience with CIAI (10K+ TPS transaction processing)
- Research into async task management patterns
- Contributions to FastAPI and aiohttp (background task lifecycle)

## Testing
```bash
# Run example demonstrations
python3 example.py

# Expected output: All 5 demos complete successfully
```

## Future Enhancements

**Potential additions (not implemented to keep scope minimal):**

- [ ] Metrics export (Prometheus format)
- [ ] Distributed state backend (Redis)
- [ ] Adaptive weight tuning (ML-based)
- [ ] Task dependency graphs
- [ ] Persistent queue (survive restarts)

**Why not included:**
- Keep implementation focused
- Avoid dependency bloat
- Easy to extend when specific needs arise

## License

MIT

## Author

Built from production experience managing resource constraints in high-throughput distributed systems.

Motivated by systematic research into async task management and cost optimization in cloud environments.

**Note on Scope:** This project is not intended as a drop-in replacement for Celery or APScheduler, but as a reference implementation demonstrating cost-aware scheduling decisions. It prioritizes decision transparency and educational value over feature completeness.

---

**Note:** This is a focused implementation prioritizing decision-making transparency over feature completeness. The ~400 lines of code are intentionally minimal to remain auditable and maintainable.
