import 'package:flutter/material.dart';

import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';
import '../../../data/models/plan.dart';
import '../../../shared/widgets/badges.dart';

/// One plan in the comparison.
///
/// The bullet list is generated from the plan's own limits, so the marketing
/// copy cannot drift away from what the server actually enforces.
class PlanCard extends StatelessWidget {
  const PlanCard({
    super.key,
    required this.plan,
    required this.isCurrent,
    required this.isRecommended,
    required this.onSelect,
    this.busy = false,
  });

  final Plan plan;
  final bool isCurrent;
  final bool isRecommended;
  final VoidCallback onSelect;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final scheme = Theme.of(context).colorScheme;
    final highlight = isRecommended && !isCurrent;

    return Semantics(
      container: true,
      label: '${plan.name} plan, ${plan.priceLabel} ${plan.periodLabel}'
          '${isCurrent ? ', your current plan' : ''}',
      child: Container(
        decoration: BoxDecoration(
          color: scheme.surfaceContainer,
          borderRadius: BorderRadius.circular(AppRadius.card),
          border: Border.all(
            color: highlight
                ? tokens.accent.withValues(alpha: 0.55)
                : isCurrent
                    ? tokens.borderStrong
                    : tokens.border,
            width: highlight ? 1.5 : 1,
          ),
        ),
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(plan.name, style: context.text.titleLarge),
                ),
                if (isCurrent)
                  TagChip(
                    label: 'CURRENT',
                    color: tokens.subtleForeground,
                    dense: true,
                  )
                else if (isRecommended)
                  TagChip(
                    label: 'BEST VALUE',
                    color: tokens.accent,
                    dense: true,
                  ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: <Widget>[
                Flexible(
                  child: Text(
                    plan.priceLabel,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: context.text.headlineMedium,
                  ),
                ),
                if (plan.periodLabel.isNotEmpty) ...<Widget>[
                  const SizedBox(width: AppSpacing.sm - 2),
                  Text(plan.periodLabel, style: context.text.bodySmall),
                ],
              ],
            ),
            if (plan.tagline.isNotEmpty) ...<Widget>[
              const SizedBox(height: AppSpacing.sm),
              Text(plan.tagline, style: context.text.bodySmall),
            ],
            const SizedBox(height: AppSpacing.md),
            const Divider(),
            const SizedBox(height: AppSpacing.md),
            for (final entitlement in plan.entitlements)
              Padding(
                key: ValueKey<String>('${plan.id}-$entitlement'),
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Icon(
                      Icons.check,
                      size: 16,
                      color: plan.isFreeTier
                          ? tokens.mutedForeground
                          : tokens.accent,
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: Text(
                        entitlement,
                        style: context.text.bodyMedium?.copyWith(
                          color: tokens.subtleForeground,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: AppSpacing.sm),
            SizedBox(
              width: double.infinity,
              child: isCurrent
                  ? OutlinedButton(
                      onPressed: null,
                      child: const Text('Your plan'),
                    )
                  : FilledButton(
                      onPressed: busy ? null : onSelect,
                      child: Text(
                        plan.isFreeTier
                            ? 'Switch to Free'
                            : 'Choose ${plan.name}',
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
