import 'package:flutter/material.dart';

import '../../../core/routing/proxy_engine.dart';
import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';
import '../../../data/models/chain_hop.dart';
import '../../../data/models/proxy_chain.dart';
import '../../../shared/format.dart';
import '../../../shared/widgets/badges.dart';
import '../../../shared/widgets/surface.dart';

/// Compact read-out of the route currently loaded into the engine.
class ChainSummaryCard extends StatelessWidget {
  const ChainSummaryCard({
    super.key,
    required this.chain,
    required this.status,
    required this.onEdit,
  });

  final ProxyChain chain;
  final EngineStatus status;
  final VoidCallback onEdit;

  HopHealth? _healthFor(int position) {
    for (final h in status.hopHealth) {
      if (h.position == position) return h;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;

    return Surface(
      padding: const EdgeInsets.all(AppSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    const SectionLabel('Active route'),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      chain.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: context.text.titleMedium,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: AppTouch.minGap),
              TextButton.icon(
                onPressed: onEdit,
                icon: const Icon(Icons.tune, size: 18),
                label: const Text('Edit'),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          if (chain.isEmpty)
            Text(
              'No hops configured. Open the chain builder to pick a route.',
              style: context.text.bodySmall,
            )
          else
            Column(
              children: <Widget>[
                for (var i = 0; i < chain.hops.length; i++)
                  Padding(
                    key: ValueKey<int>(chain.hops[i].proxy.id),
                    padding: EdgeInsets.only(
                      bottom: i == chain.hops.length - 1 ? 0 : AppSpacing.sm,
                    ),
                    child: _HopRow(
                      index: i,
                      hop: chain.hops[i],
                      health: _healthFor(i),
                    ),
                  ),
              ],
            ),
          if (!chain.isEmpty) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            const Divider(),
            const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
            Row(
              children: <Widget>[
                Icon(Icons.timer_outlined, size: 16, color: tokens.mutedForeground),
                const SizedBox(width: AppSpacing.sm - 2),
                Expanded(
                  child: Text(
                    status.isConnected
                        ? 'Measured end to end'
                        : 'Estimated end to end',
                    style: context.text.bodySmall,
                  ),
                ),
                Text(
                  '${Format.latency(chain.estimatedLatencyMs)}'
                  '–${Format.latency(chain.worstCaseLatencyMs)}',
                  style: context.text.titleSmall?.copyWith(
                    color: chain.latencyExpectation == LatencyExpectation.severe
                        ? tokens.warning
                        : tokens.subtleForeground,
                    fontFeatures: const <FontFeature>[
                      FontFeature.tabularFigures(),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _HopRow extends StatelessWidget {
  const _HopRow({
    required this.index,
    required this.hop,
    required this.health,
  });

  final int index;
  final ChainHop hop;
  final HopHealth? health;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final proxy = hop.proxy;
    final up = health?.isUp ?? proxy.isAlive;
    final color = up ? tokens.accent : tokens.destructiveText;

    return Row(
      children: <Widget>[
        Container(
          width: 24,
          height: 24,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(color: color.withValues(alpha: 0.6)),
            color: color.withValues(alpha: 0.12),
          ),
          child: Text(
            '${index + 1}',
            style: context.text.labelSmall?.copyWith(color: color),
          ),
        ),
        const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
        Expanded(
          child: Text(
            '${proxy.countryCode} · ${proxy.city}',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: context.text.bodyLarge?.copyWith(fontSize: 14),
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        ProtocolBadge(proxy.protocol),
        const SizedBox(width: AppSpacing.sm),
        SizedBox(
          width: 56,
          child: Text(
            health?.rttMs != null
                ? Format.latency(health!.rttMs!)
                : Format.latency(proxy.latencyMs),
            textAlign: TextAlign.right,
            style: context.text.bodySmall?.copyWith(
              color: color,
              fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
            ),
          ),
        ),
      ],
    );
  }
}
