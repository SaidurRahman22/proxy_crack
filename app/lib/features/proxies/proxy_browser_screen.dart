import 'package:flutter/material.dart';

import '../../app/app_controller.dart';
import '../../app/app_scope.dart';
import '../../core/layout/breakpoints.dart';
import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/models/proxy.dart';
import '../../shared/widgets/state_views.dart';
import 'widgets/proxy_detail_pane.dart';
import 'widgets/proxy_list_tile.dart';

/// Browse the entitlement-scoped proxy pool.
///
/// The list is rendered with `ListView.builder` and a `ValueKey` per row, so
/// a few hundred servers cost the same as a screenful.
class ProxyBrowserScreen extends StatefulWidget {
  const ProxyBrowserScreen({super.key, required this.onOpenChain});

  final VoidCallback onOpenChain;

  @override
  State<ProxyBrowserScreen> createState() => _ProxyBrowserScreenState();
}

class _ProxyBrowserScreenState extends State<ProxyBrowserScreen> {
  final TextEditingController _search = TextEditingController();
  final ScrollController _scroll = ScrollController();
  int? _selectedId;

  @override
  void dispose() {
    _search.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _toggleInChain(AppController controller, Proxy proxy) {
    if (controller.isInChain(proxy)) {
      final index =
          controller.chain.hops.indexWhere((h) => h.proxy.id == proxy.id);
      controller.removeHopAt(index);
      _notify('Removed ${proxy.countryCode} from the chain.');
      return;
    }

    final rejection = controller.addHop(proxy);
    if (rejection == null) {
      _notify(
        'Added ${proxy.countryCode} as hop ${controller.chain.length}.',
        actionLabel: 'View chain',
        onAction: widget.onOpenChain,
      );
      return;
    }

    _notify(switch (rejection) {
      HopRejection.chainLengthLimit =>
        'Your plan allows ${controller.maxChainLength} '
            '${controller.maxChainLength == 1 ? 'hop' : 'hops'}. '
            'Remove one, or upgrade for longer chains.',
      HopRejection.premiumRequired =>
        'That is a premium server. Upgrade to add it to a chain.',
      HopRejection.duplicate => 'That server is already in your chain.',
      HopRejection.engineBusy =>
        'Disconnect before editing the chain.',
    });
  }

  void _notify(String message, {String? actionLabel, VoidCallback? onAction}) {
    final messenger = ScaffoldMessenger.maybeOf(context);
    if (messenger == null) return;
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(message),
          duration: const Duration(seconds: 4),
          action: actionLabel != null && onAction != null
              ? SnackBarAction(label: actionLabel, onPressed: onAction)
              : null,
        ),
      );
  }

  void _openDetail(AppController controller, Proxy proxy) {
    setState(() => _selectedId = proxy.id);
    if (WindowSize.of(context).supportsTwoPane) return;

    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxHeight: MediaQuery.sizeOf(sheetContext).height * 0.8,
          ),
          child: ProxyDetailPane(
            proxy: proxy,
            isInChain: controller.isInChain(proxy),
            locked: proxy.isPremium && controller.isFreeTier,
            onToggleInChain: () {
              Navigator.of(sheetContext).pop();
              _toggleInChain(controller, proxy);
            },
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);

    return SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final size = WindowSize.fromWidth(constraints.maxWidth);
          final gutter = Layout.gutter(size);

          final listPane = Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              _BrowserHeader(
                controller: controller,
                searchController: _search,
                gutter: gutter,
              ),
              Expanded(
                child: _ProxyList(
                  controller: controller,
                  gutter: gutter,
                  scrollController: _scroll,
                  selectedId: _selectedId,
                  onSelect: (p) => _openDetail(controller, p),
                  onToggle: (p) => _toggleInChain(controller, p),
                ),
              ),
            ],
          );

          if (!size.supportsTwoPane) {
            return listPane;
          }

          final selected = controller.proxies.valueOrNull
              ?.where((p) => p.id == _selectedId)
              .firstOrNull;

          return Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Expanded(flex: 3, child: listPane),
              const VerticalDivider(width: 1, thickness: 1),
              Expanded(
                flex: 2,
                child: ProxyDetailPane(
                  proxy: selected,
                  isInChain:
                      selected != null && controller.isInChain(selected),
                  locked: (selected?.isPremium ?? false) &&
                      controller.isFreeTier,
                  onToggleInChain: () {
                    if (selected != null) _toggleInChain(controller, selected);
                  },
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _BrowserHeader extends StatelessWidget {
  const _BrowserHeader({
    required this.controller,
    required this.searchController,
    required this.gutter,
  });

  final AppController controller;
  final TextEditingController searchController;
  final double gutter;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final total = controller.proxies.valueOrNull?.length ?? 0;
    final shown = controller.visibleProxies.length;

    return Padding(
      padding: EdgeInsets.fromLTRB(gutter, AppSpacing.md, gutter, AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text('Proxies', style: context.text.headlineSmall),
              ),
              if (total > 0)
                Text(
                  '$shown of $total',
                  style: context.text.bodySmall,
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          TextField(
            controller: searchController,
            onChanged: controller.setQuery,
            textInputAction: TextInputAction.search,
            style: context.text.bodyLarge,
            decoration: InputDecoration(
              hintText: 'Country, city or host',
              hintStyle: context.text.bodyMedium,
              prefixIcon: Icon(
                Icons.search,
                size: 20,
                color: tokens.mutedForeground,
              ),
              suffixIcon: controller.query.isEmpty
                  ? null
                  : IconButton(
                      tooltip: 'Clear search',
                      icon: const Icon(Icons.close, size: 20),
                      constraints: const BoxConstraints(
                        minWidth: AppTouch.minTarget,
                        minHeight: AppTouch.minTarget,
                      ),
                      onPressed: () {
                        searchController.clear();
                        controller.setQuery('');
                      },
                    ),
              filled: true,
              fillColor: Theme.of(context).colorScheme.surfaceContainer,
              contentPadding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.md,
                vertical: AppSpacing.sm + AppSpacing.xs,
              ),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(AppRadius.control),
                borderSide: BorderSide(color: tokens.border),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(AppRadius.control),
                borderSide: BorderSide(color: tokens.border),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(AppRadius.control),
                borderSide: BorderSide(color: tokens.accent, width: 2),
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: <Widget>[
                _FilterChip(
                  label: 'All',
                  selected: controller.protocolFilter == null,
                  onSelected: () => controller.setProtocolFilter(null),
                ),
                for (final protocol in ProxyProtocol.values) ...<Widget>[
                  const SizedBox(width: AppTouch.minGap),
                  _FilterChip(
                    key: ValueKey<String>('filter-${protocol.wire}'),
                    label: protocol.badge,
                    selected: controller.protocolFilter == protocol,
                    onSelected: () => controller.setProtocolFilter(protocol),
                  ),
                ],
                const SizedBox(width: AppTouch.minGap),
                _FilterChip(
                  label: 'Online only',
                  icon: Icons.bolt_outlined,
                  selected: controller.aliveOnly,
                  onSelected: () =>
                      controller.setAliveOnly(!controller.aliveOnly),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Filter pill with a 48 px tall target and 8 px gaps between neighbours.
class _FilterChip extends StatelessWidget {
  const _FilterChip({
    super.key,
    required this.label,
    required this.selected,
    required this.onSelected,
    this.icon,
  });

  final String label;
  final bool selected;
  final VoidCallback onSelected;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final scheme = Theme.of(context).colorScheme;
    final fg = selected ? tokens.onAccent : tokens.subtleForeground;

    return Semantics(
      selected: selected,
      button: true,
      child: Material(
        color: selected ? tokens.accent : scheme.surfaceContainer,
        borderRadius: BorderRadius.circular(AppRadius.control),
        child: InkWell(
          onTap: onSelected,
          borderRadius: BorderRadius.circular(AppRadius.control),
          child: Container(
            constraints: const BoxConstraints(
              minHeight: AppTouch.minTarget,
              minWidth: AppTouch.minTarget,
            ),
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
            alignment: Alignment.center,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(AppRadius.control),
              border: Border.all(
                color: selected ? Colors.transparent : tokens.border,
              ),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                if (icon != null) ...<Widget>[
                  Icon(icon, size: 16, color: fg),
                  const SizedBox(width: AppSpacing.xs + 2),
                ],
                Text(
                  label,
                  style: context.text.labelLarge?.copyWith(
                    color: fg,
                    fontSize: 14,
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

class _ProxyList extends StatelessWidget {
  const _ProxyList({
    required this.controller,
    required this.gutter,
    required this.scrollController,
    required this.selectedId,
    required this.onSelect,
    required this.onToggle,
  });

  final AppController controller;
  final double gutter;
  final ScrollController scrollController;
  final int? selectedId;
  final ValueChanged<Proxy> onSelect;
  final ValueChanged<Proxy> onToggle;

  @override
  Widget build(BuildContext context) {
    return controller.proxies.map(
      loading: () => const ListSkeleton(label: 'Loading proxy list'),
      error: (message, detail) => ErrorStateView(
        message: message,
        detail: 'The server list could not be fetched. '
            'Your connection may be blocked.',
        onRetry: controller.refreshProxies,
      ),
      data: (all) {
        if (all.isEmpty) {
          return MessageState(
            icon: Icons.inbox_outlined,
            title: 'No servers yet',
            message: 'Your plan has no proxies assigned. This usually means '
                'the pool is still being provisioned.',
            primaryActionLabel: 'Refresh',
            onPrimaryAction: controller.refreshProxies,
          );
        }

        final visible = controller.visibleProxies;
        if (visible.isEmpty) {
          return MessageState(
            icon: Icons.filter_alt_off_outlined,
            title: 'Nothing matches those filters',
            message: 'No server in the pool matches '
                '"${controller.query}" with the filters you have set.',
            primaryActionLabel: 'Clear filters',
            onPrimaryAction: controller.clearFilters,
          );
        }

        return RefreshIndicator(
          onRefresh: controller.refreshProxies,
          color: context.tokens.accent,
          backgroundColor: Theme.of(context).colorScheme.surfaceContainer,
          child: ListView.separated(
            controller: scrollController,
            padding: EdgeInsets.fromLTRB(
              gutter,
              AppSpacing.sm,
              gutter,
              AppSpacing.xxl,
            ),
            itemCount: visible.length,
            // Keeps a few hundred rows cheap: only the visible window is
            // built, and rows are not kept alive off-screen.
            addAutomaticKeepAlives: false,
            separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
            itemBuilder: (context, index) {
              final proxy = visible[index];
              return ProxyListTile(
                key: ValueKey<int>(proxy.id),
                proxy: proxy,
                isInChain: controller.isInChain(proxy),
                isSelected: proxy.id == selectedId,
                locked: proxy.isPremium && controller.isFreeTier,
                onTap: () => onSelect(proxy),
                onToggleInChain: () => onToggle(proxy),
              );
            },
          ),
        );
      },
    );
  }
}
