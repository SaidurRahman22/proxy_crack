import 'package:flutter/material.dart';

/// Raw colour tokens from `design-system/proxy-crack/MASTER.md`.
///
/// Nothing outside `core/theme/` should reference this class directly --
/// widgets read colours from `Theme.of(context)` / `context.tokens`.
///
/// Contrast ratios quoted below are WCAG 2.1 ratios computed against
/// [background] (`#0F172A`) and [card] (`#192134`).
abstract final class AppColors {
  // --- MASTER.md palette -------------------------------------------------
  /// `--color-primary`
  static const Color primary = Color(0xFF1E3A5F);

  /// `--color-on-primary` (11.5:1 on [primary])
  static const Color onPrimary = Color(0xFFFFFFFF);

  /// `--color-secondary`
  static const Color secondary = Color(0xFF334155);

  /// `--color-on-secondary` (10.4:1 on [secondary])
  static const Color onSecondary = Color(0xFFFFFFFF);

  /// `--color-accent` -- also the "connected" signal colour.
  /// 7.8:1 on [background], 6.9:1 on [card].
  static const Color accent = Color(0xFF22C55E);

  /// `--color-on-accent` (7.8:1 on [accent])
  static const Color onAccent = Color(0xFF0F172A);

  /// `--color-background`
  static const Color background = Color(0xFF0F172A);

  /// `--color-foreground` (17.9:1 on [background])
  static const Color foreground = Color(0xFFFFFFFF);

  /// `--color-card`
  static const Color card = Color(0xFF192134);

  /// `--color-card-foreground` (15.8:1 on [card])
  static const Color cardForeground = Color(0xFFFFFFFF);

  /// `--color-muted`
  static const Color muted = Color(0xFF10192E);

  /// `--color-muted-foreground` (7.0:1 on [background], 6.2:1 on [card])
  static const Color mutedForeground = Color(0xFF94A3B8);

  /// `--color-border`
  static const Color border = Color(0x14FFFFFF); // rgba(255,255,255,0.08)

  /// `--color-destructive` -- FILL ONLY. As text on [background] it is
  /// 3.7:1, which fails the 4.5:1 bar; use [destructiveText] for glyphs.
  static const Color destructive = Color(0xFFDC2626);

  /// `--color-on-destructive` (4.8:1 on [destructive])
  static const Color onDestructive = Color(0xFFFFFFFF);

  /// `--color-ring`
  static const Color ring = Color(0xFFFFFFFF);

  // --- Contrast-driven extensions ---------------------------------------
  // These are NOT in MASTER.md. Each exists because a MASTER.md token
  // measured below 4.5:1 in the role it was needed for. See the report.

  /// Destructive text/icons on dark surfaces. 6.4:1 on [background],
  /// 5.7:1 on [card]. Hue-matched to [destructive].
  static const Color destructiveText = Color(0xFFF87171);

  /// Warning text/icons (latency expectation-setting).
  /// 10.7:1 on [background], 9.5:1 on [card].
  static const Color warning = Color(0xFFFBBF24);

  /// Secondary body text that sits on a [secondary] fill.
  /// [mutedForeground] on [secondary] is only 4.1:1; this is 7.0:1.
  static const Color subtleForeground = Color(0xFFCBD5E1);

  /// A slightly stronger border for focus rings and selected rows.
  static const Color borderStrong = Color(0x33FFFFFF); // 0.20 alpha

  /// Dimmed accent used for glows/tracks; never carries text.
  static const Color accentGlow = Color(0x3322C55E);
}
