import 'package:flutter/material.dart';

import '../../../core/motion/motion.dart';
import '../../../core/routing/proxy_engine.dart';
import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';

/// The emotional centre of the app: one large, unambiguous control.
///
/// The *fill* reports the current state; the *label* names the action the tap
/// performs. Size is capped so it still fits a 375 px viewport with the stats
/// block visible underneath.
class ConnectButton extends StatefulWidget {
  const ConnectButton({
    super.key,
    required this.status,
    required this.onPressed,
    this.isWorking = false,
  });

  final EngineStatus status;
  final VoidCallback? onPressed;

  /// True while the app is working on a connect but the engine has not been
  /// reached yet — resolving the chain, fetching credentials, pre-flighting.
  ///
  /// Found on a device: without it the control reports the *engine's* state,
  /// which during a retry is still the previous `error`, so the button read
  /// "RETRY" while a connect was already running. A user who wants to stop
  /// that connect is then looking at a control that offers to start one.
  final bool isWorking;

  @override
  State<ConnectButton> createState() => _ConnectButtonState();
}

class _ConnectButtonState extends State<ConnectButton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: AppMotion.pulse,
  );

  @override
  void didUpdateWidget(covariant ConnectButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncPulse();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _syncPulse();
  }

  /// What the control should *look* like, which is not always what the
  /// engine's own state says — see [ConnectButton.isWorking].
  EngineState get _effectiveState =>
      widget.isWorking && widget.status.state != EngineState.disconnecting
          ? EngineState.connecting
          : widget.status.state;

  void _syncPulse() {
    final shouldRun = _effectiveState == EngineState.connecting &&
        MotionScope.allowAmbient(context);
    if (shouldRun && !_pulse.isAnimating) {
      _pulse.repeat(reverse: true);
    } else if (!shouldRun && _pulse.isAnimating) {
      _pulse.stop();
      _pulse.value = 0;
    }
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final scheme = Theme.of(context).colorScheme;
    final state = _effectiveState;

    final (Color fill, Color ring, Color content, IconData icon, String action) =
        switch (state) {
      EngineState.disconnected => (
          tokens.accent,
          tokens.accent,
          tokens.onAccent,
          Icons.power_settings_new,
          'Connect',
        ),
      // The label names the action a tap performs, not the state. While a
      // connect is in flight that action is "cancel it", and the control
      // says so — a button reading "Connecting" invites the user to wait
      // for something they may need to stop.
      EngineState.connecting => (
          scheme.secondary,
          tokens.accent,
          tokens.subtleForeground,
          Icons.close,
          'Cancel',
        ),
      EngineState.connected => (
          scheme.surfaceContainer,
          tokens.accent,
          tokens.accent,
          Icons.lock_outline,
          'Disconnect',
        ),
      EngineState.disconnecting => (
          scheme.secondary,
          tokens.subtleForeground,
          tokens.subtleForeground,
          Icons.sync,
          'Stopping',
        ),
      EngineState.error => (
          scheme.surfaceContainer,
          tokens.destructiveText,
          tokens.destructiveText,
          Icons.priority_high,
          'Retry',
        ),
    };

    // 🔴 Never disabled because the engine is busy.
    //
    // It used to be. That meant the one control that turns the tunnel off
    // was dead for the entire window in which a half-built chain can take
    // the device's network away — the window in which a user most needs
    // it. A VPN whose off switch greys itself out while it is capturing
    // traffic is the failure mode this product cannot ship with.
    //
    // `disconnecting` is the one exception, and only because the teardown
    // it would re-trigger is already running and bounded.
    final enabled = widget.onPressed != null &&
        widget.status.state != EngineState.disconnecting;

    return LayoutBuilder(
      builder: (context, constraints) {
        final size = constraints.maxWidth.isFinite
            ? constraints.maxWidth.clamp(180.0, 232.0)
            : 220.0;

        return Center(
          child: Semantics(
            button: true,
            enabled: enabled,
            label: '$action. Tunnel is ${_stateWord(state)}.',
            child: AnimatedBuilder(
              animation: _pulse,
              builder: (context, child) {
                final glow = state == EngineState.connected
                    ? 0.55
                    : state == EngineState.connecting
                        ? 0.25 + _pulse.value * 0.5
                        : 0.0;
                return Container(
                  width: size,
                  height: size,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: ring.withValues(alpha: 0.28 + glow * 0.5),
                      width: 2,
                    ),
                    boxShadow: glow > 0
                        ? <BoxShadow>[
                            BoxShadow(
                              color: ring.withValues(alpha: glow * 0.35),
                              blurRadius: 28 + glow * 16,
                              spreadRadius: 1,
                            ),
                          ]
                        : null,
                  ),
                  padding: const EdgeInsets.all(AppSpacing.sm + AppSpacing.xs),
                  child: child,
                );
              },
              child: Material(
                color: fill,
                shape: const CircleBorder(),
                clipBehavior: Clip.antiAlias,
                child: InkWell(
                  onTap: enabled ? widget.onPressed : null,
                  customBorder: const CircleBorder(),
                  child: Padding(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: <Widget>[
                        Icon(icon, size: 40, color: content),
                        const SizedBox(height: AppSpacing.sm),
                        FittedBox(
                          fit: BoxFit.scaleDown,
                          child: Text(
                            action.toUpperCase(),
                            style: context.text.titleMedium?.copyWith(
                              color: content,
                              letterSpacing: 1.6,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  static String _stateWord(EngineState state) => switch (state) {
        EngineState.disconnected => 'disconnected',
        EngineState.connecting => 'connecting',
        EngineState.connected => 'connected',
        EngineState.disconnecting => 'disconnecting',
        EngineState.error => 'in an error state',
      };
}
