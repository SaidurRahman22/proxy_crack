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
class LatencyLabel extends StatelessWidget {
  const LatencyLabel({super.key, required this.proxy});

  final Proxy proxy;

  @override
  Widget build(BuildContext context) {
    final color = latencyColor(context, proxy.latencyGrade);
    final value = proxy.isAlive ? Format.latency(proxy.latencyMs) : '--';
    return Semantics(
      label: proxy.isAlive
          ? 'Latency ${proxy.latencyMs} milliseconds'
          : 'Latency unknown, host offline',
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
