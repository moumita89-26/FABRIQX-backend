from django.urls import path

from . import views


app_name = 'fabriqx'

urlpatterns = [
    path('admin/invoices/<int:pk>/print/', views.invoice_print, name='invoice_print'),
]
