import 'package:flutter/material.dart';

import '../../app/app_controller.dart';
import '../../app/app_scope.dart';
import '../../core/layout/breakpoints.dart';
import '../../core/motion/motion.dart';
import '../../core/routing/proxy_engine.dart';
import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/api/api_failure.dart';
import '../../data/models/proxy_chain.dart';
import '../../shared/format.dart';
import '../../shared/widgets/badges.dart';
import '../../shared/widgets/latency_notice.dart';
import '../../shared/widgets/surface.dart';
import '../../shared/widgets/udp_notice.dart';
import 'widgets/chain_summary_card.dart';
import 'widgets/connect_button.dart';
import 'widgets/stat_tile.dart';

/// The emotional centre of the app: is my traffic protected, how fast is it,
/// and what route is it taking.
class ConnectionScreen extends StatelessWidget {
  const ConnectionScreen({
    super.key,
    required this.onEditChain,
    this.accountLabel,
    this.onSignOut,
  });

  final VoidCallback onEditChain;
  final String? accountLabel;
  final VoidCallback? onSignOut;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final status = controller.status;
    final stats = controller.stats;
    final chain = controller.chain;

    return SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final size = WindowSize.fromWidth(constraints.maxWidth);
          final gutter = Layout.gutter(size);

          final control = _ControlColumn(
            controller: controller,
            status: status,
            chain: chain,
          );

          final details = _DetailColumn(
            controller: controller,
            status: status,
            stats: stats,
            chain: chain,
            onEditChain: onEditChain,
          );

          if (size.supportsTwoPane) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                _Header(
                  controller: controller,
                  gutter: gutter,
                  accountLabel: accountLabel,
                  onSignOut: onSignOut,
                ),
                Expanded(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Expanded(
                        child: SingleChildScrollView(
                          padding: EdgeInsets.fromLTRB(
                            gutter,
                            0,
                            gutter / 2,
                            gutter,
                          ),
                          child: control,
                        ),
                      ),
                      Expanded(
                        child: SingleChildScrollView(
                          padding: EdgeInsets.fromLTRB(
                            gutter / 2,
                            0,
                            gutter,
                            gutter,
                          ),
                          child: details,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            );
          }

          return ReadableWidth(
            child: ListView(
              padding: EdgeInsets.fromLTRB(gutter, 0, gutter, gutter * 2),
              children: <Widget>[
                _Header(
                  controller: controller,
                  gutter: 0,
                  accountLabel: accountLabel,
                  onSignOut: onSignOut,
                ),
                control,
                const SizedBox(height: AppSpacing.lg),
                details,
              ],
            ),
          );
        },
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({
    required this.controller,
    required this.gutter,
    this.accountLabel,
    this.onSignOut,
  });

  final AppController controller;
  final double gutter;
  final String? accountLabel;
  final VoidCallback? onSignOut;

