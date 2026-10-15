import 'package:flutter/material.dart';

import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';
import '../../../shared/widgets/state_views.dart';

/// One throughput figure. Tabular figures so the numbers do not jitter as
/// they tick -- a detail that makes live stats feel solid rather than noisy.
class StatTile extends StatelessWidget {
  const StatTile({
    super.key,
    required this.label,
    required this.value,
    required this.icon,
    this.valueColor,
    this.isPlaceholder = false,
  });

  final String label;
  final String value;
  final IconData icon;
  final Color? valueColor;

  /// True before a session starts: the tile keeps its shape but dims, so the
  /// grid does not reflow the moment the first sample arrives.
  final bool isPlaceholder;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Semantics(
      label: '$label: ${isPlaceholder ? 'no data yet' : value}',
      excludeSemantics: true,
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.surfaceContainer,
          borderRadius: BorderRadius.circular(AppRadius.card),
          border: Border.all(color: tokens.border),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(icon, size: 16, color: tokens.mutedForeground),
                const SizedBox(width: AppSpacing.sm - 2),
                Expanded(
                  child: Text(
                    label.toUpperCase(),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: context.text.labelSmall,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            if (isPlaceholder)
              const SkeletonBox(width: 76, height: 20)
            else
              FittedBox(
                fit: BoxFit.scaleDown,
                alignment: Alignment.centerLeft,
                child: Text(
                  value,
                  style: context.text.titleLarge?.copyWith(
                    color: valueColor ?? tokens.subtleForeground,
                    fontFeatures: const <FontFeature>[
                      FontFeature.tabularFigures(),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
