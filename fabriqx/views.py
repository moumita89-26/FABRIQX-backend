from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, render

from .models import Invoice


@staff_member_required
def invoice_print(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("order"), pk=pk)
    return render(request, "fabriqx/invoice.html", {"invoice": invoice, "order": invoice.order})

# Create your views here.
