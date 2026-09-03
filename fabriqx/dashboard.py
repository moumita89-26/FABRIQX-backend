from datetime import timedelta
import json

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import CustomerProfile, InfluencerCommission, Order, Product, ProductVariant


def dashboard_callback(request, context):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    paid_statuses = (Order.Status.CONFIRMED, Order.Status.PROCESSING, Order.Status.SHIPPED, Order.Status.DELIVERED)
    sales = Order.objects.filter(status__in=paid_statuses)
    sales_days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    sales_rows = sales.filter(placed_at__date__gte=sales_days[0]).annotate(day=TruncDate("placed_at")).values("day").annotate(total=Sum("grand_total"))
    sales_by_day = {row["day"]: float(row["total"] or 0) for row in sales_rows}
    status_rows = Order.objects.values("status").annotate(total=Count("id"))
    status_counts = {row["status"]: row["total"] for row in status_rows}

    sales_chart = {
        "labels": [day.strftime("%d %b") for day in sales_days],
        "datasets": [{
            "label": "Sales",
            "data": [sales_by_day.get(day, 0) for day in sales_days],
            "borderColor": "rgb(170, 93, 29)",
            "backgroundColor": "rgba(207, 126, 39, 0.18)",
            "fill": True,
            "tension": 0.35,
        }],
    }
    order_chart = {
        "labels": [label for value, label in Order.Status.choices],
        "datasets": [{
            "label": "Orders",
            "data": [status_counts.get(value, 0) for value, label in Order.Status.choices],
            "backgroundColor": ["rgba(207, 126, 39, 0.72)"] * len(Order.Status.choices),
            "borderColor": ["rgb(170, 93, 29)"] * len(Order.Status.choices),
            "borderWidth": 1,
        }],
    }
    chart_options = {
        "responsive": True,
        "maintainAspectRatio": False,
        "plugins": {"legend": {"display": False}},
        "scales": {"y": {"beginAtZero": True, "ticks": {"precision": 0}}},
    }
    cards = [
        {"title": "Today's sales", "value": sales.filter(placed_at__date=today).aggregate(v=Sum("grand_total"))["v"] or 0, "icon": "payments"},
        {"title": "This month's sales", "value": sales.filter(placed_at__date__gte=month_start).aggregate(v=Sum("grand_total"))["v"] or 0, "icon": "monitoring"},
        {"title": "Orders", "value": Order.objects.count(), "icon": "shopping_bag"},
        {"title": "Customers", "value": CustomerProfile.objects.count(), "icon": "group"},
        {"title": "Products", "value": Product.objects.count(), "icon": "inventory_2"},
        {"title": "Pending commission", "value": InfluencerCommission.objects.filter(status=InfluencerCommission.Status.PENDING).aggregate(v=Sum("commission_amount"))["v"] or 0, "icon": "handshake"},
    ]
    context.update({
        "dashboard_cards": cards,
        "recent_orders": Order.objects.select_related("customer").order_by("-placed_at")[:8],
        "low_stock": ProductVariant.objects.select_related("product").filter(stock_quantity__lte=5).order_by("stock_quantity")[:8],
        "sales_chart": json.dumps(sales_chart),
        "order_chart": json.dumps(order_chart),
        "chart_options": json.dumps(chart_options),
    })
    return context
