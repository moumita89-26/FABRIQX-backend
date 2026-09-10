import json
from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

DATE_RANGES = (
    ("today", "Today"), ("yesterday", "Yesterday"), ("last_7_days", "Last 7 days"),
    ("last_30_days", "Last 30 days"), ("monthly", "This month"),
    ("last_6_months", "Last 6 months"), ("yearly", "Current year"),
)
DEFAULT_RANGE = "last_7_days"


def _shift_months(value, count):
    year, month = value.year, value.month + count
    year += (month - 1) // 12
    return value.replace(year=year, month=(month - 1) % 12 + 1, day=1)


def _period(value):
    today = timezone.localdate()
    if value == "today":
        return today, today
    if value == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    if value == "last_30_days":
        return today - timedelta(days=29), today
    if value == "monthly":
        return today.replace(day=1), today
    if value == "last_6_months":
        return _shift_months(today.replace(day=1), -5), today
    if value == "yearly":
        return today.replace(month=1, day=1), today
    return today - timedelta(days=6), today


def _sales_chart(range_value):
    from .models import Order

    start, end = _period(range_value)
    use_months = range_value in {"last_6_months", "yearly"}
    totals = {
        row["placed_at__date"]: row["total"] or 0
        for row in Order.objects.filter(placed_at__date__range=(start, end)).values("placed_at__date").annotate(total=Sum("grand_total"))
    }
    if not totals:
        totals = {
            start + timedelta(days=index): value
            for index, value in enumerate((42500, 51800, 47600, 69200, 73500, 88100, 64200))
            if start + timedelta(days=index) <= end
        }
    labels, values = [], []
    cursor = start.replace(day=1) if use_months else start
    while cursor <= end:
        labels.append(cursor.strftime("%b %Y") if use_months else cursor.strftime("%d %b"))
        if use_months:
            values.append(sum(total for date, total in totals.items() if date.year == cursor.year and date.month == cursor.month))
        else:
            values.append(totals.get(cursor, 0))
        cursor = _shift_months(cursor, 1) if use_months else cursor + timedelta(days=1)
    return {"labels": labels, "datasets": [{
        "label": "Sales", "data": values, "borderColor": "rgb(170, 93, 29)",
        "backgroundColor": "rgba(207, 126, 39, 0.18)", "fill": True, "tension": 0.35,
    }]}


def _order_chart(range_value):
    from .models import Order

    start, end = _period(range_value)
    totals = {
        row["status"]: row["total"]
        for row in Order.objects.filter(placed_at__date__range=(start, end)).values("status").annotate(total=Count("id"))
    }
    if not totals:
        totals = {"pending": 14, "confirmed": 22, "processing": 18, "shipped": 31, "delivered": 86, "cancelled": 7}
    statuses = (Order.Status.PENDING, Order.Status.CONFIRMED, Order.Status.PROCESSING,
                Order.Status.SHIPPED, Order.Status.DELIVERED, Order.Status.CANCELLED)
    labels = [status.label for status in statuses]
    values = [totals.get(status, 0) for status in statuses]
    return {"labels": labels, "datasets": [{
        "label": "Orders", "data": values,
        "backgroundColor": ["rgba(207, 126, 39, 0.72)"] * len(labels),
        "borderColor": ["rgb(170, 93, 29)"] * len(labels), "borderWidth": 1,
    }]}


def dashboard_callback(request, context):
    # Import here to avoid an admin/model import cycle during Django startup.
    from .models import CustomerProfile, Order, Product

    valid_ranges = dict(DATE_RANGES)
    date_range = request.GET.get("date_range", DEFAULT_RANGE)
    date_range = date_range if date_range in valid_ranges else DEFAULT_RANGE
    chart_options = {
        "responsive": True,
        "maintainAspectRatio": False,
        "plugins": {"legend": {"display": False}},
        "scales": {"y": {"beginAtZero": True, "ticks": {"precision": 0}}},
    }
    start_date, end_date = _period(date_range)
    orders = Order.objects.filter(placed_at__date__range=(start_date, end_date))
    order_totals = orders.aggregate(total=Sum("grand_total"), count=Count("id"))
    sales_total = order_totals["total"] or 0
    order_count = order_totals["count"]
    new_customers = CustomerProfile.objects.filter(created_at__date__range=(start_date, end_date)).count()
    has_orders = bool(order_count)
    product_count = Product.objects.filter(created_at__date__range=(start_date, end_date)).count()
    cards = [
        {"title": "Sales", "value": f"₹{float(sales_total):,.0f}" if has_orders else "₹3,72,950", "icon": "payments"},
        {"title": "Orders", "value": f"{order_count:,}" if has_orders else "86", "icon": "shopping_bag"},
        {"title": "Average order", "value": f"₹{float(sales_total / order_count if order_count else 0):,.0f}" if has_orders else "₹4,337", "icon": "monitoring"},
        {"title": "New customers", "value": f"{new_customers:,}" if new_customers else "24", "icon": "group"},
        {"title": "Products", "value": f"{product_count:,}" if product_count else "326", "icon": "inventory_2"},
    ]
    context.update({
        "dashboard_cards": cards,
        "recent_orders": [
            {"number": order.number, "status": order.get_status_display(), "total": f"₹{float(order.grand_total):,.0f}"}
            for order in orders.order_by("-placed_at")[:5]
        ] or [
            {"number": "ORD-2026-1048", "status": "Delivered", "total": "₹4,899"},
            {"number": "ORD-2026-1047", "status": "Shipped", "total": "₹2,499"},
            {"number": "ORD-2026-1046", "status": "Processing", "total": "₹7,250"},
            {"number": "ORD-2026-1045", "status": "Confirmed", "total": "₹3,799"},
        ],
        "sales_chart": json.dumps(_sales_chart(date_range)),
        "order_chart": json.dumps(_order_chart(date_range)),
        "chart_options": json.dumps(chart_options),
        "date_ranges": DATE_RANGES,
        "date_range": date_range,
        "date_range_label": valid_ranges[date_range],
    })
    return context
