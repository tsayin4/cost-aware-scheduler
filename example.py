"""
Example usage of CostAwareScheduler
Demonstrates resource-aware task scheduling and budget management
"""

import asyncio
import random
from scheduler import (
    CostAwareScheduler,
    ResourceBudget,
    TaskCost,
    TaskPriority,
    BudgetExhaustedError,
)


# Simulated tasks with different costs
async def api_call_task(task_id: str, api_name: str) -> str:
    """Simulate an API call (high API cost, low compute)"""
    await asyncio.sleep(0.2)
    return f"Task {task_id}: Called {api_name} API"


async def compute_task(task_id: str, complexity: str) -> str:
    """Simulate compute-heavy task (low API cost, high compute)"""
    await asyncio.sleep(0.5)
    return f"Task {task_id}: Completed {complexity} computation"


async def memory_task(task_id: str, data_size: str) -> str:
    """Simulate memory-intensive task"""
    await asyncio.sleep(0.3)
    return f"Task {task_id}: Processed {data_size} data"


async def demo_basic_scheduling():
    """Demo 1: Basic priority-based scheduling"""
    print("\n" + "="*60)
    print("DEMO 1: Basic Priority-Based Scheduling")
    print("="*60)
    
    # Configure generous budget
    budget = ResourceBudget(
        api_calls_per_minute=100,
        compute_units=50.0,
        memory_mb=256
    )
    
    scheduler = CostAwareScheduler(budget=budget, name="demo1")
    await scheduler.start()
    
    print("\nScheduling tasks with different priorities...")
    
    # Schedule tasks (different priorities)
    tasks = [
        ("LOW priority task", TaskPriority.LOW, TaskCost(api_calls=1, compute_units=1.0)),
        ("CRITICAL priority task", TaskPriority.CRITICAL, TaskCost(api_calls=1, compute_units=1.0)),
        ("NORMAL priority task", TaskPriority.NORMAL, TaskCost(api_calls=1, compute_units=1.0)),
        ("HIGH priority task", TaskPriority.HIGH, TaskCost(api_calls=1, compute_units=1.0)),
    ]
    
    for desc, priority, cost in tasks:
        task_id = await scheduler.schedule(
            api_call_task,
            priority=priority,
            cost=cost,
            task_id=desc,
            api_name="demo"
        )
        print(f"  Scheduled: {desc}")
    
    print("\nQueue status (sorted by priority):")
    for task in scheduler.get_queue_status():
        print(f"  [{task['priority']}] Age: {task['age_seconds']:.1f}s | "
              f"Cost: {task['cost']['api_calls']} API calls")
    
    print("\nExecuting tasks (should execute CRITICAL → HIGH → NORMAL → LOW):")
    for i in range(4):
        result = await scheduler.execute_next()
        if result:
            print(f"  {i+1}. {result}")
    
    metrics = scheduler.get_metrics()
    print(f"\nMetrics: {metrics['tasks_executed']} executed, "
          f"{metrics['tasks_deferred']} deferred")
    
    await scheduler.stop()


async def demo_budget_constraints():
    """Demo 2: Behavior under tight budget constraints"""
    print("\n" + "="*60)
    print("DEMO 2: Budget-Constrained Scheduling")
    print("="*60)
    
    # Configure TIGHT budget
    budget = ResourceBudget(
        api_calls_per_minute=10,  # Very limited
        compute_units=20.0,
        memory_mb=100
    )
    
    scheduler = CostAwareScheduler(budget=budget, name="demo2")
    await scheduler.start()
    
    print(f"\nTight budget: {budget.api_calls_per_minute} API calls/min, "
          f"{budget.compute_units} compute units")
    
    # Schedule expensive tasks
    print("\nScheduling 5 expensive tasks...")
    for i in range(5):
        await scheduler.schedule(
            compute_task,
            priority=TaskPriority.NORMAL,
            cost=TaskCost(api_calls=2, compute_units=8.0),
            task_id=f"expensive-{i}",
            complexity="heavy"
        )
    
    print(f"Queue size: {len(scheduler.get_queue_status())} tasks")
    
    print("\nExecuting with budget constraints:")
    executed_count = 0
    for i in range(10):  # Try to execute 10 times
        result = await scheduler.execute_next()
        if result:
            executed_count += 1
            print(f"  ✅ Executed task {executed_count}")
        else:
            print(f"  ⏸️  No affordable task (iteration {i+1})")
        
        if executed_count >= 5:
            break
        
        await asyncio.sleep(0.5)  # Wait for budget to refill
    
    metrics = scheduler.get_metrics()
    print(f"\nFinal: {metrics['tasks_executed']} executed, "
          f"{metrics['tasks_deferred']} deferred, "
          f"{metrics['queue_size']} still queued")
    
    await scheduler.stop()


