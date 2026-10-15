"""Stock Django admin for the proxies app (decision D7 -- deliberately minimal).

The one custom piece is the CSV bulk upload view, which is a required
feature. It is a plain form page plus a link on the proxy changelist; there
is no dashboard, theme or widget work here.
"""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import path, reverse

from proxies.forms import ProxyCSVUploadForm
from proxies.importers import CSVImportError, import_proxies_from_csv
from proxies.models import ChainHop, Proxy, ProxyChain, UploadBatch


@admin.register(Proxy)
class ProxyAdmin(admin.ModelAdmin):
    list_display = (
        "host",
        "port",
        "protocol",
        "tier",
        "country",
        "city",
        "is_enabled",
        "is_alive",
        "latency_ms",
        "last_checked",
    )
    list_filter = ("protocol", "tier", "is_enabled", "is_alive", "country")
    search_fields = ("host", "country", "city")
    list_select_related = ("upload_batch",)
    change_list_template = "admin/proxies/proxy/change_list.html"

    def get_urls(self):
        custom = [
            path(
                "upload-csv/",
                self.admin_site.admin_view(self.upload_csv_view),
                name="proxies_proxy_upload_csv",
            ),
        ]
        return custom + super().get_urls()

    def upload_csv_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied

        if request.method == "POST":
            form = ProxyCSVUploadForm(request.POST, request.FILES)
            if form.is_valid():
                uploaded = form.cleaned_data["csv_file"]
                try:
                    batch = import_proxies_from_csv(
                        uploaded, uploaded.name, uploaded_by=request.user
                    )
                except CSVImportError as exc:
                    self.message_user(request, str(exc), level=messages.ERROR)
                else:
                    self.message_user(
                        request,
                        f"{batch.filename}: {batch.row_count} rows, "
                        f"{batch.created_count} created, {batch.updated_count} updated, "
                        f"{batch.fail_count} failed.",
                        level=messages.WARNING if batch.fail_count else messages.SUCCESS,
                    )
                    # Send the operator to the batch record, unless they lack
                    # permission to view it -- the import still succeeded.
                    if request.user.has_perm("proxies.change_uploadbatch"):
                        return redirect(
                            reverse("admin:proxies_uploadbatch_change", args=[batch.pk])
                        )
                    return redirect(reverse("admin:proxies_proxy_changelist"))
        else:
            form = ProxyCSVUploadForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Bulk upload proxies (CSV)",
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/proxies/proxy/upload_csv.html", context)


@admin.register(UploadBatch)
class UploadBatchAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "uploaded_by",
        "row_count",
        "success_count",
        "fail_count",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = ("filename", "uploaded_by__email")
    readonly_fields = (
        "uploaded_by",
        "filename",
        "row_count",
        "created_count",
        "updated_count",
        "success_count",
        "fail_count",
        "errors",
        "created_at",
    )

    def has_add_permission(self, request):
        # Batches are created by the CSV upload view, never by hand.
        return False


class ChainHopInline(admin.TabularInline):
    model = ChainHop
    extra = 0
    autocomplete_fields = ("proxy",)


@admin.register(ProxyChain)
class ProxyChainAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "length", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("name", "user__email")
    autocomplete_fields = ("user",)
    inlines = (ChainHopInline,)
