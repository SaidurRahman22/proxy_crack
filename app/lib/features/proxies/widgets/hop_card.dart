import 'package:flutter/material.dart';

import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';
import '../../../data/models/chain_hop.dart';
import '../../../shared/format.dart';
import '../../../shared/widgets/badges.dart';

/// One hop in the chain builder.
///
/// Shows what the hop *costs*, not just what it is: the running total after
/// this hop is the number that explains why a long chain feels slow.
class HopCard extends StatelessWidget {
  const HopCard({
    super.key,
    required this.hop,
    required this.runningLatencyMs,
    required this.index,
    required this.editable,
    required this.onRemove,
  });

  final ChainHop hop;

  /// Cumulative estimated latency through this hop.
  final int runningLatencyMs;

  final int index;
  final bool editable;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final scheme = Theme.of(context).colorScheme;
    final proxy = hop.proxy;
    final dead = !proxy.isAlive;
    final accentColor = dead ? tokens.destructiveText : tokens.accent;

    return Semantics(
      label: 'Hop ${index + 1}: ${proxy.city}, ${proxy.country}, '
          '${proxy.protocol.label}. '
          'Running latency $runningLatencyMs milliseconds.',
      excludeSemantics: true,
      child: Container(
        decoration: BoxDecoration(
          color: scheme.surfaceContainer,
          borderRadius: BorderRadius.circular(AppRadius.card),
          border: Border.all(
            color: dead ? accentColor.withValues(alpha: 0.45) : tokens.border,
          ),
        ),
        padding: const EdgeInsets.all(AppSpacing.sm + AppSpacing.xs),
        child: Row(
          children: <Widget>[
            Container(
              width: 32,
              height: 32,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: accentColor.withValues(alpha: 0.14),
                border: Border.all(color: accentColor.withValues(alpha: 0.55)),
              ),
              child: Text(
                '${index + 1}',
                style: context.text.titleSmall?.copyWith(color: accentColor),
              ),
            ),
            const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(
                    proxy.location,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: context.text.titleSmall,
                  ),
                  const SizedBox(height: AppSpacing.xs + 2),
                  Wrap(
                    spacing: AppSpacing.sm - 2,
                    runSpacing: AppSpacing.xs,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: <Widget>[
                      ProtocolBadge(proxy.protocol),
                      TagChip(
                        label: '+${Format.latency(proxy.latencyMs)}',
                        color: tokens.mutedForeground,
                        dense: true,
                      ),
                      TagChip(
                        label:
                            'total ${Format.latency(runningLatencyMs)}',
                        color: runningLatencyMs > 400
                            ? tokens.warning
                            : tokens.subtleForeground,
                        dense: true,
                      ),
                      if (dead)
                        TagChip(
                          label: 'OFFLINE',
                          icon: Icons.error_outline,
                          color: tokens.destructiveText,
                          dense: true,
                        ),
                    ],
                  ),
                ],
              ),
            ),
            if (editable) ...<Widget>[
              const SizedBox(width: AppSpacing.xs),
              IconButton(
                onPressed: onRemove,
                tooltip: 'Remove hop ${index + 1}',
                iconSize: 20,
                constraints: const BoxConstraints(
                  minWidth: AppTouch.minTarget,
                  minHeight: AppTouch.minTarget,
                ),
                icon: Icon(
                  Icons.remove_circle_outline,
                  color: tokens.destructiveText,
                ),
              ),
              ReorderableDragStartListener(
                index: index,
                child: Tooltip(
                  message: 'Drag to reorder',
                  child: Container(
                    width: AppTouch.minTarget,
                    height: AppTouch.minTarget,
                    alignment: Alignment.center,
                    child: Icon(
                      Icons.drag_handle,
                      size: 22,
                      color: tokens.mutedForeground,
                    ),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
