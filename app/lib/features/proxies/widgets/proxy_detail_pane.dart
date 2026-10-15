import 'package:flutter/material.dart';

import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';
import '../../../data/models/proxy.dart';
import '../../../shared/format.dart';
import '../../../shared/widgets/badges.dart';
import '../../../shared/widgets/state_views.dart';
import '../../../shared/widgets/surface.dart';

/// Detail view for one proxy.
///
/// Rendered as the right-hand pane on wide windows and as a bottom sheet on
/// compact ones -- same widget, two hosts, so the desktop layout costs
/// nothing extra later.
class ProxyDetailPane extends StatelessWidget {
  const ProxyDetailPane({
    super.key,
    required this.proxy,
    required this.isInChain,
    required this.locked,
    required this.onToggleInChain,
    this.onViewPlans,
  });

  final Proxy? proxy;
  final bool isInChain;
  final bool locked;
  final VoidCallback onToggleInChain;
  final VoidCallback? onViewPlans;

  @override
  Widget build(BuildContext context) {
    final p = proxy;
    if (p == null) {
      return const MessageState(
        icon: Icons.touch_app_outlined,
        title: 'No proxy selected',
        message: 'Pick a server from the list to see its health history and '
            'add it to your chain.',
      );
    }

    final tokens = context.tokens;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              CountryBadge(p.countryCode),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      p.location,
                      style: context.text.titleLarge,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    AliveIndicator(isAlive: p.isAlive),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          Surface(
            child: Column(
              children: <Widget>[
                _DetailRow(label: 'Endpoint', value: p.endpoint),
                const Divider(height: AppSpacing.lg),
                _DetailRow(label: 'Protocol', value: p.protocol.label),
                const Divider(height: AppSpacing.lg),
                _DetailRow(
                  label: 'Single-hop latency',
                  value: p.isAlive ? Format.latency(p.latencyMs) : 'unknown',
                  valueColor: latencyColor(context, p.latencyGrade),
                ),
                const Divider(height: AppSpacing.lg),
                _DetailRow(label: 'Tier', value: p.tier.label),
                const Divider(height: AppSpacing.lg),
                _DetailRow(
                  label: 'Last health check',
                  value: Format.relativeTime(p.lastChecked),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          if (!p.isAlive)
            Container(
              padding: const EdgeInsets.all(AppSpacing.md),
              decoration: BoxDecoration(
                color: tokens.destructiveText.withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(AppRadius.card),
                border: Border.all(
                  color: tokens.destructiveText.withValues(alpha: 0.32),
                ),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Icon(
                    Icons.error_outline,
                    size: 20,
                    color: tokens.destructiveText,
                  ),
                  const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
                  Expanded(
                    child: Text(
                      'This host failed its last health check. A dead hop '
                      'kills the whole chain, so it cannot be added.',
                      style: context.text.bodySmall,
                    ),
                  ),
                ],
              ),
            ),
          if (locked)
            Padding(
              padding: const EdgeInsets.only(top: AppSpacing.sm),
              child: Text(
                'Premium servers need a paid plan. Your current plan can only '
                'use the free pool.',
                style: context.text.bodySmall,
              ),
            ),
          const SizedBox(height: AppSpacing.md),
          if (locked && onViewPlans != null)
            FilledButton.icon(
              onPressed: onViewPlans,
              icon: const Icon(Icons.workspace_premium_outlined, size: 20),
              label: const Text('See plans'),
            )
          else
            FilledButton.icon(
              onPressed: p.isAlive ? onToggleInChain : null,
              icon: Icon(
                isInChain ? Icons.remove_circle_outline : Icons.add_circle_outline,
                size: 20,
              ),
              label: Text(isInChain ? 'Remove from chain' : 'Add to chain'),
            ),
        ],
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({
    required this.label,
    required this.value,
    this.valueColor,
  });

  final String label;
  final String value;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Expanded(child: Text(label, style: context.text.bodyMedium)),
        const SizedBox(width: AppSpacing.md),
        Flexible(
          child: Text(
            value,
            textAlign: TextAlign.right,
            style: context.text.titleSmall?.copyWith(color: valueColor),
          ),
        ),
      ],
    );
  }
}
