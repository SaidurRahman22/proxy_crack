"""Model fields that encrypt their value at rest.

`EncryptedCharField` is a drop-in for `models.CharField`: declare the same
`max_length` you would have used for the plaintext and the field sizes the
underlying column to fit the Fernet token.

One behaviour genuinely differs from `CharField` and cannot be papered over:
Fernet is non-deterministic (random IV, embedded timestamp), so the same
plaintext encrypts to a different token every time. Equality lookups,
`__contains`, ordering and `search_fields` therefore cannot work. Rather
than return silently-wrong empty results, the field refuses every lookup
except `isnull`.
"""

from django.core.exceptions import FieldError
from django.core.validators import MaxLengthValidator
from django.db import models

from core.crypto import decrypt, encrypt, encrypted_max_length

SUPPORTED_LOOKUPS = {"isnull"}


class EncryptedCharField(models.CharField):
    description = "Fernet-encrypted string"

    def __init__(self, *args, **kwargs):
        # The declared max_length is the plaintext limit; the column has to
        # be wide enough for the ciphertext.
        self.plaintext_max_length = kwargs.get("max_length")
        if self.plaintext_max_length is not None:
            kwargs["max_length"] = encrypted_max_length(self.plaintext_max_length)
        super().__init__(*args, **kwargs)
        if self.plaintext_max_length is not None:
            # CharField.__init__ already added a validator for the ciphertext
            # width. Validators run against the plaintext python value, so
            # this stricter one is the binding limit.
            self.validators.append(MaxLengthValidator(self.plaintext_max_length))

    def deconstruct(self):
        """Keep migrations expressed in plaintext terms.

        The widened column is recomputed in `__init__`, so a change to the
        sizing formula does not require rewriting old migrations.
        """
        name, path, args, kwargs = super().deconstruct()
        if self.plaintext_max_length is not None:
            kwargs["max_length"] = self.plaintext_max_length
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        return decrypt(value)

    def get_prep_value(self, value):
        return encrypt(super().get_prep_value(value))

    def formfield(self, **kwargs):
        """Forms should limit the plaintext a user types, not the width of
        the token it turns into."""
        defaults = {}
        if self.plaintext_max_length is not None:
            defaults["max_length"] = self.plaintext_max_length
        defaults.update(kwargs)
        return super().formfield(**defaults)

    def get_lookup(self, lookup_name):
        if lookup_name not in SUPPORTED_LOOKUPS:
            raise FieldError(
                f"{self.__class__.__name__} does not support the "
                f"{lookup_name!r} lookup: values are encrypted with a random "
                "IV, so a query filter would silently match nothing. Filter "
                "in Python after loading the rows, or add a separate "
                "searchable column."
            )
        return super().get_lookup(lookup_name)
