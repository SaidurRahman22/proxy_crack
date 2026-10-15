"""Tests for credential encryption at rest.

The load-bearing assertion is `test_stored_column_is_not_plaintext`: it reads
the column with a raw cursor, bypassing the field's converters, which is the
only way to prove what a database dump would actually contain.
"""

from cryptography.fernet import Fernet
from django.core.exceptions import FieldError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from core.checks import check_credential_encryption_key
from core.crypto import (
    CredentialDecryptionError,
    CredentialKeyError,
    _CRYPTER_CACHE,
    decrypt,
    encrypt,
    encrypted_max_length,
    get_crypter,
)
from core.fields import EncryptedCharField
from proxies.models import Proxy, ProxyProtocol, ProxyTier

CRED_COLUMNS = ("username", "password", "ss_method")


def raw_credentials(proxy_id):
    """Read the credential columns with no field converters in the way."""
    columns = ", ".join(CRED_COLUMNS)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {columns} FROM {Proxy._meta.db_table} WHERE id = %s", [proxy_id]
        )
        return dict(zip(CRED_COLUMNS, cursor.fetchone()))


def make_proxy(**kwargs):
    return Proxy.objects.create(
        protocol=kwargs.pop("protocol", ProxyProtocol.SOCKS5),
        host=kwargs.pop("host", "198.51.100.77"),
        port=kwargs.pop("port", 1080),
        tier=kwargs.pop("tier", ProxyTier.PREMIUM),
        **kwargs,
    )


class CryptoRoundTripTests(TestCase):
    def test_round_trip(self):
        self.assertEqual(decrypt(encrypt("hunter2hunter2")), "hunter2hunter2")

    def test_ciphertext_differs_from_plaintext(self):
        token = encrypt("s3cret")
        self.assertNotEqual(token, "s3cret")
        self.assertNotIn("s3cret", token)
        self.assertTrue(token.startswith("gAAAAA"))  # Fernet version byte

    def test_encryption_is_non_deterministic(self):
        self.assertNotEqual(encrypt("same"), encrypt("same"))

    def test_empty_and_none_pass_through(self):
        for value in ("", None):
            self.assertEqual(encrypt(value), value)
            self.assertEqual(decrypt(value), value)

    def test_unicode_survives(self):
        secret = "pa55-éè-中文-\U0001f510"
        self.assertEqual(decrypt(encrypt(secret)), secret)

    def test_decrypting_plaintext_raises_rather_than_returning_it(self):
        with self.assertRaises(CredentialDecryptionError):
            decrypt("this was never encrypted")

    def test_column_width_fits_the_longest_token(self):
        for plaintext_max in (64, 255):
            longest = "\U0001f510" * plaintext_max  # 4 UTF-8 bytes per char
            self.assertLessEqual(
                len(encrypt(longest)), encrypted_max_length(plaintext_max)
            )


class EncryptedFieldTests(TestCase):
    def setUp(self):
        self.proxy = make_proxy(
            username="alice", password="s3cret", ss_method="aes-256-gcm"
        )

    def test_orm_returns_plaintext(self):
        fetched = Proxy.objects.get(pk=self.proxy.pk)
        self.assertEqual(fetched.username, "alice")
        self.assertEqual(fetched.password, "s3cret")
        self.assertEqual(fetched.ss_method, "aes-256-gcm")

    def test_stored_column_is_not_plaintext(self):
        """What a stolen database dump would actually contain."""
        stored = raw_credentials(self.proxy.pk)
        self.assertNotEqual(stored["username"], "alice")
        self.assertNotEqual(stored["password"], "s3cret")
        self.assertNotEqual(stored["ss_method"], "aes-256-gcm")
        for column, value in stored.items():
            self.assertTrue(value.startswith("gAAAAA"), f"{column} is not a token")
        # And the plaintext appears nowhere in the row.
        self.assertNotIn("s3cret", "".join(stored.values()))

    def test_values_list_also_decrypts(self):
        self.assertEqual(
            list(Proxy.objects.filter(pk=self.proxy.pk).values_list("password", flat=True)),
            ["s3cret"],
        )

    def test_blank_credentials_stay_empty_not_encrypted(self):
        bare = make_proxy(host="198.51.100.78")
        self.assertEqual(raw_credentials(bare.pk)["username"], "")
        self.assertEqual(Proxy.objects.get(pk=bare.pk).username, "")

    def test_update_re_encrypts(self):
        self.proxy.password = "rotated-secret"
        self.proxy.save(update_fields=["password"])
        self.assertNotIn("rotated-secret", raw_credentials(self.proxy.pk)["password"])
        self.assertEqual(Proxy.objects.get(pk=self.proxy.pk).password, "rotated-secret")

    def test_lookups_are_refused_rather_than_silently_empty(self):
        with self.assertRaises(FieldError):
            Proxy.objects.filter(password="s3cret").exists()

    def test_migration_declares_plaintext_max_length(self):
        field = Proxy._meta.get_field("password")
        self.assertEqual(field.deconstruct()[3]["max_length"], 255)
        self.assertEqual(field.max_length, encrypted_max_length(255))
        self.assertIsInstance(field, EncryptedCharField)


