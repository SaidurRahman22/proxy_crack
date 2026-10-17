import 'package:flutter/material.dart';

import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/models/proxy_chain.dart';

/// Says, before the user connects, that this chain cannot carry UDP.
///
/// memory.md P7 and engine-decision.md §2.5. An HTTP proxy is TCP-only —
/// Xray's own docs: *"`http` can only proxy the TCP protocol; UDP-based
/// protocols cannot pass through"* — and a chain is only as UDP-capable as
/// its weakest hop. One HTTP hop anywhere makes the whole chain TCP-only.
///
/// This is worth a dedicated notice because of *how* it fails. The engine
/// black-holes UDP so QUIC fails immediately and falls back to TCP, which is
/// the right behaviour — but video calls, WebRTC and most games simply stop
/// working, and from inside those apps it looks like a broken network rather
/// than a chosen trade-off. Silent black-holing generates support tickets;
/// letting it hang generates worse ones. Saying so up front generates none.
///
/// The text describes what the engine actually does, not an estimate:
/// [ProxyChain.supportsUdp] is the same value the config generator branches
/// on when it decides whether to emit the UDP black-hole rule.
class UdpNotice extends StatelessWidget {
  const UdpNotice({super.key, required this.chain});

  final ProxyChain chain;

  @override
  Widget build(BuildContext context) {
    if (chain.isEmpty || chain.supportsUdp) return const SizedBox.shrink();

    final tokens = context.tokens;
    final color = tokens.warning;
    final blockers = chain.tcpOnlyHops;
    final positions = blockers
        .map((h) => '${h.position + 1}')
        .join(' and ');

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
          Icon(Icons.videocam_off_outlined, size: 20, color: color),
          const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  'This chain is TCP only',
                  style: context.text.titleSmall?.copyWith(color: color),
                ),
                const SizedBox(height: AppSpacing.xs),
                Text(
                  blockers.length == 1
                      ? 'Hop $positions is an HTTP proxy, and HTTP cannot '
                            'carry UDP. While connected, UDP is blocked '
                            'outright: video calls, WebRTC and most games '
                            'will not work, and QUIC falls back to TCP. '
                            'Swap that hop for SOCKS5 or Shadowsocks to '
                            'carry UDP.'
                      : 'Hops $positions are HTTP proxies, and HTTP cannot '
                            'carry UDP. While connected, UDP is blocked '
                            'outright: video calls, WebRTC and most games '
                            'will not work, and QUIC falls back to TCP. '
                            'Swap them for SOCKS5 or Shadowsocks to carry '
                            'UDP.',
                  style: context.text.bodySmall,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
