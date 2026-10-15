import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_colors.dart';
import 'app_dimens.dart';

/// Tokens that Material's [ColorScheme] has no slot for.
///
/// Read them with `Theme.of(context).extension<AppTokens>()!` or, more
/// conveniently, `context.tokens`.
@immutable
class AppTokens extends ThemeExtension<AppTokens> {
  const AppTokens({
    required this.accent,
    required this.onAccent,
    required this.accentGlow,
    required this.warning,
    required this.destructiveText,
    required this.subtleForeground,
    required this.mutedForeground,
    required this.border,
    required this.borderStrong,
    required this.muted,
  });

  /// Connected / CTA green.
  final Color accent;
  final Color onAccent;

  /// Low-alpha accent for glows and progress tracks. Never carries text.
  final Color accentGlow;

  /// Amber used for the compounding-latency warning.
  final Color warning;

  /// Red that is legible as *text* on dark surfaces.
  final Color destructiveText;

  /// Body text on a `secondary` fill.
  final Color subtleForeground;

  /// Secondary text on background/card.
  final Color mutedForeground;

  final Color border;
  final Color borderStrong;
  final Color muted;

  @override
  AppTokens copyWith({
    Color? accent,
    Color? onAccent,
    Color? accentGlow,
    Color? warning,
    Color? destructiveText,
    Color? subtleForeground,
    Color? mutedForeground,
    Color? border,
    Color? borderStrong,
    Color? muted,
  }) {
    return AppTokens(
      accent: accent ?? this.accent,
      onAccent: onAccent ?? this.onAccent,
      accentGlow: accentGlow ?? this.accentGlow,
      warning: warning ?? this.warning,
      destructiveText: destructiveText ?? this.destructiveText,
      subtleForeground: subtleForeground ?? this.subtleForeground,
      mutedForeground: mutedForeground ?? this.mutedForeground,
      border: border ?? this.border,
      borderStrong: borderStrong ?? this.borderStrong,
      muted: muted ?? this.muted,
    );
  }

  @override
  AppTokens lerp(ThemeExtension<AppTokens>? other, double t) {
    if (other is! AppTokens) return this;
    return AppTokens(
      accent: Color.lerp(accent, other.accent, t)!,
      onAccent: Color.lerp(onAccent, other.onAccent, t)!,
      accentGlow: Color.lerp(accentGlow, other.accentGlow, t)!,
      warning: Color.lerp(warning, other.warning, t)!,
      destructiveText: Color.lerp(destructiveText, other.destructiveText, t)!,
      subtleForeground: Color.lerp(subtleForeground, other.subtleForeground, t)!,
      mutedForeground: Color.lerp(mutedForeground, other.mutedForeground, t)!,
      border: Color.lerp(border, other.border, t)!,
      borderStrong: Color.lerp(borderStrong, other.borderStrong, t)!,
      muted: Color.lerp(muted, other.muted, t)!,
    );
  }

  static const AppTokens dark = AppTokens(
    accent: AppColors.accent,
    onAccent: AppColors.onAccent,
    accentGlow: AppColors.accentGlow,
    warning: AppColors.warning,
    destructiveText: AppColors.destructiveText,
    subtleForeground: AppColors.subtleForeground,
    mutedForeground: AppColors.mutedForeground,
    border: AppColors.border,
    borderStrong: AppColors.borderStrong,
    muted: AppColors.muted,
  );
}

extension AppThemeContext on BuildContext {
  AppTokens get tokens => Theme.of(this).extension<AppTokens>()!;
  TextTheme get text => Theme.of(this).textTheme;
  ColorScheme get colors => Theme.of(this).colorScheme;
}

/// The single source of truth for the visual language of the app.
abstract final class AppTheme {
  static const String fontFamily = 'Inter';

