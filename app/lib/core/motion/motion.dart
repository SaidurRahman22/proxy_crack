import 'package:flutter/material.dart';

import '../theme/app_dimens.dart';

/// Reduced-motion helpers.
///
/// Android surfaces "Remove animations" through
/// `MediaQueryData.disableAnimations`. Every animation in this app routes its
/// duration through [MotionScope.duration] so that one switch silences the
/// lot without any widget needing to branch.
abstract final class MotionScope {
  /// True when the user asked the platform to remove animations.
  static bool reduced(BuildContext context) =>
      MediaQuery.maybeDisableAnimationsOf(context) ?? false;

  /// [d], or [Duration.zero] when the user has reduced motion enabled.
  static Duration duration(BuildContext context, Duration d) =>
      reduced(context) ? Duration.zero : d;

  /// Looping/ambient animations (pulses, shimmers) must not run at all under
  /// reduced motion -- a zero-length repeat would spin the scheduler.
  static bool allowAmbient(BuildContext context) => !reduced(context);
}

/// A drop-in [AnimatedContainer] that honours reduced motion.
class MotionContainer extends StatelessWidget {
  const MotionContainer({
    super.key,
    this.duration = AppMotion.fast,
    this.curve = Curves.easeOutCubic,
    this.decoration,
    this.padding,
    this.width,
    this.height,
    this.alignment,
    this.child,
  });

  final Duration duration;
  final Curve curve;
  final Decoration? decoration;
  final EdgeInsetsGeometry? padding;
  final double? width;
  final double? height;
  final AlignmentGeometry? alignment;
  final Widget? child;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: MotionScope.duration(context, duration),
      curve: curve,
      decoration: decoration,
      padding: padding,
      width: width,
      height: height,
      alignment: alignment,
      child: child,
    );
  }
}

/// Cross-fades between children, or swaps instantly under reduced motion.
class MotionSwitcher extends StatelessWidget {
  const MotionSwitcher({
    super.key,
    required this.child,
    this.duration = AppMotion.fast,
  });

  final Widget child;
  final Duration duration;

  @override
  Widget build(BuildContext context) {
    return AnimatedSwitcher(
      duration: MotionScope.duration(context, duration),
      switchInCurve: Curves.easeOut,
      switchOutCurve: Curves.easeIn,
      child: child,
    );
  }
}
