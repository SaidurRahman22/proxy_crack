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
from proxies.models import (
    ChainHop,
    IpIntelRecord,
    Proxy,
    ProxyChain,
    ProxyProbe,
    UploadBatch,
)


@admin.register(Proxy)
class ProxyAdmin(admin.ModelAdmin):
    list_display = (
        "host",
        "port",
        "protocol",
        "tier",
        "country",
        "city",
        "provider",
        "is_enabled",
        "is_alive",
        "quality_score",
        "success_rate",
        "latency_p95_ms",
        "last_checked",
    )
    list_filter = ("protocol", "tier", "is_enabled", "is_alive", "country", "provider")
    search_fields = ("host", "country", "city", "provider")
    list_select_related = ("upload_batch",)
    change_list_template = "admin/proxies/proxy/change_list.html"
    # Derived from ProxyProbe by proxies.health. Editable here would mean
    # an operator's hand-typed success rate silently reverting at the next
    # probe run, which reads as the admin losing writes.
    readonly_fields = (
        "success_rate",
        "probe_count",
        "consecutive_failures",
        "latency_p50_ms",
        "latency_p95_ms",
        "quality_score",
        "last_checked",
        "latency_ms",
        "is_alive",
    )

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


@admin.register(ProxyProbe)
class ProxyProbeAdmin(admin.ModelAdmin):
    """Read-only. Probe rows are observations, not records to edit.

    No add permission and no editable fields: a probe row that someone
    typed is not a measurement, and the health figures derived from this
    table would then be derived partly from opinion.
    """

    list_display = ("proxy", "checked_at", "succeeded", "latency_ms", "error_kind")
    list_filter = ("succeeded", "error_kind", "checked_at")
    search_fields = ("proxy__host", "target")
    list_select_related = ("proxy",)
    date_hierarchy = "checked_at"
    readonly_fields = (
        "proxy",
        "checked_at",
        "succeeded",
        "latency_ms",
        "error_kind",
        "target",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ChainHopInline(admin.TabularInline):
    model = ChainHop
    extra = 0
    autocomplete_fields = ("proxy",)
    fields = (
        "position",
        "proxy",
        "protocol",
        "country",
        "tier",
        "max_latency_ms",
        "min_success_rate",
        "allow_unprobed",
    )


@admin.register(ProxyChain)
class ProxyChainAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "length", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("name", "user__email")
    autocomplete_fields = ("user",)
    inlines = (ChainHopInline,)


@admin.register(IpIntelRecord)
class IpIntelRecordAdmin(admin.ModelAdmin):
    """Read-only. These rows are a cache of an upstream lookup, and a
    hand-typed ASN would both be overwritten by the next refresh and, in
    the meantime, make the diversity analysis report a correlation nobody
    measured -- which is the one thing that feature must never do."""

    list_display = (
        "query",
        "ip",
        "asn",
        "asn_org",
        "prefix",
        "country",
        "status",
        "looked_up_at",
        "expires_at",
    )
    list_filter = ("status", "source", "country", "registry")
    search_fields = ("query", "ip", "asn_org", "prefix")
    readonly_fields = (
        "query",
        "ip",
        "asn",
        "asn_org",
        "prefix",
        "country",
        "registry",
        "status",
        "source",
        "detail",
        "looked_up_at",
        "expires_at",
    )
    date_hierarchy = "looked_up_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
