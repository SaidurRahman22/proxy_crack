import 'package:flutter/material.dart';

import '../../core/motion/motion.dart';
import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/api/api_failure.dart';
import 'surface.dart';

/// A placeholder block that breathes while content loads.
///
/// Under reduced motion it renders as a flat block -- the skeleton still
/// communicates layout, it just stops moving.
class SkeletonBox extends StatefulWidget {
  const SkeletonBox({
    super.key,
    this.width,
    this.height = 14,
    this.radius = AppRadius.control,
  });

  final double? width;
  final double height;
  final double radius;

  @override
  State<SkeletonBox> createState() => _SkeletonBoxState();
}

class _SkeletonBoxState extends State<SkeletonBox>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: AppMotion.pulse,
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (MotionScope.allowAmbient(context)) {
      if (!_controller.isAnimating) _controller.repeat(reverse: true);
    } else {
      _controller.stop();
      _controller.value = 0.5;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final base = context.tokens.borderStrong;
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final t = 0.45 + _controller.value * 0.35;
        return Container(
          width: widget.width,
          height: widget.height,
          decoration: BoxDecoration(
            color: base.withValues(alpha: 0.10 + t * 0.12),
            borderRadius: BorderRadius.circular(widget.radius),
          ),
        );
      },
    );
  }
}

/// Skeleton rows shaped like the proxy list, so the layout does not jump when
/// real data lands.
class ListSkeleton extends StatelessWidget {
  const ListSkeleton({super.key, this.rows = 7, this.label});

  final int rows;
  final String? label;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: label ?? 'Loading',
      liveRegion: true,
      child: ListView.separated(
        padding: const EdgeInsets.all(AppSpacing.md),
        itemCount: rows,
        physics: const NeverScrollableScrollPhysics(),
        separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
        itemBuilder: (context, index) => Surface(
          key: ValueKey<String>('skeleton-$index'),
          child: Row(
            children: <Widget>[
              const SkeletonBox(width: 44, height: 44, radius: AppRadius.control),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: const <Widget>[
                    SkeletonBox(width: 140, height: 15),
                    SizedBox(height: AppSpacing.sm),
                    SkeletonBox(width: 200, height: 12),
                  ],
                ),
              ),
              const SizedBox(width: AppSpacing.md),
              const SkeletonBox(width: 52, height: 24),
            ],
          ),
        ),
      ),
    );
  }
}

