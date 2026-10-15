import 'package:flutter/material.dart';

import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';
import '../../../data/models/proxy.dart';
import '../../../shared/format.dart';
import '../../../shared/widgets/badges.dart';

/// One row of the proxy browser.
///
/// Kept cheap on purpose: no implicit animations, no shadows, const subtrees
/// where possible. The browser renders a few hundred of these through
/// `ListView.builder`, so per-row cost is the whole performance story.
class ProxyListTile extends StatelessWidget {
  const ProxyListTile({
    super.key,
    required this.proxy,
    required this.isInChain,
    required this.isSelected,
    required this.onTap,
    required this.onToggleInChain,
    this.locked = false,
  });

  final Proxy proxy;
  final bool isInChain;
  final bool isSelected;
  final VoidCallback onTap;
  final VoidCallback onToggleInChain;

  /// Premium proxy on a free plan: still listed, but the add control explains
  /// itself instead of silently failing.
  final bool locked;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final scheme = Theme.of(context).colorScheme;

    return Semantics(
      selected: isSelected,
      button: true,
      label: '${proxy.city}, ${proxy.country}. '
          '${proxy.protocol.label}. '
          '${proxy.isAlive ? '${proxy.latencyMs} milliseconds' : 'offline'}. '
          '${proxy.isPremium ? 'Premium. ' : ''}'
          '${isInChain ? 'In your chain.' : ''}',
      excludeSemantics: true,
      child: Material(
        color: isSelected ? scheme.secondary : scheme.surfaceContainer,
        borderRadius: BorderRadius.circular(AppRadius.card),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Container(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(AppRadius.card),
              border: Border.all(
                color: isInChain ? tokens.accent.withValues(alpha: 0.45)
                    : tokens.border,
              ),
            ),
            padding: const EdgeInsets.all(AppSpacing.sm + AppSpacing.xs),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                CountryBadge(proxy.countryCode),
                const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: <Widget>[
                      Text(
                        proxy.location,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: context.text.titleSmall,
                      ),
                      const SizedBox(height: 2),
                      Text(
                        proxy.endpoint,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: context.text.bodySmall,
                      ),
                      const SizedBox(height: AppSpacing.sm - 2),
                      Wrap(
                        spacing: AppSpacing.sm - 2,
                        runSpacing: AppSpacing.xs,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: <Widget>[
                          ProtocolBadge(proxy.protocol),
                          AliveIndicator(isAlive: proxy.isAlive),
                          if (proxy.isPremium)
                            TagChip(
                              label: 'PREMIUM',
                              icon: Icons.workspace_premium_outlined,
                              color: tokens.warning,
                              dense: true,
                            ),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    LatencyLabel(proxy: proxy),
                    const SizedBox(height: 2),
                    Text(
                      Format.relativeTime(proxy.lastChecked),
                      style: context.text.bodySmall?.copyWith(fontSize: 11),
                    ),
                  ],
                ),
                const SizedBox(width: AppSpacing.xs),
                _AddControl(
                  isInChain: isInChain,
                  locked: locked,
                  onPressed: onToggleInChain,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _AddControl extends StatelessWidget {
  const _AddControl({
    required this.isInChain,
    required this.locked,
    required this.onPressed,
  });

  final bool isInChain;
  final bool locked;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final (IconData icon, Color color, String tip) = locked
        ? (Icons.lock_outline, tokens.warning, 'Premium proxy — upgrade to use')
        : isInChain
            ? (Icons.remove_circle_outline, tokens.destructiveText,
                'Remove from chain')
            : (Icons.add_circle_outline, tokens.accent, 'Add to chain');

    return Tooltip(
      message: tip,
      child: IconButton(
        onPressed: onPressed,
        iconSize: 22,
        // 48x48 minimum target with 8 px of breathing room from the row edge.
        constraints: const BoxConstraints(
          minWidth: AppTouch.minTarget,
          minHeight: AppTouch.minTarget,
        ),
        icon: Icon(icon, color: color),
        tooltip: tip,
      ),
    );
  }
}