class KeyConfigurationTests(TestCase):
    def test_missing_key_fails_the_system_check(self):
        with override_settings(PROXY_CRED_KEYS=[]):
            errors = check_credential_encryption_key(None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "core.E001")
        self.assertIn("PROXY_CRED_KEY", errors[0].msg)

    def test_malformed_key_fails_the_system_check(self):
        with override_settings(PROXY_CRED_KEYS=["obviously-not-a-fernet-key"]):
            errors = check_credential_encryption_key(None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "core.E001")
        self.assertIn("not a valid Fernet key", errors[0].msg)

    def test_configured_key_passes_the_system_check(self):
        self.assertEqual(check_credential_encryption_key(None), [])

    def test_missing_key_raises_instead_of_writing_plaintext(self):
        with override_settings(PROXY_CRED_KEYS=[]):
            with self.assertRaises(CredentialKeyError):
                get_crypter()
            with self.assertRaises(CredentialKeyError):
                encrypt("would-have-been-plaintext")

    def test_malformed_key_raises_instead_of_writing_plaintext(self):
        with override_settings(PROXY_CRED_KEYS=["not-base64"]):
            with self.assertRaises(CredentialKeyError):
                encrypt("would-have-been-plaintext")


class KeyRotationTests(TestCase):
    def setUp(self):
        self.old_key = Fernet.generate_key().decode()
        self.new_key = Fernet.generate_key().decode()
        _CRYPTER_CACHE.clear()

    def tearDown(self):
        _CRYPTER_CACHE.clear()

    def test_rotation_without_downtime(self):
        # 1. Row written under the old key only.
        with override_settings(PROXY_CRED_KEYS=[self.old_key]):
            proxy = make_proxy(username="alice", password="s3cret")
            written = raw_credentials(proxy.pk)

        # 2. Both keys configured, new one first: old rows still readable.
        with override_settings(PROXY_CRED_KEYS=[self.new_key, self.old_key]):
            self.assertEqual(Proxy.objects.get(pk=proxy.pk).password, "s3cret")
            call_command("rotate_proxy_credentials", verbosity=0)
            rewritten = raw_credentials(proxy.pk)

        self.assertNotEqual(written["password"], rewritten["password"])

        # 3. Old key dropped: the row is still readable.
        with override_settings(PROXY_CRED_KEYS=[self.new_key]):
            self.assertEqual(Proxy.objects.get(pk=proxy.pk).password, "s3cret")

        # And it is genuinely re-keyed -- the old key alone no longer works.
        with override_settings(PROXY_CRED_KEYS=[self.old_key]):
            with self.assertRaises(CredentialDecryptionError):
                Proxy.objects.get(pk=proxy.pk)

    def test_rotation_is_idempotent(self):
        with override_settings(PROXY_CRED_KEYS=[self.new_key]):
            proxy = make_proxy(password="s3cret")
            call_command("rotate_proxy_credentials", verbosity=0)
            call_command("rotate_proxy_credentials", verbosity=0)
            self.assertEqual(Proxy.objects.get(pk=proxy.pk).password, "s3cret")

    def test_dry_run_does_not_write(self):
        with override_settings(PROXY_CRED_KEYS=[self.new_key]):
            proxy = make_proxy(password="s3cret")
            before = raw_credentials(proxy.pk)
            call_command("rotate_proxy_credentials", "--dry-run", verbosity=0)
            self.assertEqual(raw_credentials(proxy.pk), before)
