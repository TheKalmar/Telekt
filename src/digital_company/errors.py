"""Domain failures that control durable retry and operator-facing state."""


class BudgetLimitError(RuntimeError):
    """A configured inference limit blocks another paid model call."""
