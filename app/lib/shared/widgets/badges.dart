import 'package:flutter/material.dart';

import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/models/proxy.dart';
import '../format.dart';

/// Resolves a latency grade to a token colour.
///
/// Every colour returned here was contrast-checked at 12% tint over
/// `--color-card`; the lowest is 4.97:1.
Color latencyColor(BuildContext context, LatencyGrade grade) {
  final tokens = context.tokens;
  return switch (grade) {
    LatencyGrade.fast => tokens.accent,
    LatencyGrade.moderate => tokens.subtleForeground,
    LatencyGrade.slow => tokens.warning,
    LatencyGrade.dead => tokens.destructiveText,
    // Never measured. Deliberately NOT green: an unprobed hop is not a fast
    // hop, and colouring it like one would be the same mistake D20 refuses
    // to make in the diversity ladder.
    LatencyGrade.unknown => tokens.mutedForeground,
  };
}

/// Flat, bordered chip. No gradients, no shadows (Swiss/minimal).
class TagChip extends StatelessWidget {
  const TagChip({
    super.key,
    required this.label,
    this.color,
    this.background,
    this.icon,
    this.dense = false,
  });

  final String label;
  final Color? color;
  final Color? background;
  final IconData? icon;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final fg = color ?? context.tokens.subtleForeground;
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: dense ? AppSpacing.sm : AppSpacing.sm + AppSpacing.xs,
        vertical: dense ? 3 : AppSpacing.xs,
      ),
      decoration: BoxDecoration(
        color: background ?? fg.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(AppRadius.control - 2),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (icon != null) ...<Widget>[
            Icon(icon, size: 13, color: fg),
            const SizedBox(width: AppSpacing.xs),
          ],
          Text(
            label,
            style: context.text.labelMedium?.copyWith(color: fg),
          ),
        ],
      ),
    );
  }
}

/// `SS` / `SOCKS5` / `HTTP`.
class ProtocolBadge extends StatelessWidget {
  const ProtocolBadge(this.protocol, {super.key});

  final ProxyProtocol protocol;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '${protocol.label} protocol',
      excludeSemantics: true,
      child: TagChip(
        label: protocol.badge,
        color: context.tokens.subtleForeground,
        background: Theme.of(context).colorScheme.secondary,
      ),
    );
  }
}

/// Two-letter country chip. Used instead of a flag emoji -- the design system
/// bans emoji as icons, and flags render inconsistently across Android OEMs.
class CountryBadge extends StatelessWidget {
  const CountryBadge(this.code, {super.key});

  final String code;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 44,
      height: 44,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.primary,
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      child: Text(
        code,
        style: context.text.titleSmall?.copyWith(
          letterSpacing: 0.5,
          color: Theme.of(context).colorScheme.onPrimary,
        ),
      ),
    );
  }
}

/// Alive / dead indicator. Shape *and* colour differ, so the signal survives
/// colour-blindness: a filled dot for alive, a hollow ring for dead.
class AliveIndicator extends StatelessWidget {
  const AliveIndicator({super.key, required this.isAlive, this.showLabel = true});

  final bool isAlive;
  final bool showLabel;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final color = isAlive ? tokens.accent : tokens.destructiveText;
    return Semantics(
      label: isAlive ? 'Online' : 'Offline',
      excludeSemantics: true,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: isAlive ? color : Colors.transparent,
              border: Border.all(color: color, width: 1.6),
            ),
          ),
          if (showLabel) ...<Widget>[
            const SizedBox(width: AppSpacing.sm - 2),
            Text(
              isAlive ? 'Online' : 'Offline',
              style: context.text.labelMedium?.copyWith(color: color),
            ),
          ],
        ],
      ),
    );
  }
}

/// Latency readout with a grade colour.
///
/// Prefers `latency_p95_ms` over `latency_ms`. A good mean with a terrible
/// tail is a bad hop and the mean hides that — which is why R1 records the
/// percentile and why this is the number the user sees.
class LatencyLabel extends StatelessWidget {
  const LatencyLabel({super.key, required this.proxy});

  final Proxy proxy;

  @override
  Widget build(BuildContext context) {
    final color = latencyColor(context, proxy.latencyGrade);
    final latency = proxy.effectiveLatencyMs;
    final String value;
    final String spoken;
    if (!proxy.isAlive) {
      value = '--';
      spoken = 'Latency unknown, host offline';
    } else if (latency == null) {
      value = 'n/a';
      spoken = 'Never probed, latency unknown';
    } else {
      value = Format.latency(latency);
      spoken = 'Latency $latency milliseconds';
    }
    return Semantics(
      label: spoken,
      excludeSemantics: true,
      child: Text(
        value,
        style: context.text.titleSmall?.copyWith(
          color: color,
          fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
        ),
      ),
    );
  }
}

/// Reliability readout: `success_rate` over `probe_count` probes.
///
/// An **unprobed** proxy reads "not probed", never "0%". The two are
/// completely different claims — one is a proxy that drops every connection,
/// the other is a proxy nobody has asked yet (D15, P15) — and conflating
/// them in the UI would undo the distinction R1 exists to draw.
class HealthBadge extends StatelessWidget {
  const HealthBadge({super.key, required this.proxy, this.dense = true});

  final Proxy proxy;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    if (proxy.isUnprobed) {
      return Semantics(
        label: 'Never probed. Reliability unknown.',
        excludeSemantics: true,
        child: TagChip(
          label: 'NOT PROBED',
          icon: Icons.help_outline,
          color: tokens.mutedForeground,
          dense: dense,
        ),
      );
    }
    final percent = proxy.successPercent ?? 0;
    final color = percent >= 95
        ? tokens.accent
        : percent >= 80
            ? tokens.subtleForeground
            : tokens.warning;
    return Semantics(
      label: '$percent percent of ${proxy.probeCount} probes succeeded',
      excludeSemantics: true,
      child: TagChip(
        label: '$percent% · ${proxy.probeCount}',
        icon: Icons.insights_outlined,
        color: color,
        dense: dense,
      ),
    );
  }
}