  /// Status/navigation bar styling for a dark, edge-to-edge shell.
  static const SystemUiOverlayStyle systemOverlay = SystemUiOverlayStyle(
    statusBarColor: Color(0x00000000),
    statusBarIconBrightness: Brightness.light,
    statusBarBrightness: Brightness.dark,
    systemNavigationBarColor: AppColors.background,
    systemNavigationBarIconBrightness: Brightness.light,
  );

  static const ColorScheme _scheme = ColorScheme(
    brightness: Brightness.dark,
    primary: AppColors.primary,
    onPrimary: AppColors.onPrimary,
    primaryContainer: AppColors.primary,
    onPrimaryContainer: AppColors.onPrimary,
    secondary: AppColors.secondary,
    onSecondary: AppColors.onSecondary,
    secondaryContainer: AppColors.secondary,
    onSecondaryContainer: AppColors.subtleForeground,
    tertiary: AppColors.accent,
    onTertiary: AppColors.onAccent,
    error: AppColors.destructive,
    onError: AppColors.onDestructive,
    errorContainer: AppColors.destructive,
    onErrorContainer: AppColors.onDestructive,
    surface: AppColors.background,
    onSurface: AppColors.foreground,
    surfaceContainerLowest: AppColors.muted,
    surfaceContainerLow: AppColors.muted,
    surfaceContainer: AppColors.card,
    surfaceContainerHigh: AppColors.card,
    surfaceContainerHighest: AppColors.card,
    onSurfaceVariant: AppColors.mutedForeground,
    outline: AppColors.borderStrong,
    outlineVariant: AppColors.border,
    inverseSurface: AppColors.foreground,
    onInverseSurface: AppColors.background,
    shadow: Color(0xFF000000),
    scrim: Color(0x99000000),
  );

  /// Swiss/minimal type scale. Tight tracking on headings, generous
  /// line-height on body copy. All sizes are logical px at textScale 1.0.
  static const TextTheme _textTheme = TextTheme(
    displaySmall: TextStyle(
      fontSize: 40,
      height: 1.1,
      fontWeight: FontWeight.w700,
      letterSpacing: -1.2,
      color: AppColors.foreground,
    ),
    headlineMedium: TextStyle(
      fontSize: 28,
      height: 1.2,
      fontWeight: FontWeight.w700,
      letterSpacing: -0.6,
      color: AppColors.foreground,
    ),
    headlineSmall: TextStyle(
      fontSize: 22,
      height: 1.25,
      fontWeight: FontWeight.w600,
      letterSpacing: -0.3,
      color: AppColors.foreground,
    ),
    titleLarge: TextStyle(
      fontSize: 20,
      height: 1.3,
      fontWeight: FontWeight.w600,
      letterSpacing: -0.2,
      color: AppColors.foreground,
    ),
    titleMedium: TextStyle(
      fontSize: 16,
      height: 1.35,
      fontWeight: FontWeight.w600,
      color: AppColors.foreground,
    ),
    titleSmall: TextStyle(
      fontSize: 14,
      height: 1.4,
      fontWeight: FontWeight.w600,
      color: AppColors.foreground,
    ),
    bodyLarge: TextStyle(
      fontSize: 16,
      height: 1.5,
      fontWeight: FontWeight.w400,
      color: AppColors.foreground,
    ),
    bodyMedium: TextStyle(
      fontSize: 14,
      height: 1.5,
      fontWeight: FontWeight.w400,
      color: AppColors.mutedForeground,
    ),
    bodySmall: TextStyle(
      fontSize: 13,
      height: 1.45,
      fontWeight: FontWeight.w400,
      color: AppColors.mutedForeground,
    ),
    labelLarge: TextStyle(
      fontSize: 15,
      height: 1.2,
      fontWeight: FontWeight.w600,
      letterSpacing: 0.1,
      color: AppColors.foreground,
    ),
    labelMedium: TextStyle(
      fontSize: 12,
      height: 1.2,
      fontWeight: FontWeight.w600,
      letterSpacing: 0.6,
      color: AppColors.mutedForeground,
    ),
    labelSmall: TextStyle(
      fontSize: 11,
      height: 1.2,
      fontWeight: FontWeight.w700,
      letterSpacing: 0.9,
      color: AppColors.mutedForeground,
    ),
  );

