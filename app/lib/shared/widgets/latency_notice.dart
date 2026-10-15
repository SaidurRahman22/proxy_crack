import 'package:flutter/material.dart';

import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/models/proxy_chain.dart';
import '../format.dart';

/// Sets the expectation that chaining is slow *before* the user decides the
/// app is broken.
///
/// architecture.md section 1: "Latency compounds. Each hop adds a full RTT. A
/// 3-hop chain is routinely 400-900 ms. Users read that as 'the app is slow'
/// unless the UI sets expectations." This widget is that expectation-setting,
/// and it is deliberately not dismissible on 3+ hops.
class LatencyNotice extends StatelessWidget {
  const LatencyNotice({
    super.key,
    required this.chain,
    this.compact = false,
  });

  final ProxyChain chain;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final expectation = chain.latencyExpectation;

    final (IconData icon, Color color, String headline, String body) = switch (
        expectation) {
      LatencyExpectation.none => (
          Icons.info_outline,
          tokens.mutedForeground,
          'No hops yet',
          'Add at least one proxy to build a route.',
        ),
      LatencyExpectation.direct => (
          Icons.speed_outlined,
          tokens.accent,
          'Single hop · about ${Format.latency(chain.estimatedLatencyMs)}',
          'One hop behaves like an ordinary VPN. Browsing should feel normal.',
        ),
      LatencyExpectation.noticeable => (
          Icons.timelapse_outlined,
          tokens.warning,
          'Two hops · about '
              '${Format.latency(chain.estimatedLatencyMs)}–'
              '${Format.latency(chain.worstCaseLatencyMs)}',
          'Each hop adds a full round trip. Pages will take a beat longer to '
              'start loading. This is the chain working, not a fault.',
        ),
      LatencyExpectation.severe => (
          Icons.warning_amber_outlined,
          tokens.warning,
          '${chain.length} hops · expect '
              '${Format.latency(chain.estimatedLatencyMs)}–'
              '${Format.latency(chain.worstCaseLatencyMs)}',
          'Latency compounds: every hop adds a full round trip, so a 3-hop '
              'chain routinely lands at 400–900 ms. Video calls and games '
              'will suffer. Throughput drops too. Drop a hop if you need speed '
              'more than depth.',
        ),
    };

    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: color.withValues(alpha: 0.32)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(icon, size: 20, color: color),
          const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  headline,
                  style: context.text.titleSmall?.copyWith(color: color),
                ),
                if (!compact) ...<Widget>[
                  const SizedBox(height: AppSpacing.xs),
                  Text(body, style: context.text.bodySmall),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
