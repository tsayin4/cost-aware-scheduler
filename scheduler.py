"""
Cost-Aware Task Scheduler
Intelligent async task scheduling with resource budget constraints
"""

import asyncio
import time
from enum import Enum
from typing import Optional, Callable, Any, Dict, List
from dataclasses import dataclass, field
from collections import deque
import heapq


class TaskPriority(Enum):
    """Task priority levels"""
    CRITICAL = 1    # Must execute (user-facing)
    HIGH = 2        # Important but can wait
    NORMAL = 3      # Standard background tasks
    LOW = 4         # Best-effort, defer when constrained


@dataclass
class ResourceBudget:
    """Resource budget configuration"""
    api_calls_per_minute: int = 60
    compute_units: float = 100.0  # Arbitrary units
    memory_mb: int = 512
    
    def __post_init__(self):
        if self.api_calls_per_minute <= 0:
            raise ValueError("api_calls_per_minute must be > 0")
        if self.compute_units <= 0:
            raise ValueError("compute_units must be > 0")
        if self.memory_mb <= 0:
            raise ValueError("memory_mb must be > 0")


@dataclass
class TaskCost:
    """Resource cost of a single task"""
    api_calls: int = 0
    compute_units: float = 1.0
    memory_mb: int = 10
    estimated_duration: float = 1.0  # seconds


@dataclass(order=True)
class ScheduledTask:
    """Task with priority and cost awareness"""
    # Priority for heap ordering (lower number = higher priority)
    _priority_score: float = field(init=False, repr=False)
    
    # Task metadata
    priority: TaskPriority = field(compare=False)
    cost: TaskCost = field(compare=False)
    func: Callable = field(compare=False)
    args: tuple = field(default_factory=tuple, compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)
    task_id: str = field(default="", compare=False)
    created_at: float = field(default_factory=time.time, compare=False)
    
    def __post_init__(self):
        # Calculate weighted priority score
        # Lower score = higher priority
        self._priority_score = self._calculate_score()
    
    def _calculate_score(self) -> float:
        """
        Calculate priority score (lower = more urgent)
        
        Key Decision: Weighted score considering:
        1. Priority level (base weight)
        2. Cost efficiency (cost per priority)
        3. Age (prevent starvation)
        
        Alternative considered: Pure priority queue
        Why not: Ignores resource efficiency
        """
        base_priority = self.priority.value
        cost_factor = self.cost.compute_units + (self.cost.api_calls * 0.5)
        age_factor = (time.time() - self.created_at) / 60.0  # minutes old
        
        # Combine factors (tune weights based on use case)
        score = (base_priority * 10.0) + (cost_factor * 0.1) - (age_factor * 0.5)
        return score


@dataclass
class SchedulerMetrics:
    """Metrics for monitoring scheduler performance"""
    tasks_queued: int = 0
    tasks_executed: int = 0
    tasks_rejected: int = 0
    tasks_deferred: int = 0
    total_cost_spent: Dict[str, float] = field(default_factory=lambda: {
        "api_calls": 0.0,
        "compute_units": 0.0,
        "memory_mb": 0.0
    })
    
    def record_execution(self, cost: TaskCost):
        self.tasks_executed += 1
        self.total_cost_spent["api_calls"] += cost.api_calls
        self.total_cost_spent["compute_units"] += cost.compute_units
        self.total_cost_spent["memory_mb"] += cost.memory_mb
    
    def record_rejection(self):
        self.tasks_rejected += 1
    
    def record_deferral(self):
        self.tasks_deferred += 1


class BudgetExhaustedError(Exception):
    """Raised when resource budget is exhausted"""
    pass