  @override
  Widget build(BuildContext context) {
    final plan = controller.subscription.valueOrNull?.plan;
    return Padding(
      padding: EdgeInsets.fromLTRB(gutter, AppSpacing.md, gutter, AppSpacing.lg),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text('Proxy Crack', style: context.text.headlineSmall),
                const SizedBox(height: 2),
                Text(
                  accountLabel ??
                      (controller.engine.supportsSystemWideRouting
                          ? 'System-wide routing'
                          : 'App traffic only'),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: context.text.bodySmall,
                ),
              ],
            ),
          ),
          const SizedBox(width: AppTouch.minGap),
          if (plan != null)
            TagChip(
              label: plan.name.toUpperCase(),
              icon: plan.isFreeTier
                  ? Icons.lock_open_outlined
                  : Icons.workspace_premium_outlined,
              color: plan.isFreeTier
                  ? context.tokens.mutedForeground
                  : context.tokens.accent,
            ),
          if (onSignOut != null) ...<Widget>[
            const SizedBox(width: AppSpacing.xs),
            IconButton(
              onPressed: controller.status.isConnected ? null : onSignOut,
              icon: const Icon(Icons.logout, size: 20),
              tooltip: controller.status.isConnected
                  ? 'Disconnect before signing out'
                  : 'Sign out',
              constraints: const BoxConstraints(
                minWidth: AppTouch.minTarget,
                minHeight: AppTouch.minTarget,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// What the app is doing between the Connect tap and the engine starting.
///
/// A real connect is four server interactions, not one: save the chain,
/// resolve it, fetch one set of credentials per hop, then start the engine.
/// Naming the step matters because they fail differently — "hop 2 was rate
/// limited" is actionable and "connection failed" is not.
class _ConnectProgress extends StatelessWidget {
  const _ConnectProgress({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final stage = controller.connectStage;
    final failure = controller.connectFailure;

    if (failure != null) {
      return _ConnectFailureCard(failure: failure);
    }
    if (stage == ConnectStage.idle) return const SizedBox.shrink();

    final hops = controller.chain.length;
    final label = switch (stage) {
      ConnectStage.savingChain => 'Saving the chain',
      ConnectStage.resolving => 'Resolving hops to live proxies',
      ConnectStage.fetchingCredentials =>
        'Fetching credentials '
            '(${controller.credentialsFetched} of $hops)',
      ConnectStage.startingEngine => 'Starting the tunnel',
      ConnectStage.idle => '',
    };

    return Semantics(
      liveRegion: true,
      child: Surface(
        padding: const EdgeInsets.all(AppSpacing.sm + AppSpacing.xs),
        child: Row(
          children: <Widget>[
            SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: tokens.accent,
              ),
            ),
            const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
            Expanded(child: Text(label, style: context.text.bodySmall)),
          ],
        ),
      ),
    );
  }
}

/// A connect that failed before the engine was reached.
///
/// Branches on `error.code`, not on message text: the three the chain
/// builder can actually produce — `chain_length_exceeded`,
/// `proxy_not_entitled` and `chain_unresolvable` — each need a different
/// next step from the user, and a 429 needs a wait rather than a retry.
class _ConnectFailureCard extends StatelessWidget {
  const _ConnectFailureCard({required this.failure});

  final ApiFailure failure;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final f = failure;

    String title;
    String body;
    IconData icon;

    if (f is ApiException) {
      if (f.isThrottled) {
        icon = Icons.hourglass_empty_outlined;
        title = 'Rate limit reached';
        body = f.message;
      } else if (f.hasCode(ApiErrorCode.chainLengthExceeded)) {
        icon = Icons.workspace_premium_outlined;
        final max = f.detailValue('max_chain_length');
        title = 'Chain is longer than your plan allows';
        body = max == null
            ? f.message
            : 'Your plan allows $max '
                  '${max == 1 ? 'hop' : 'hops'}. Remove a hop or upgrade.';
      } else if (f.hasCode(ApiErrorCode.proxyNotEntitled)) {
        icon = Icons.lock_outline;
        title = 'A hop is not included in your plan';
        body = f.message;
      } else if (f.hasCode(ApiErrorCode.chainUnresolvable)) {
        icon = Icons.link_off;
        final position = f.failingPosition;
        final where = position == null ? 'A hop' : 'Hop ${position + 1}';
        title = 'This chain cannot be resolved right now';
        body = switch (f.resolutionCode) {
          ChainResolutionCode.hopProxyDead =>
            '$where failed its last health checks. Replace it in the chain '
                'builder.',
          ChainResolutionCode.hopProxyDisabled =>
            '$where has been withdrawn by the operator. Replace it.',
          ChainResolutionCode.hopUnsatisfiedUnprobed =>
            '$where asks for a latency or reliability guarantee, and no '
                'proxy has been measured well enough to make it yet.',
          ChainResolutionCode.hopUnsatisfied =>
            'Nothing in the inventory currently satisfies $where.',
          ChainResolutionCode.hopDuplicateProxy =>
            'The same proxy appears twice in this chain.',
          ChainResolutionCode.chainEmpty => 'This chain has no hops.',
          _ => f.message,
        };
      } else {
        icon = Icons.error_outline;
        title = 'Could not start the connection';
        body = f.message;
      }
    } else if (f.isOffline) {
      icon = Icons.wifi_off_outlined;
      title = 'No connection to the server';
      body = '${f.message} ${f.detailLine ?? ''}'.trim();
    } else {
      icon = Icons.error_outline;
      title = 'Could not start the connection';
      body = f.message;
    }

    return Semantics(
      liveRegion: true,
      child: Surface(
        padding: const EdgeInsets.all(AppSpacing.md),
        borderColor: tokens.destructiveText.withValues(alpha: 0.32),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(icon, size: 20, color: tokens.destructiveText),
            const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(title, style: context.text.titleSmall),
                  const SizedBox(height: 2),
                  Text(body, style: context.text.bodySmall),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Operator copy from `GET /api/content/announcements/`.
class _Announcements extends StatelessWidget {
  const _Announcements({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final items = controller.visibleAnnouncements;
    if (items.isEmpty) return const SizedBox.shrink();
    final tokens = context.tokens;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        const SectionLabel('From the operator'),
        const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
        for (final item in items)
          Padding(
            // ValueKey on a dynamic list item: the row id plus its update
            // stamp, so edited copy rebuilds instead of being reused.
            key: ValueKey<String>('announcement-${item.listKey}'),
            padding: const EdgeInsets.only(bottom: AppSpacing.sm),
            child: Surface(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Icon(
                    Icons.campaign_outlined,
                    size: 20,
                    color: tokens.subtleForeground,
                  ),
                  const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(item.title, style: context.text.titleSmall),
                        const SizedBox(height: 2),
                        Text(item.body, style: context.text.bodySmall),
                      ],
                    ),
                  ),
                  const SizedBox(width: AppSpacing.xs),
                  IconButton(
                    onPressed: () => controller.dismissAnnouncement(item),
                    icon: const Icon(Icons.close, size: 18),
                    tooltip: 'Dismiss',
                    constraints: const BoxConstraints(
                      minWidth: AppTouch.minTarget,
                      minHeight: AppTouch.minTarget,
                    ),
                  ),
                ],
              ),
            ),
          ),
        const SizedBox(height: AppSpacing.md),
      ],
    );
  }
}

