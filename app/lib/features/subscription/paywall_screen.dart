import 'package:flutter/material.dart';

import '../../app/app_controller.dart';
import '../../app/app_scope.dart';
import '../../core/layout/breakpoints.dart';
import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/models/plan.dart';
import '../../data/models/subscription.dart';
import '../../shared/format.dart';
import '../../shared/widgets/state_views.dart';
import '../../shared/widgets/surface.dart';
import 'widgets/plan_card.dart';

/// Plan comparison and upgrade.
///
/// The purchase here is a stand-in. Real billing goes through Google Play and
/// is validated server-side (architecture.md section 4); nothing the client
/// says about entitlement is trusted.
class PaywallScreen extends StatelessWidget {
  const PaywallScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);

    return SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final size = WindowSize.fromWidth(constraints.maxWidth);
          final gutter = Layout.gutter(size);

          return controller.plans.map(
            loading: () => const _PaywallSkeleton(),
            error: (message, detail) => ErrorStateView(
              message: message,
              detail: 'Plan details could not be loaded, so nothing can be '
                  'purchased right now.',
              onRetry: controller.load,
            ),
            data: (plans) {
              if (plans.isEmpty) {
                return MessageState(
                  icon: Icons.sell_outlined,
                  title: 'No plans available',
                  message: 'There are no plans published for your region yet. '
                      'The free tier stays active in the meantime.',
                  primaryActionLabel: 'Reload',
                  onPrimaryAction: controller.load,
                );
              }
              return _PaywallBody(
                controller: controller,
                plans: plans,
                gutter: gutter,
                maxWidth: constraints.maxWidth,
              );
            },
          );
        },
      ),
    );
  }
}

class _PaywallBody extends StatelessWidget {
  const _PaywallBody({
    required this.controller,
    required this.plans,
    required this.gutter,
    required this.maxWidth,
  });

  final AppController controller;
  final List<Plan> plans;
  final double gutter;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    final subscription = controller.subscription.valueOrNull;
    final busy = controller.subscription.isLoading;
    final columns = Layout.columnsFor(maxWidth);
    const gap = AppSpacing.md;
    final contentWidth = maxWidth - gutter * 2;
    final cardWidth = columns == 1
        ? contentWidth
        : (contentWidth - gap * (columns - 1)) / columns;

