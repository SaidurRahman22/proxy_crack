import 'package:flutter/material.dart';

import '../../app/app_scope.dart';
import '../../core/routing/proxy_engine.dart';
import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../connection/connection_screen.dart';
import '../proxies/chain_builder_screen.dart';
import '../proxies/proxy_browser_screen.dart';
import '../subscription/paywall_screen.dart';
import 'adaptive_nav_scaffold.dart';

/// Tabs. Four of a maximum five, so the bar never scrolls or crowds, and the
/// same four map cleanly onto a navigation rail on wide windows.
enum ShellTab {
  connection('Connect', Icons.shield_outlined, Icons.shield),
  proxies('Proxies', Icons.dns_outlined, Icons.dns),
  chain('Chain', Icons.account_tree_outlined, Icons.account_tree),
  plans('Plans', Icons.workspace_premium_outlined, Icons.workspace_premium);

  const ShellTab(this.label, this.icon, this.selectedIcon);

  final String label;
  final IconData icon;
  final IconData selectedIcon;

  NavItem get navItem => NavItem(
        id: name,
        label: label,
        icon: icon,
        selectedIcon: selectedIcon,
      );
}

/// Root scaffold. Navigation chrome is chosen by window width inside
/// [AdaptiveNavScaffold]; this widget only owns *which tab is selected*.
///
/// Back behaviour, which users must be able to predict:
/// * on any tab other than Connect, Back returns to Connect;
/// * on Connect, Back leaves the app;
/// * tab state (scroll offset, filters) survives switching, because the
///   screens stay mounted inside an [IndexedStack].
class AppShell extends StatefulWidget {
  const AppShell({super.key});

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  ShellTab _tab = ShellTab.connection;

  void _select(ShellTab tab) {
    if (_tab == tab) return;
    setState(() => _tab = tab);
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final isConnected = controller.status.state == EngineState.connected;
    final atRoot = _tab == ShellTab.connection;

    return PopScope<Object?>(
      canPop: atRoot,
      onPopInvokedWithResult: (didPop, _) {
        if (didPop) return;
        _select(ShellTab.connection);
      },
      child: AdaptiveNavScaffold(
        items: <NavItem>[for (final tab in ShellTab.values) tab.navItem],
        selectedIndex: _tab.index,
        onSelected: (i) => _select(ShellTab.values[i]),
        railLeading: const _RailBrand(),
        floatingAction: isConnected && _tab != ShellTab.connection
            ? _ConnectedPill(onTap: () => _select(ShellTab.connection))
            : null,
        body: IndexedStack(
          index: _tab.index,
          children: <Widget>[
            ConnectionScreen(onEditChain: () => _select(ShellTab.chain)),
            ProxyBrowserScreen(onOpenChain: () => _select(ShellTab.chain)),
            ChainBuilderScreen(
              onBrowseProxies: () => _select(ShellTab.proxies),
              onViewPlans: () => _select(ShellTab.plans),
              onConnect: () => _select(ShellTab.connection),
            ),
            const PaywallScreen(),
          ],
        ),
      ),
    );
  }
}

/// Compact mark shown above the navigation rail on wide windows. Never
/// rendered on a phone.
class _RailBrand extends StatelessWidget {
  const _RailBrand();

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: 'Proxy Crack',
      child: Container(
        width: 40,
        height: 40,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.primary,
          borderRadius: BorderRadius.circular(AppRadius.control),
        ),
        child: Icon(
          Icons.hub_outlined,
          size: 22,
          color: Theme.of(context).colorScheme.onPrimary,
        ),
      ),
    );
  }
}

/// Persistent "you are connected" affordance shown on non-Connect tabs.
class _ConnectedPill extends StatelessWidget {
  const _ConnectedPill({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Semantics(
      button: true,
      label: 'Tunnel is connected. Open the connection screen.',
      child: Material(
        color: tokens.accent,
        borderRadius: BorderRadius.circular(AppRadius.pill),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(AppRadius.pill),
          // Padding, not a Container with `alignment`: inside the Scaffold's
          // floating-action slot the constraints are bounded, and an aligned
          // Container would expand to fill the whole window.
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.lg,
              vertical: 14,
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Icon(Icons.lock_outline, size: 18, color: tokens.onAccent),
                const SizedBox(width: AppSpacing.sm),
                Text(
                  'Connected',
                  style: context.text.labelLarge?.copyWith(
                    color: tokens.onAccent,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
