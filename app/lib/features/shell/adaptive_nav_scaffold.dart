import 'package:flutter/material.dart';

import '../../core/layout/breakpoints.dart';
import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';

/// One navigation destination, independent of how it gets drawn.
class NavItem {
  const NavItem({
    required this.id,
    required this.label,
    required this.icon,
    required this.selectedIcon,
  });

  final String id;
  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

/// The single place that decides *where* navigation chrome lives.
///
/// Compact windows get a [NavigationBar] at the bottom; medium and expanded
/// windows get a [NavigationRail] down the leading edge. No screen knows
/// which it got, so adding desktop later is a change to this file alone.
class AdaptiveNavScaffold extends StatelessWidget {
  const AdaptiveNavScaffold({
    super.key,
    required this.items,
    required this.selectedIndex,
    required this.onSelected,
    required this.body,
    this.floatingAction,
    this.railLeading,
  });

  final List<NavItem> items;
  final int selectedIndex;
  final ValueChanged<int> onSelected;
  final Widget body;
  final Widget? floatingAction;

  /// Optional branding block shown above the rail on wide windows.
  final Widget? railLeading;

  @override
  Widget build(BuildContext context) {
    assert(
      items.length <= 5,
      'Bottom navigation tops out at 5 destinations.',
    );

    return LayoutBuilder(
      builder: (context, constraints) {
        final size = WindowSize.fromWidth(constraints.maxWidth);

        if (size.usesBottomNav) {
          return Scaffold(
            body: body,
            bottomNavigationBar: SafeArea(
              top: false,
              child: NavigationBar(
                selectedIndex: selectedIndex,
                onDestinationSelected: onSelected,
                destinations: <Widget>[
                  for (final item in items)
                    NavigationDestination(
                      key: ValueKey<String>('nav-${item.id}'),
                      icon: Icon(item.icon),
                      selectedIcon: Icon(item.selectedIcon),
                      label: item.label,
                      tooltip: item.label,
                    ),
                ],
              ),
            ),
            floatingActionButton: floatingAction,
            floatingActionButtonLocation:
                FloatingActionButtonLocation.centerFloat,
          );
        }

        // Medium and expanded: rail on the leading edge.
        return Scaffold(
          body: SafeArea(
            child: Row(
              children: <Widget>[
                _Rail(
                  items: items,
                  selectedIndex: selectedIndex,
                  onSelected: onSelected,
                  extended: size == WindowSize.expanded,
                  leading: railLeading,
                ),
                const VerticalDivider(width: 1, thickness: 1),
                Expanded(child: body),
              ],
            ),
          ),
          floatingActionButton: floatingAction,
          floatingActionButtonLocation: FloatingActionButtonLocation.endFloat,
        );
      },
    );
  }
}

class _Rail extends StatelessWidget {
  const _Rail({
    required this.items,
    required this.selectedIndex,
    required this.onSelected,
    required this.extended,
    this.leading,
  });

  final List<NavItem> items;
  final int selectedIndex;
  final ValueChanged<int> onSelected;
  final bool extended;
  final Widget? leading;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return NavigationRail(
      selectedIndex: selectedIndex,
      onDestinationSelected: onSelected,
      extended: extended,
      labelType:
          extended ? NavigationRailLabelType.none : NavigationRailLabelType.all,
      backgroundColor: Theme.of(context).colorScheme.surfaceContainer,
      indicatorColor: Theme.of(context).colorScheme.primary,
      minWidth: 72,
      groupAlignment: -0.9,
      leading: leading == null
          ? null
          : Padding(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.md),
              child: leading,
            ),
      selectedIconTheme: IconThemeData(color: tokens.accent, size: 24),
      unselectedIconTheme:
          IconThemeData(color: tokens.mutedForeground, size: 24),
      selectedLabelTextStyle: context.text.labelLarge,
      unselectedLabelTextStyle: context.text.labelMedium,
      destinations: <NavigationRailDestination>[
        for (final item in items)
          NavigationRailDestination(
            icon: Icon(item.icon),
            selectedIcon: Icon(item.selectedIcon),
            label: Text(item.label),
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
          ),
      ],
    );
  }
}