/// Status line + the big toggle.
class _ControlColumn extends StatelessWidget {
  const _ControlColumn({
    required this.controller,
    required this.status,
    required this.chain,
  });

  final AppController controller;
  final EngineStatus status;
  final ProxyChain chain;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _StatusBanner(status: status),
        if (controller.connectStage != ConnectStage.idle ||
            controller.connectFailure != null) ...<Widget>[
          const SizedBox(height: AppSpacing.md),
          _ConnectProgress(controller: controller),
        ],
        const SizedBox(height: AppSpacing.lg),
        ConnectButton(
          status: status,
          onPressed: chain.isEmpty && !status.hasError
              ? null
              : controller.toggleConnection,
        ),
        const SizedBox(height: AppSpacing.md),
        if (chain.isEmpty)
          Text(
            'Pick at least one proxy in the chain builder before connecting.',
            textAlign: TextAlign.center,
            style: context.text.bodySmall,
          ),
      ],
    );
  }
}

/// Stats grid, route summary and latency expectation.
class _DetailColumn extends StatelessWidget {
  const _DetailColumn({
    required this.controller,
    required this.status,
    required this.stats,
    required this.chain,
    required this.onEditChain,
  });

  final AppController controller;
  final EngineStatus status;
  final ThroughputStats stats;
  final ProxyChain chain;
  final VoidCallback onEditChain;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final live = status.isConnected;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _Announcements(controller: controller),
        const SectionLabel('Throughput'),
        const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
        _StatsGrid(
          tiles: <StatTile>[
            StatTile(
              label: 'Download',
              icon: Icons.south_rounded,
              value: Format.rate(stats.downBytesPerSecond),
              valueColor: live ? tokens.accent : null,
              isPlaceholder: !live,
            ),
            StatTile(
              label: 'Upload',
              icon: Icons.north_rounded,
              value: Format.rate(stats.upBytesPerSecond),
              isPlaceholder: !live,
            ),
            StatTile(
              label: 'Chain latency',
              icon: Icons.timer_outlined,
              value: Format.latency(
                stats.measuredLatencyMs ?? chain.estimatedLatencyMs,
              ),
              valueColor:
                  chain.latencyExpectation == LatencyExpectation.severe
                      ? tokens.warning
                      : null,
              isPlaceholder: !live && chain.isEmpty,
            ),
            StatTile(
              label: 'Session',
              icon: Icons.schedule_outlined,
              value: Format.duration(stats.sessionDuration),
              isPlaceholder: !live,
            ),
            StatTile(
              label: 'Downloaded',
              icon: Icons.download_outlined,
              value: Format.bytes(stats.bytesDown),
              isPlaceholder: !live,
            ),
            StatTile(
              label: 'Uploaded',
              icon: Icons.upload_outlined,
              value: Format.bytes(stats.bytesUp),
              isPlaceholder: !live,
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.lg),
        ChainSummaryCard(
          chain: chain,
          status: status,
          onEdit: onEditChain,
        ),
        if (!chain.isEmpty) ...<Widget>[
          const SizedBox(height: AppSpacing.md),
          LatencyNotice(chain: chain, compact: live),
        ],
        // P7. Renders nothing when every hop carries UDP, so it costs the
        // common case nothing and is impossible to forget on the uncommon
        // one.
        if (!chain.supportsUdp) ...<Widget>[
          const SizedBox(height: AppSpacing.md),
          UdpNotice(chain: chain),
        ],
      ],
    );
  }
}

