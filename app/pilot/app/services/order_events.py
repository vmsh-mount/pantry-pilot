"""
Post-order task dispatcher.

Call dispatch_post_order_tasks(order_id) after every order commit, regardless
of order type (Flow, Quick Order, Routines, or any future type). All background
work that needs to run after an order is placed lives here — nutrition resolution
today, loyalty / analytics / re-stock signals tomorrow.

Adding a new order type: create the Order row, commit, then call this function.
Adding a new post-order task: import it and add a .delay() call below.
"""

from app.utils.logging import get_logger

logger = get_logger(__name__)


def dispatch_post_order_tasks(order_id: str) -> None:
    """Fire-and-forget all background tasks for a newly placed order."""
    from app.tasks.nutrition import resolve_order_nutrition

    resolve_order_nutrition.delay(str(order_id))
    logger.info("post_order_tasks_dispatched", order_id=order_id)
