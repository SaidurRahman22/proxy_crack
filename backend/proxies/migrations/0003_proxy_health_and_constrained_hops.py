"""Per-hop health checking and positional failover (roadmap item 1).

Three things, all additive:

* `ProxyProbe` -- one row per health observation. The only table in the
  schema that grows without a user action, hence `prune_probes`.
* Health fields on `Proxy` -- a denormalised cache of the probe table,
  rebuilt by `proxies.health.recompute_health`. Every new column carries a
  default, so existing rows come out of this migration in the "unprobed"
  state rather than as "dead", which is the safe direction: an unprobed
  proxy is still selectable, a dead one is not.
* Constrained hops -- `ChainHop.proxy` becomes nullable and five
  constraint columns appear beside it. **Backwards compatible by
  construction**: every existing hop has a non-null `proxy` and blank
  constraints, which is exactly the "fixed hop" case, so nothing saved
  before this migration changes behaviour.

`uniq_proxy_per_chain` is dropped and re-added with
`condition=proxy IS NOT NULL`. Without the condition the rule would depend
on how a given backend treats NULLs in a unique index, and several
constrained hops in one chain -- the normal case for this feature -- must
not collide with each other.
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('proxies', '0002_encrypt_proxy_credentials'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProxyProbe',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('checked_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('succeeded', models.BooleanField()),
                ('latency_ms', models.PositiveIntegerField(blank=True, help_text='Round trip of a full CONNECT through the proxy to the target. Null on failure -- a failed probe has no latency, and storing the timeout duration there would poison the percentiles.', null=True)),
                ('error_kind', models.CharField(blank=True, choices=[('timeout', 'Timed out'), ('refused', 'Connection refused'), ('auth_failed', 'Authentication rejected'), ('dns', 'Name resolution failed'), ('tls', 'TLS failure'), ('protocol', 'Protocol error'), ('unknown', 'Unknown')], help_text='Empty on success.', max_length=20)),
                ('target', models.CharField(blank=True, help_text='host:port the probe CONNECTed to. Recorded per row because when the target itself goes down, every proxy looks dead and this column is the only way to tell.', max_length=120)),
            ],
            options={
                'verbose_name': 'proxy probe',
                'verbose_name_plural': 'proxy probes',
                'ordering': ('-checked_at', '-pk'),
            },
        ),
        migrations.RemoveConstraint(
            model_name='chainhop',
            name='uniq_proxy_per_chain',
        ),
        migrations.AddField(
            model_name='chainhop',
            name='country',
            field=models.CharField(blank=True, help_text='Blank means any. Matched case-insensitively against Proxy.country.', max_length=64),
        ),
        migrations.AddField(
            model_name='chainhop',
            name='max_latency_ms',
            field=models.PositiveIntegerField(blank=True, help_text='Ceiling on latency_p95_ms, not on the mean. An unprobed proxy has no p95 and therefore cannot satisfy this.', null=True),
        ),
        migrations.AddField(
            model_name='chainhop',
            name='min_success_rate',
            field=models.FloatField(blank=True, help_text='Floor on the rolling success rate, 0.0-1.0. An unprobed proxy has no rate and therefore cannot satisfy this.', null=True, validators=[django.core.validators.MinValueValidator(0.0), django.core.validators.MaxValueValidator(1.0)]),
        ),
        migrations.AddField(
            model_name='chainhop',
            name='protocol',
            field=models.CharField(blank=True, choices=[('ss', 'Shadowsocks'), ('socks5', 'SOCKS5'), ('http', 'HTTP')], help_text='Blank means any. Note an http hop makes the whole chain TCP-only (architecture.md section 1).', max_length=10),
        ),
        migrations.AddField(
            model_name='chainhop',
            name='tier',
            field=models.CharField(blank=True, choices=[('free', 'Free'), ('premium', 'Premium')], help_text='Blank means any tier the plan allows. Never widens entitlement -- the resolver intersects this with the plan.', max_length=20),
        ),
        migrations.AddField(
            model_name='proxy',
            name='consecutive_failures',
            field=models.PositiveIntegerField(db_index=True, default=0, help_text='Trailing failures in the probe history. Reset to 0 by any success. This, not the last probe, decides `is_alive`.'),
        ),
        migrations.AddField(
            model_name='proxy',
            name='latency_p50_ms',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='proxy',
            name='latency_p95_ms',
            field=models.PositiveIntegerField(blank=True, help_text='95th percentile over the window. A proxy with a good mean and a terrible tail is a bad hop, and a mean hides that.', null=True),
        ),
        migrations.AddField(
            model_name='proxy',
            name='probe_count',
            field=models.PositiveIntegerField(default=0, help_text='Probes inside the current rolling window -- the sample `success_rate` is computed from. A 100%% rate over 2 probes and over 200 are not the same claim, and the scorer treats them differently.'),
        ),
        migrations.AddField(
            model_name='proxy',
            name='quality_score',
            field=models.FloatField(db_index=True, default=50.0, help_text='0-100. Reliability is primary, latency secondary -- see proxies.health.compute_quality_score. Stored rather than computed so the resolver can rank candidates in SQL.'),
        ),
        migrations.AddField(
            model_name='proxy',
            name='success_rate',
            field=models.FloatField(blank=True, db_index=True, help_text='Successes / probes over the rolling window (0.0-1.0), NOT lifetime. Null until the proxy has been probed at least once.', null=True),
        ),
        migrations.AlterField(
            model_name='chainhop',
            name='proxy',
            field=models.ForeignKey(blank=True, help_text='Set for a fixed hop; null for a constrained one.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='chain_hops', to='proxies.proxy'),
        ),
        migrations.AlterField(
            model_name='proxy',
            name='is_alive',
            field=models.BooleanField(db_index=True, default=True, help_text='False once `consecutive_failures` reaches settings.PROXY_DEAD_AFTER_FAILURES. One failed probe is noise, not death.'),
        ),
        migrations.AlterField(
            model_name='proxy',
            name='latency_ms',
            field=models.PositiveIntegerField(blank=True, help_text='Latency of the most recent SUCCESSFUL probe. Kept for the Phase 1 API contract; latency_p95_ms is the field to rank on.', null=True),
        ),
        migrations.AddIndex(
            model_name='proxy',
            index=models.Index(fields=['is_enabled', 'is_alive', 'tier', '-quality_score'], name='proxy_resolve_idx'),
        ),
        migrations.AddConstraint(
            model_name='chainhop',
            constraint=models.UniqueConstraint(condition=models.Q(('proxy__isnull', False)), fields=('chain', 'proxy'), name='uniq_proxy_per_chain'),
        ),
        migrations.AddConstraint(
            model_name='chainhop',
            constraint=models.CheckConstraint(condition=models.Q(('proxy__isnull', True), models.Q(('country', ''), ('max_latency_ms__isnull', True), ('min_success_rate__isnull', True), ('protocol', ''), ('tier', '')), _connector='OR'), name='hop_is_fixed_or_constrained'),
        ),
        migrations.AddField(
            model_name='proxyprobe',
            name='proxy',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='probes', to='proxies.proxy'),
        ),
        migrations.AddIndex(
            model_name='proxyprobe',
            index=models.Index(fields=['proxy', '-checked_at'], name='probe_proxy_time_idx'),
        ),
        migrations.AddIndex(
            model_name='proxyprobe',
            index=models.Index(fields=['checked_at', 'succeeded'], name='probe_time_ok_idx'),
        ),
    ]
