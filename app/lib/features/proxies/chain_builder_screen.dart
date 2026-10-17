import 'package:flutter/material.dart';

import '../../app/app_controller.dart';
import '../../app/app_scope.dart';
import '../../core/layout/breakpoints.dart';
import '../../core/motion/motion.dart';
import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/models/proxy_chain.dart';
import '../../shared/format.dart';
import '../../shared/widgets/latency_notice.dart';
import '../../shared/widgets/state_views.dart';
import '../../shared/widgets/surface.dart';
import '../../shared/widgets/udp_notice.dart';
import 'widgets/diversity_panel.dart';
import 'widgets/hop_card.dart';

/// Compose an ordered chain of hops.
///
/// Two jobs: let the user order the route, and make the cost of that order
/// impossible to miss. architecture.md calls out that 3 hops routinely lands
/// at 400-900 ms and that users read it as a broken app -- so the running
/// total is attached to every hop and the warning is not dismissible.
class ChainBuilderScreen extends StatelessWidget {
  const ChainBuilderScreen({
    super.key,
    required this.onBrowseProxies,
    required this.onViewPlans,
    required this.onConnect,
  });

  final VoidCallback onBrowseProxies;
  final VoidCallback onViewPlans;
  final VoidCallback onConnect;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final chain = controller.chain;
    final editable = !controller.status.isConnected && !controller.status.isBusy;

    return SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final size = WindowSize.fromWidth(constraints.maxWidth);
          final gutter = Layout.gutter(size);

          final header = _ChainHeader(
            controller: controller,
            gutter: gutter,
            editable: editable,
            onViewPlans: onViewPlans,
          );

          final hopList = chain.isEmpty
              ? _EmptyChain(onBrowseProxies: onBrowseProxies)
              : _HopList(
                  controller: controller,
                  chain: chain,
                  editable: editable,
                  gutter: gutter,
                  // On compact the list sits inside the page's scroll view,
                  // so it must size to its children and not scroll itself.
                  shrinkWrap: !size.supportsTwoPane,
                );

          final sidePanel = _ChainFooter(
            chain: chain,
            controller: controller,
            editable: editable,
            onBrowseProxies: onBrowseProxies,
            onConnect: onConnect,
            gutter: gutter,
          );