  static ThemeData get dark {
    final base = ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: _scheme,
      fontFamily: fontFamily,
      scaffoldBackgroundColor: AppColors.background,
      canvasColor: AppColors.background,
      visualDensity: VisualDensity.standard,
      extensions: const <ThemeExtension<dynamic>>[AppTokens.dark],
    );

    return base.copyWith(
      textTheme: _textTheme,
      primaryTextTheme: _textTheme,
      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.background,
        surfaceTintColor: Colors.transparent,
        foregroundColor: AppColors.foreground,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        systemOverlayStyle: systemOverlay,
        titleTextStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 22,
          height: 1.25,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.4,
          color: AppColors.foreground,
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: AppColors.border,
        thickness: 1,
        space: 1,
      ),
      iconTheme: const IconThemeData(color: AppColors.mutedForeground, size: 22),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.accent,
        linearTrackColor: AppColors.accentGlow,
        circularTrackColor: AppColors.accentGlow,
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.accent,
          foregroundColor: AppColors.onAccent,
          disabledBackgroundColor: AppColors.secondary,
          disabledForegroundColor: AppColors.subtleForeground,
          minimumSize: const Size(0, AppTouch.minTarget),
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: AppSpacing.sm + AppSpacing.xs,
          ),
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(AppRadius.control)),
          ),
          textStyle: const TextStyle(
            fontFamily: fontFamily,
            fontSize: 15,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.1,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.foreground,
          side: const BorderSide(color: AppColors.borderStrong, width: 1.5),
          minimumSize: const Size(0, AppTouch.minTarget),
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: AppSpacing.sm + AppSpacing.xs,
          ),
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(AppRadius.control)),
          ),
          textStyle: const TextStyle(
            fontFamily: fontFamily,
            fontSize: 15,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppColors.accent,
          minimumSize: const Size(0, AppTouch.minTarget),
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(AppRadius.control)),
          ),
          textStyle: const TextStyle(
            fontFamily: fontFamily,
            fontSize: 15,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: AppColors.foreground,
          minimumSize: const Size(AppTouch.minTarget, AppTouch.minTarget),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: AppColors.card,
        surfaceTintColor: Colors.transparent,
        indicatorColor: AppColors.primary,
        elevation: 0,
        height: 68,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            size: 24,
            color: selected ? AppColors.foreground : AppColors.mutedForeground,
          );
        }),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return TextStyle(
            fontFamily: fontFamily,
            fontSize: 12,
            height: 1.2,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
            letterSpacing: 0.1,
            color: selected ? AppColors.foreground : AppColors.mutedForeground,
          );
        }),
      ),
      snackBarTheme: const SnackBarThemeData(
        backgroundColor: AppColors.secondary,
        contentTextStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 14,
          height: 1.4,
          color: AppColors.subtleForeground,
        ),
        actionTextColor: AppColors.accent,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(AppRadius.control)),
        ),
      ),
      listTileTheme: const ListTileThemeData(
        iconColor: AppColors.mutedForeground,
        textColor: AppColors.foreground,
        minVerticalPadding: AppSpacing.sm + AppSpacing.xs,
      ),
      bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: AppColors.card,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        dragHandleColor: AppColors.borderStrong,
        shape: RoundedRectangleBorder(
          borderRadius:
              BorderRadius.vertical(top: Radius.circular(AppRadius.modal)),
        ),
      ),
      tooltipTheme: const TooltipThemeData(
        decoration: BoxDecoration(
          color: AppColors.secondary,
          borderRadius: BorderRadius.all(Radius.circular(AppRadius.control)),
        ),
        textStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 13,
          color: AppColors.subtleForeground,
        ),
      ),
    );
  }
}