/// Responsive tile grid. Column count comes from the window width, so the
/// same grid works on a 375 px phone and a 1440 px desktop window.
class _StatsGrid extends StatelessWidget {
  const _StatsGrid({required this.tiles});

  final List<StatTile> tiles;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = constraints.maxWidth >= 560 ? 3 : 2;
        const gap = AppSpacing.sm + AppSpacing.xs;
        final itemWidth =
            (constraints.maxWidth - gap * (columns - 1)) / columns;

        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: <Widget>[
            for (final tile in tiles)
              SizedBox(
                width: itemWidth,
                child: KeyedSubtree(
                  key: ValueKey<String>('stat-${tile.label}'),
                  child: tile,
                ),
              ),
          ],
        );
      },
    );
  }
}

/// One line that says exactly what the tunnel is doing, plus determinate
/// progress while connecting -- not a bare spinner.
class _StatusBanner extends StatelessWidget {
  const _StatusBanner({required this.status});

  final EngineStatus status;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;

    final (Color color, IconData icon, String title) = switch (status.state) {
      EngineState.disconnected => (
          tokens.mutedForeground,
          Icons.lock_open_outlined,
          'Not protected',
        ),
      EngineState.connecting => (
          tokens.warning,
          Icons.sync,
          'Building the chain',
        ),
      EngineState.connected => (
          tokens.accent,
          Icons.verified_user_outlined,
          'Protected',
        ),
      EngineState.disconnecting => (
          tokens.mutedForeground,
          Icons.sync,
          'Disconnecting',
        ),
      EngineState.error => (
          tokens.destructiveText,
          Icons.error_outline,
          'Connection failed',
        ),
    };

    final detail = status.detail ??
        switch (status.state) {
          EngineState.disconnected =>
            'Traffic is leaving this device unproxied.',
          EngineState.connected => 'All device traffic is going through '
              'the chain.',
          _ => '',
        };

    return MotionSwitcher(
      child: Container(
        key: ValueKey<String>('${status.state}-${status.detail}'),
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(AppRadius.card),
          border: Border.all(color: color.withValues(alpha: 0.30)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(icon, size: 18, color: color),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Text(
                    title,
                    style: context.text.titleSmall?.copyWith(color: color),
                  ),
                ),
                Semantics(
                  liveRegion: true,
                  child: Text(
                    status.state.name.toUpperCase(),
                    style: context.text.labelSmall?.copyWith(color: color),
                  ),
                ),
              ],
            ),
            if (detail.isNotEmpty) ...<Widget>[
              const SizedBox(height: AppSpacing.sm - 2),
              Text(detail, style: context.text.bodySmall),
            ],
            if (status.state == EngineState.connecting) ...<Widget>[
              const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
              ClipRRect(
                borderRadius: BorderRadius.circular(AppRadius.pill),
                child: LinearProgressIndicator(
                  value: status.progress,
                  minHeight: 4,
                  backgroundColor: tokens.borderStrong,
                  color: color,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