async def demo_cost_vs_priority():
    """Demo 3: Cost-aware priority balancing"""
    print("\n" + "="*60)
    print("DEMO 3: Cost vs Priority Trade-offs")
    print("="*60)
    
    budget = ResourceBudget(
        api_calls_per_minute=20,
        compute_units=30.0,
        memory_mb=150
    )
    
    scheduler = CostAwareScheduler(budget=budget, name="demo3")
    await scheduler.start()
    
    print("\nScheduling tasks with different cost/priority combinations:")
    
    # Expensive high-priority vs cheap low-priority
    tasks = [
        ("Expensive CRITICAL", TaskPriority.CRITICAL, TaskCost(api_calls=5, compute_units=15.0)),
        ("Cheap LOW priority", TaskPriority.LOW, TaskCost(api_calls=1, compute_units=2.0)),
        ("Medium NORMAL", TaskPriority.NORMAL, TaskCost(api_calls=2, compute_units=5.0)),
    ]
    
    for desc, priority, cost in tasks:
        await scheduler.schedule(
            api_call_task,
            priority=priority,
            cost=cost,
            task_id=desc,
            api_name="test"
        )
        print(f"  {desc}: {cost.api_calls} API calls, {cost.compute_units} compute")
    
    print("\nExecution order (priority-weighted by cost):")
    for i in range(3):
        result = await scheduler.execute_next()
        if result:
            print(f"  {i+1}. {result}")
        await asyncio.sleep(0.3)
    
    await scheduler.stop()


async def demo_rejection_policy():
    """Demo 4: Admission control with rejection"""
    print("\n" + "="*60)
    print("DEMO 4: Admission Control (Reject vs Queue)")
    print("="*60)
    
    budget = ResourceBudget(
        api_calls_per_minute=5,  # Very tight
        compute_units=10.0,
        memory_mb=50
    )
    
    scheduler = CostAwareScheduler(budget=budget, name="demo4")
    await scheduler.start()
    
    print(f"\nVery tight budget: {budget.api_calls_per_minute} API calls/min")
    
    # Try to schedule tasks with rejection policy
    print("\nAttempting to schedule 3 expensive tasks with reject_if_no_budget=True:")
    
    for i in range(3):
        try:
            task_id = await scheduler.schedule(
                compute_task,
                priority=TaskPriority.HIGH,
                cost=TaskCost(api_calls=3, compute_units=5.0),
                reject_if_no_budget=True,  # Reject immediately if over budget
                task_id=f"strict-{i}",
                complexity="moderate"
            )
            print(f"  ✅ Task {i+1} accepted (id: {task_id[:20]}...)")
        except BudgetExhaustedError as e:
            print(f"  ❌ Task {i+1} rejected: Insufficient budget")
    
    metrics = scheduler.get_metrics()
    print(f"\nResult: {metrics['tasks_queued']} queued, "
          f"{metrics['tasks_rejected']} rejected immediately")
    
    # Now schedule without rejection (allows queueing)
    print("\nScheduling 2 more tasks WITHOUT rejection (allows queueing):")
    for i in range(2):
        task_id = await scheduler.schedule(
            compute_task,
            priority=TaskPriority.NORMAL,
            cost=TaskCost(api_calls=2, compute_units=3.0),
            reject_if_no_budget=False,  # Queue even if over budget
            task_id=f"queue-{i}",
            complexity="light"
        )
        print(f"  ✅ Task queued (id: {task_id[:20]}...)")
    
    print(f"\nQueue size: {len(scheduler.get_queue_status())} tasks waiting")
    
    await scheduler.stop()


async def demo_realistic_scenario():
    """Demo 5: Realistic mixed workload"""
    print("\n" + "="*60)
    print("DEMO 5: Realistic Mixed Workload")
    print("="*60)
    
    budget = ResourceBudget(
        api_calls_per_minute=30,
        compute_units=50.0,
        memory_mb=200
    )
    
    scheduler = CostAwareScheduler(budget=budget, name="production")
    await scheduler.start()
    
    print("\nSimulating production workload:")
    print("  - User-facing requests (CRITICAL)")
    print("  - Analytics tasks (NORMAL)")
    print("  - Background cleanup (LOW)")
    
    # Simulate realistic task arrival
    tasks = [
        ("User request", TaskPriority.CRITICAL, TaskCost(api_calls=2, compute_units=3.0)),
        ("Analytics job", TaskPriority.NORMAL, TaskCost(api_calls=1, compute_units=10.0)),
        ("Cache cleanup", TaskPriority.LOW, TaskCost(api_calls=0, compute_units=2.0)),
        ("User request", TaskPriority.CRITICAL, TaskCost(api_calls=2, compute_units=3.0)),
        ("Data export", TaskPriority.NORMAL, TaskCost(api_calls=3, compute_units=8.0)),
        ("Log rotation", TaskPriority.LOW, TaskCost(api_calls=0, compute_units=1.0)),
    ]
    
    print("\nScheduling 6 tasks...")
    for desc, priority, cost in tasks:
        await scheduler.schedule(
            api_call_task,
            priority=priority,
            cost=cost,
            task_id=desc,
            api_name=desc
        )
    
    print("\nExecuting tasks:")
    executed = []
    for i in range(6):
        result = await scheduler.execute_next()
        if result:
            executed.append(result)
            print(f"  {i+1}. {result}")
        await asyncio.sleep(0.2)
    
    metrics = scheduler.get_metrics()
    print(f"\nFinal metrics:")
    print(f"  Executed: {metrics['tasks_executed']}")
    print(f"  Deferred: {metrics['tasks_deferred']}")
    print(f"  Total API calls used: {metrics['total_cost_spent']['api_calls']:.0f}")
    print(f"  Total compute used: {metrics['total_cost_spent']['compute_units']:.1f}")
    
    await scheduler.stop()


async def main():
    """Run all demonstrations"""
    print("\n" + "="*60)
    print("  COST-AWARE TASK SCHEDULER DEMONSTRATIONS")
    print("="*60)
    
    await demo_basic_scheduling()
    await demo_budget_constraints()
    await demo_cost_vs_priority()
    await demo_rejection_policy()
    await demo_realistic_scenario()
    
    print("\n" + "="*60)
    print("All demos completed!")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())