/// Shared chrome for empty and error states: icon, headline, explanation and
/// at most two actions. Never a bare spinner, never a dead end.
class MessageState extends StatelessWidget {
  const MessageState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.iconColor,
    this.primaryActionLabel,
    this.onPrimaryAction,
    this.secondaryActionLabel,
    this.onSecondaryAction,
  });

  final IconData icon;
  final String title;
  final String message;
  final Color? iconColor;
  final String? primaryActionLabel;
  final VoidCallback? onPrimaryAction;
  final String? secondaryActionLabel;
  final VoidCallback? onSecondaryAction;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: <Widget>[
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.surfaceContainer,
                  borderRadius: BorderRadius.circular(AppRadius.card),
                  border: Border.all(color: tokens.border),
                ),
                child: Icon(
                  icon,
                  size: 26,
                  color: iconColor ?? tokens.mutedForeground,
                ),
              ),
              const SizedBox(height: AppSpacing.md),
              Text(
                title,
                textAlign: TextAlign.center,
                style: context.text.titleMedium,
              ),
              const SizedBox(height: AppSpacing.sm),
              Text(
                message,
                textAlign: TextAlign.center,
                style: context.text.bodyMedium,
              ),
              if (onPrimaryAction != null) ...<Widget>[
                const SizedBox(height: AppSpacing.lg),
                FilledButton(
                  onPressed: onPrimaryAction,
                  child: Text(primaryActionLabel ?? 'Retry'),
                ),
              ],
              if (onSecondaryAction != null) ...<Widget>[
                const SizedBox(height: AppTouch.minGap),
                TextButton(
                  onPressed: onSecondaryAction,
                  child: Text(secondaryActionLabel ?? 'Back'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Error presentation driven by the *typed* failure rather than by a string.
///
/// Four outcomes deserve four different screens, and a user told "something
/// went wrong" for all of them learns nothing:
///
/// * **Offline / unreachable** — the device, the Wi-Fi or the dev server.
///   Retry is the right action and the base URL is worth naming, because on
///   an emulator the usual cause is pointing at `127.0.0.1` instead of
///   `10.0.2.2`.
/// * **429 throttled** — not a fault. The backend's scopes are deliberately
///   tight (credentials 60/hour, chain writes 60/hour, content 60/hour) so
///   hitting one is a normal operational event. The server's own message
///   names the wait; it is shown verbatim rather than replaced with
///   "Error 429".
/// * **403 entitlement** — the plan, not the network. Retrying changes
///   nothing, so the primary action is the plans screen when one is wired.
/// * Everything else falls back to the generic error view.
class FailureStateView extends StatelessWidget {
  const FailureStateView({
    super.key,
    required this.message,
    this.detail,
    this.failure,
    this.onRetry,
    this.onViewPlans,
  });

  final String message;
  final String? detail;
  final ApiFailure? failure;
  final VoidCallback? onRetry;
  final VoidCallback? onViewPlans;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final f = failure;

    if (f != null && f.isOffline) {
      return MessageState(
        icon: Icons.wifi_off_outlined,
        iconColor: tokens.warning,
        title: 'No connection to the server',
        message: f.detailLine ??
            'The app could not reach the API. Check the device network and '
                'that the backend is running.',
        primaryActionLabel: 'Try again',
        onPrimaryAction: onRetry,
      );
    }

    if (f is ApiException && f.isThrottled) {
      return MessageState(
        icon: Icons.hourglass_empty_outlined,
        iconColor: tokens.warning,
        title: 'Rate limit reached',
        message: f.message,
        primaryActionLabel: 'Try again',
        onPrimaryAction: onRetry,
      );
    }

    if (f is ApiException &&
        f.isForbidden &&
        (f.hasCode(ApiErrorCode.proxyNotEntitled) ||
            f.hasCode(ApiErrorCode.chainLengthExceeded))) {
      return MessageState(
        icon: Icons.workspace_premium_outlined,
        iconColor: tokens.warning,
        title: 'Your plan does not cover this',
        message: f.message,
        primaryActionLabel: onViewPlans == null ? null : 'See plans',
        onPrimaryAction: onViewPlans,
        secondaryActionLabel: 'Try again',
        onSecondaryAction: onRetry,
      );
    }

    return ErrorStateView(
      message: message,
      detail: detail ?? f?.detailLine,
      onRetry: onRetry,
    );
  }
}

/// A compact failure line for a panel that is one part of a screen, where a
/// full-height [MessageState] would push everything else out of view.
class FailureInline extends StatelessWidget {
  const FailureInline({
    super.key,
    required this.message,
    this.detail,
    this.failure,
    this.onRetry,
  });

  final String message;
  final String? detail;
  final ApiFailure? failure;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final f = failure;
    final throttled = f is ApiException && f.isThrottled;
    final offline = f?.isOffline ?? false;
    final color = throttled || offline
        ? tokens.warning
        : tokens.destructiveText;

    return Semantics(
      liveRegion: true,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(
            offline
                ? Icons.wifi_off_outlined
                : throttled
                    ? Icons.hourglass_empty_outlined
                    : Icons.error_outline,
            size: 18,
            color: color,
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(message, style: context.text.bodySmall),
                if (detail != null) ...<Widget>[
                  const SizedBox(height: 2),
                  Text(
                    detail!,
                    style: context.text.bodySmall?.copyWith(
                      color: tokens.mutedForeground,
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (onRetry != null) ...<Widget>[
            const SizedBox(width: AppSpacing.sm),
            SizedBox(
              height: AppTouch.minTarget,
              child: TextButton(
                onPressed: onRetry,
                child: const Text('Retry'),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Error presentation for a failed async load.
class ErrorStateView extends StatelessWidget {
  const ErrorStateView({
    super.key,
    required this.message,
    this.detail,
    this.onRetry,
  });

  final String message;
  final String? detail;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return MessageState(
      icon: Icons.cloud_off_outlined,
      iconColor: context.tokens.destructiveText,
      title: message,
      message: detail ?? 'Check your connection and try again.',
      primaryActionLabel: 'Try again',
      onPrimaryAction: onRetry,
    );
  }
}