    return ListView(
      padding: EdgeInsets.fromLTRB(gutter, AppSpacing.md, gutter, AppSpacing.xxl),
      children: <Widget>[
        Text('Plans', style: context.text.headlineSmall),
        const SizedBox(height: AppSpacing.xs),
        Text(
          'Chain depth is the thing you are buying. The free tier gives you '
          'one hop; paid plans let you stack them.',
          style: context.text.bodyMedium,
        ),
        const SizedBox(height: AppSpacing.lg),
        if (subscription != null) ...<Widget>[
          _CurrentPlanCard(subscription: subscription),
          const SizedBox(height: AppSpacing.lg),
        ],
        const SectionLabel('Compare'),
        const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
        Wrap(
          spacing: gap,
          runSpacing: gap,
          children: <Widget>[
            for (final plan in plans)
              SizedBox(
                width: cardWidth.clamp(0.0, Layout.cardMaxWidth),
                child: PlanCard(
                  key: ValueKey<int>(plan.id),
                  plan: plan,
                  isCurrent: subscription?.plan.id == plan.id,
                  isRecommended: plan.id == 2,
                  busy: busy,
                  onSelect: () => _confirm(context, plan),
                ),
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.lg),
        Surface(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Icon(
                Icons.verified_outlined,
                size: 20,
                color: context.tokens.mutedForeground,
              ),
              const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
              Expanded(
                child: Text(
                  'Subscriptions are billed through Google Play and can be '
                  'cancelled there at any time. Entitlements are verified on '
                  'our servers, so a plan change applies to every device on '
                  'your account.',
                  style: context.text.bodySmall,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Future<void> _confirm(BuildContext context, Plan plan) async {
    final controller = AppScope.read(context);
    final messenger = ScaffoldMessenger.maybeOf(context);

    final confirmed = await showModalBottomSheet<bool>(
      context: context,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.md,
            0,
            AppSpacing.md,
            AppSpacing.md,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Text(
                plan.isFreeTier
                    ? 'Switch to ${plan.name}?'
                    : 'Subscribe to ${plan.name}',
                style: context.text.titleLarge,
              ),
              const SizedBox(height: AppSpacing.sm),
              Text(
                plan.isFreeTier
                    ? 'You will drop to ${plan.chainLabel.toLowerCase()} and '
                        'the free proxy pool.'
                    : '${plan.priceLabel} ${plan.periodLabel}. '
                        'This demo build does not charge anything — real '
                        'billing goes through Google Play.',
                style: context.text.bodyMedium,
              ),
              const SizedBox(height: AppSpacing.lg),
              FilledButton(
                onPressed: () => Navigator.of(sheetContext).pop(true),
                child: Text(plan.isFreeTier ? 'Switch' : 'Continue'),
              ),
              const SizedBox(height: AppTouch.minGap),
              TextButton(
                onPressed: () => Navigator.of(sheetContext).pop(false),
                child: const Text('Not now'),
              ),
            ],
          ),
        ),
      ),
    );

    if (confirmed != true) return;
    await controller.simulatePurchase(plan);
    messenger
      ?..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(content: Text('You are now on ${plan.name}.')),
      );
  }
}

class _CurrentPlanCard extends StatelessWidget {
  const _CurrentPlanCard({required this.subscription});

  final Subscription subscription;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final plan = subscription.plan;
    final used = subscription.bandwidthUsedFraction;

    return Surface(
      padding: const EdgeInsets.all(AppSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const SectionLabel('Current plan'),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: <Widget>[
              Expanded(
                child: Text(plan.name, style: context.text.titleLarge),
              ),
              Text(
                subscription.status.label,
                style: context.text.titleSmall?.copyWith(
                  color: subscription.isActive
                      ? tokens.accent
                      : tokens.destructiveText,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            plan.isFreeTier
                ? plan.chainLabel
                : '${plan.chainLabel} · renews in '
                    '${subscription.daysRemaining} days',
            style: context.text.bodySmall,
          ),
          if (used != null) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            Row(
              children: <Widget>[
                Expanded(
                  child: Text('Bandwidth used', style: context.text.bodySmall),
                ),
                Text(
                  '${Format.bytes(subscription.bytesUsed)} / '
                  '${Format.bytes(plan.bandwidthLimitBytes)}',
                  style: context.text.bodySmall?.copyWith(
                    color: tokens.subtleForeground,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            Semantics(
              label: 'Bandwidth used: ${(used * 100).round()} percent',
              child: ClipRRect(
                borderRadius: BorderRadius.circular(AppRadius.pill),
                child: LinearProgressIndicator(
                  value: used,
                  minHeight: 6,
                  backgroundColor: tokens.borderStrong,
                  color: used > 0.85 ? tokens.warning : tokens.accent,
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _PaywallSkeleton extends StatelessWidget {
  const _PaywallSkeleton();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(AppSpacing.md),
      children: <Widget>[
        const SkeletonBox(width: 120, height: 26),
        const SizedBox(height: AppSpacing.md),
        const SkeletonBox(height: 14),
        const SizedBox(height: AppSpacing.lg),
        for (var i = 0; i < 3; i++)
          Padding(
            key: ValueKey<String>('plan-skeleton-$i'),
            padding: const EdgeInsets.only(bottom: AppSpacing.md),
            child: Surface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: const <Widget>[
                  SkeletonBox(width: 90, height: 20),
                  SizedBox(height: AppSpacing.md),
                  SkeletonBox(width: 140, height: 28),
                  SizedBox(height: AppSpacing.md),
                  SkeletonBox(height: 12),
                  SizedBox(height: AppSpacing.sm),
                  SkeletonBox(height: 12),
                  SizedBox(height: AppSpacing.lg),
                  SkeletonBox(height: 44, radius: AppRadius.control),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
