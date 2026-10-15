"""Admin forms for the proxies app."""

from django import forms
from django.conf import settings


class ProxyCSVUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV file",
        help_text=(
            "Header row required. Columns: protocol,host,port,username,password,"
            "ss_method,country,city,tier. protocol/host/port are mandatory; "
            "ss_method is mandatory when protocol is ss. tier defaults to "
            "premium when the column is absent."
        ),
    )

    def clean_csv_file(self):
        uploaded = self.cleaned_data["csv_file"]
        max_bytes = getattr(settings, "PROXY_CSV_MAX_BYTES", 5242880)
        if uploaded.size > max_bytes:
            raise forms.ValidationError(
                f"File is {uploaded.size} bytes; the limit is {max_bytes}."
            )
        if not uploaded.name.lower().endswith((".csv", ".txt")):
            raise forms.ValidationError("Expected a .csv file.")
        return uploaded
