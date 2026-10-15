/// Spacing, radius, motion and sizing tokens from MASTER.md
/// (Density 7/10 "Standard", Motion 4/10 "Standard").
abstract final class AppSpacing {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 16;
  static const double lg = 24;
  static const double xl = 32;
  static const double xxl = 48;
  static const double xxxl = 64;
}

abstract final class AppRadius {
  /// Buttons / inputs / badges (MASTER.md: 8px).
  static const double control = 8;

  /// Cards (MASTER.md: 12px).
  static const double card = 12;

  /// Modals / sheets (MASTER.md: 16px).
  static const double modal = 16;

  static const double pill = 999;
}

/// Motion tokens. MASTER.md: subtle hover 200-250ms, stagger 300-450ms.
abstract final class AppMotion {
  static const Duration fast = Duration(milliseconds: 200);
  static const Duration normal = Duration(milliseconds: 250);
  static const Duration slow = Duration(milliseconds: 450);

  /// Pulse period for the "connecting" indicator.
  static const Duration pulse = Duration(milliseconds: 1200);
}

/// Hard accessibility floors for this app.
abstract final class AppTouch {
  /// Minimum touch target, logical px. Android/Material says 48; the brief
  /// says 44. We take the stricter of the two.
  static const double minTarget = 48;

  /// Minimum gap between adjacent touch targets.
  static const double minGap = 8;
}