          if (size.supportsTwoPane) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                header,
                Expanded(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: <Widget>[
                      Expanded(flex: 3, child: hopList),
                      const VerticalDivider(width: 1, thickness: 1),
                      Expanded(
                        flex: 2,
                        child: SingleChildScrollView(child: sidePanel),
                      ),
                    ],
                  ),
                ),
              ],
            );
          }

          // Compact: the footer is no longer a fixed-height strip. With the
          // latency notice, the dead-hop warning and the R2 panel it can be
          // taller than the window, so on a phone the hop list and the
          // footer share one scroll view instead of the footer being
          // pinned — which overflowed by 276 px on a Pixel 8 with a
          // one-hop chain the moment the diversity panel landed.
          //
          // The empty state keeps the old shape: `_EmptyChain` is a centred
          // `MessageState` and needs bounded height, and with no chain
          // there is no panel to make the footer tall.
          if (chain.isEmpty) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                header,
                Expanded(child: hopList),
                sidePanel,
              ],
            );
          }

          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              header,
              Expanded(
                child: SingleChildScrollView(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: <Widget>[hopList, sidePanel],
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _ChainHeader extends StatelessWidget {
  const _ChainHeader({
    required this.controller,
    required this.gutter,
    required this.editable,
    required this.onViewPlans,
  });

  final AppController controller;
  final double gutter;
  final bool editable;
  final VoidCallback onViewPlans;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final chain = controller.chain;
    final max = controller.maxChainLength;
    final atLimit = chain.length >= max;

    return Padding(
      padding: EdgeInsets.fromLTRB(gutter, AppSpacing.md, gutter, AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('Chain builder', style: context.text.headlineSmall),
                    const SizedBox(height: 2),
                    Text(
                      '${chain.length} of $max '
                      '${max == 1 ? 'hop' : 'hops'} used',
                      style: context.text.bodySmall?.copyWith(
                        color: atLimit ? tokens.warning : null,
                      ),
                    ),
                  ],
                ),
              ),
              if (atLimit && controller.isFreeTier)
                TextButton(
                  onPressed: onViewPlans,
                  child: const Text('Upgrade'),
                ),
            ],
          ),
          if (!editable) ...<Widget>[
            const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
            Container(
              padding: const EdgeInsets.all(AppSpacing.sm + AppSpacing.xs),
              decoration: BoxDecoration(
                color: tokens.accent.withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(AppRadius.control),
                border: Border.all(color: tokens.accent.withValues(alpha: 0.30)),
              ),
              child: Row(
                children: <Widget>[
                  Icon(Icons.lock_outline, size: 16, color: tokens.accent),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: Text(
                      'The tunnel is up. Disconnect to change the route.',
                      style: context.text.bodySmall,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _HopList extends StatelessWidget {
  const _HopList({
    required this.controller,
    required this.chain,
    required this.editable,
    required this.gutter,
    this.shrinkWrap = false,
  });

  final AppController controller;
  final ProxyChain chain;
  final bool editable;
  final double gutter;
  final bool shrinkWrap;

  @override
  Widget build(BuildContext context) {
    // Running total: what the user actually feels after each hop.
    var running = 0;
    final totals = <int>[];
    for (final hop in chain.hops) {
      running += hop.proxy.latencyMs + 35;
      totals.add(running);
    }

    return ReorderableListView.builder(
      padding: EdgeInsets.fromLTRB(
        gutter,
        AppSpacing.sm,
        gutter,
        AppSpacing.md,
      ),
      shrinkWrap: shrinkWrap,
      physics: shrinkWrap ? const NeverScrollableScrollPhysics() : null,
      itemCount: chain.hops.length,
      buildDefaultDragHandles: false,
      onReorderItem: controller.moveHop,
      proxyDecorator: (child, index, animation) => Material(
        color: Colors.transparent,
        elevation: 0,
        child: child,
      ),
      itemBuilder: (context, index) {
        final hop = chain.hops[index];
        return Padding(
          key: ValueKey<int>(hop.proxy.id),
          padding: EdgeInsets.only(
            bottom: index == chain.hops.length - 1 ? 0 : AppSpacing.sm,
          ),
          child: HopCard(
            hop: hop,
            index: index,
            runningLatencyMs: totals[index],
            editable: editable,
            onRemove: () => controller.removeHopAt(index),
          ),
        );
      },
    );
  }
}

class _EmptyChain extends StatelessWidget {
  const _EmptyChain({required this.onBrowseProxies});

  final VoidCallback onBrowseProxies;

  @override
  Widget build(BuildContext context) {
    return MessageState(
      icon: Icons.account_tree_outlined,
      title: 'No route yet',
      message: 'A chain is an ordered list of proxies. Traffic enters at hop '
          '1 and leaves the internet from the last hop. Start with one, then '
          'add depth if you need it.',
      primaryActionLabel: 'Browse proxies',
      onPrimaryAction: onBrowseProxies,
    );
  }
}

class _ChainFooter extends StatelessWidget {
  const _ChainFooter({
    required this.chain,
    required this.controller,
    required this.editable,
    required this.onBrowseProxies,
    required this.onConnect,
    required this.gutter,
  });

  final ProxyChain chain;
  final AppController controller;
  final bool editable;
  final VoidCallback onBrowseProxies;
  final VoidCallback onConnect;
  final double gutter;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final canAdd = chain.length < controller.maxChainLength;

    return Padding(
      padding: EdgeInsets.fromLTRB(
        gutter,
        AppSpacing.sm,
        gutter,
        AppSpacing.md,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (!chain.isEmpty) ...<Widget>[
            MotionSwitcher(
              child: LatencyNotice(
                key: ValueKey<int>(chain.length),
                chain: chain,
              ),
            ),
            // P7. Renders nothing when every hop carries UDP, so it
            // costs the common case nothing and cannot be forgotten on the
            // uncommon one.
            if (!chain.supportsUdp) ...<Widget>[
              const SizedBox(height: AppSpacing.md),
              UdpNotice(chain: chain),
            ],
            const SizedBox(height: AppSpacing.md),
            // R2. Fetched on an actual edit to the chain (debounced), never
            // on a rebuild of this screen — the analysis is cheap but it is
            // still a throttled call that asks questions about the
            // inventory, and a screen that refetched on focus would spend
            // the budget doing nothing.
            DiversityPanel(
              diversity: controller.diversity,
              onRetry: controller.refreshDiversity,
            ),
            const SizedBox(height: AppSpacing.md),
            if (!chain.isHealthy)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.md),
                child: Surface(
                  padding: const EdgeInsets.all(AppSpacing.md),
                  borderColor: tokens.destructiveText.withValues(alpha: 0.32),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Icon(
                        Icons.link_off,
                        size: 20,
                        color: tokens.destructiveText,
                      ),
                      const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
                      Expanded(
                        child: Text(
                          '${chain.deadHops.length} hop'
                          '${chain.deadHops.length == 1 ? '' : 's'} failed '
                          'the last health check. The chain is only as live '
                          'as its weakest hop, so this route will not come '
                          'up until you replace them.',
                          style: context.text.bodySmall,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    'Route: ${chain.routeSummary}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: context.text.bodySmall,
                  ),
                ),
                const SizedBox(width: AppTouch.minGap),
                Text(
                  Format.latency(chain.estimatedLatencyMs),
                  style: context.text.titleSmall?.copyWith(
                    color: tokens.subtleForeground,
                    fontFeatures: const <FontFeature>[
                      FontFeature.tabularFigures(),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          Row(
            children: <Widget>[
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: editable && canAdd ? onBrowseProxies : null,
                  icon: const Icon(Icons.add, size: 20),
                  label: Text(canAdd ? 'Add hop' : 'Limit reached'),
                ),
              ),
              const SizedBox(width: AppTouch.minGap),
              Expanded(
                child: FilledButton.icon(
                  onPressed: chain.isEmpty || !chain.isHealthy
                      ? null
                      : () {
                          controller.connect();
                          onConnect();
                        },
                  icon: const Icon(Icons.bolt, size: 20),
                  label: const Text('Connect'),
                ),
              ),
            ],
          ),
          if (!chain.isEmpty && editable) ...<Widget>[
            const SizedBox(height: AppTouch.minGap),
            TextButton.icon(
              onPressed: controller.clearChain,
              icon: const Icon(Icons.delete_outline, size: 18),
              label: const Text('Clear chain'),
            ),
          ],
        ],
      ),
    );
  }
}
