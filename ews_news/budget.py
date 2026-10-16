"""Track Claude API spending and enforce the monthly budget.

Every API call is logged in the api_usage table with its cost. Each run may
spend the money left this month divided by the days left this month, so an
unused day's allowance rolls over to later days.
"""

import calendar
from datetime import datetime, timezone

from .textutil import utcnow


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, conn, settings: dict, run_id=None, now: datetime = None):
        self.conn = conn
        self.cfg = settings["budget"]
        self.run_id = run_id
        now = now or datetime.now(timezone.utc)
        month_start = now.strftime("%Y-%m-01T00:00:00Z")
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM api_usage WHERE ts >= ?",
            (month_start,)).fetchone()
        self.spent_month = row[0]
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        days_left = days_in_month - now.day + 1
        remaining = max(0.0, self.cfg["monthly_usd"] - self.spent_month)
        self.allowance = remaining / days_left
        self.spent_run = 0.0

    def cost(self, model: str, usage, web_searches: int = 0) -> float:
        p = self.cfg["prices"][model]
        return (
            (usage.get("input_tokens", 0) * p["input"]
             + usage.get("output_tokens", 0) * p["output"]
             + usage.get("cache_read", 0) * p["cache_read"]
             + usage.get("cache_write", 0) * p["cache_write"]) / 1e6
            + web_searches * self.cfg["web_search_usd"]
        )

    def check(self, estimate: float) -> None:
        """Raise BudgetExceeded if a call costing `estimate` would break the cap."""
        if self.spent_run + estimate > self.allowance:
            raise BudgetExceeded(
                f"run allowance ${self.allowance:.4f} reached "
                f"(spent ${self.spent_run:.4f} this run, ${self.spent_month:.2f} this month)")

    def record(self, purpose: str, model: str, usage: dict, web_searches: int = 0) -> float:
        cost = self.cost(model, usage, web_searches)
        self.conn.execute(
            "INSERT INTO api_usage (ts, run_id, purpose, model, input_tokens, output_tokens,"
            " cache_read, cache_write, web_searches, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (utcnow(), self.run_id, purpose, model, usage.get("input_tokens", 0),
             usage.get("output_tokens", 0), usage.get("cache_read", 0),
             usage.get("cache_write", 0), web_searches, cost))
        self.conn.commit()
        self.spent_run += cost
        self.spent_month += cost
        return cost


def usage_dict(usage) -> dict:
    """Convert an SDK usage object into plain numbers."""
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def web_search_count(usage) -> int:
    stu = getattr(usage, "server_tool_use", None)
    return (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