class CostAwareScheduler:
    """
    Production-grade task scheduler with resource budget awareness.
    
    Key Design Decisions:
    
    1. Priority queue + cost awareness (not just FIFO)
       - Reason: Balance urgency with resource efficiency
       - Alternative: Pure priority - ignores resource constraints
       - Trade-off: More complex but production-realistic
    
    2. Token bucket for rate limiting (not simple counter)
       - Reason: Allows burst capacity
       - Alternative: Hard limit - too restrictive
       - Trade-off: Slightly more code but better UX
    
    3. Proactive admission control (reject early vs fail late)
       - Reason: Fast feedback, prevents queue buildup
       - Alternative: Queue everything - causes memory issues
       - Trade-off: Some tasks rejected that might've fit later
    """
    
    def __init__(
        self,
        budget: Optional[ResourceBudget] = None,
        name: str = "default"
    ):
        self.budget = budget or ResourceBudget()
        self.name = name
        
        # Priority queue (min-heap by priority score)
        self._queue: List[ScheduledTask] = []
        self._lock = asyncio.Lock()
        
        # Current resource usage (resets periodically)
        self._current_usage = {
            "api_calls": 0,
            "compute_units": 0.0,
            "memory_mb": 0
        }
        
        # Token bucket for rate limiting
        self._api_tokens = float(budget.api_calls_per_minute)
        self._last_refill = time.time()
        
        # Metrics
        self.metrics = SchedulerMetrics()
        
        # Background refill task
        self._refill_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start background token refill"""
        if self._refill_task is None:
            self._refill_task = asyncio.create_task(self._refill_tokens())
    
    async def stop(self):
        """Stop scheduler and cleanup"""
        if self._refill_task:
            self._refill_task.cancel()
            try:
                await self._refill_task
            except asyncio.CancelledError:
                pass
    
    async def _refill_tokens(self):
        """Background task to refill API call tokens"""
        while True:
            await asyncio.sleep(1.0)  # Refill every second
            
            async with self._lock:
                now = time.time()
                elapsed = now - self._last_refill
                
                # Refill proportionally (smooth rate limiting)
                tokens_to_add = (self.budget.api_calls_per_minute / 60.0) * elapsed
                self._api_tokens = min(
                    self._api_tokens + tokens_to_add,
                    float(self.budget.api_calls_per_minute)
                )
                self._last_refill = now
    
    def _can_afford(self, cost: TaskCost) -> bool:
        """Check if we have budget for this task"""
        return (
            self._api_tokens >= cost.api_calls and
            (self._current_usage["compute_units"] + cost.compute_units) <= self.budget.compute_units and
            (self._current_usage["memory_mb"] + cost.memory_mb) <= self.budget.memory_mb
        )
    
    async def schedule(
        self,
        func: Callable,
        priority: TaskPriority = TaskPriority.NORMAL,
        cost: Optional[TaskCost] = None,
        *args,
        reject_if_no_budget: bool = False,
        **kwargs
    ) -> str:
        """
        Schedule a task for execution.
        
        Args:
            func: Async function to execute
            priority: Task priority level
            cost: Resource cost estimate
            *args: Positional arguments for func
            reject_if_no_budget: If True, reject immediately if over budget
            **kwargs: Keyword arguments for func
        
        Returns:
            Task ID
        
        Raises:
            BudgetExhaustedError: If reject_if_no_budget=True and no budget
        """
        cost = cost or TaskCost()
        task_id = f"{self.name}-{int(time.time() * 1000)}"
        
        task = ScheduledTask(
            priority=priority,
            cost=cost,
            func=func,
            args=args,
            kwargs=kwargs,
            task_id=task_id
        )
        
        async with self._lock:
            # Admission control
            if reject_if_no_budget and not self._can_afford(cost):
                self.metrics.record_rejection()
                raise BudgetExhaustedError(
                    f"Insufficient budget for task {task_id}. "
                    f"Required: {cost}, Available tokens: {self._api_tokens:.1f}"
                )
            
            # Add to priority queue
            heapq.heappush(self._queue, task)
            self.metrics.tasks_queued += 1
        
        return task_id
    
    async def execute_next(self) -> Optional[Any]:
        """
        Execute highest priority task that fits budget.
        
        Returns:
            Task result or None if queue empty or no affordable task
        """
        async with self._lock:
            if not self._queue:
                return None
            
            # Try to find affordable task (check top N tasks)
            affordable_task = None
            skipped_tasks = []
            
            # Check up to 5 highest priority tasks
            for _ in range(min(5, len(self._queue))):
                if not self._queue:
                    break
                
                candidate = heapq.heappop(self._queue)
                
                if self._can_afford(candidate.cost):
                    affordable_task = candidate
                    break
                else:
                    skipped_tasks.append(candidate)
                    self.metrics.record_deferral()
            
            # Return skipped tasks to queue
            for task in skipped_tasks:
                heapq.heappush(self._queue, task)
            
            if not affordable_task:
                return None
            
            # Consume budget
            self._api_tokens -= affordable_task.cost.api_calls
            self._current_usage["compute_units"] += affordable_task.cost.compute_units
            self._current_usage["memory_mb"] += affordable_task.cost.memory_mb
        
        # Execute task (outside lock)
        try:
            result = await affordable_task.func(*affordable_task.args, **affordable_task.kwargs)
            
            async with self._lock:
                self.metrics.record_execution(affordable_task.cost)
                # Release compute/memory (API tokens refill automatically)
                self._current_usage["compute_units"] -= affordable_task.cost.compute_units
                self._current_usage["memory_mb"] -= affordable_task.cost.memory_mb
            
            return result
        
        except Exception as e:
            # Release resources on failure
            async with self._lock:
                self._current_usage["compute_units"] -= affordable_task.cost.compute_units
                self._current_usage["memory_mb"] -= affordable_task.cost.memory_mb
            raise
    
    async def execute_all(self):
        """Execute all queued tasks that fit budget"""
        while True:
            result = await self.execute_next()
            if result is None:
                break
            await asyncio.sleep(0.1)  # Prevent tight loop
    
    def get_metrics(self) -> dict:
        """Get current metrics snapshot"""
        return {
            "name": self.name,
            "queue_size": len(self._queue),
            "tasks_queued": self.metrics.tasks_queued,
            "tasks_executed": self.metrics.tasks_executed,
            "tasks_rejected": self.metrics.tasks_rejected,
            "tasks_deferred": self.metrics.tasks_deferred,
            "current_usage": self._current_usage.copy(),
            "total_cost_spent": self.metrics.total_cost_spent.copy(),
            "api_tokens_available": self._api_tokens,
        }
    
    def get_queue_status(self) -> List[dict]:
        """Get current queue contents"""
        return [
            {
                "task_id": task.task_id,
                "priority": task.priority.name,
                "cost": {
                    "api_calls": task.cost.api_calls,
                    "compute_units": task.cost.compute_units,
                    "memory_mb": task.cost.memory_mb
                },
                "age_seconds": time.time() - task.created_at
            }
            for task in sorted(self._queue)
        ]