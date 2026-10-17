import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:flutter/material.dart';

import '../../app/session_controller.dart';
import '../../core/layout/breakpoints.dart';
import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';
import '../../data/api/api_config.dart';
import '../../data/api/api_failure.dart';
import '../../shared/widgets/surface.dart';

/// Sign in or create an account.
///
/// Two modes in one form rather than two screens: the fields are nearly
/// identical, and a user who picked the wrong one should not lose what they
/// typed.
class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key, required this.session, required this.config});

  final SessionController session;
  final ApiConfig config;

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  final _passwordFocus = FocusNode();

  bool _register = false;
  bool _obscure = true;

  @override
  void initState() {
    super.initState();
    widget.session.addListener(_onSession);
  }

  @override
  void dispose() {
    widget.session.removeListener(_onSession);
    _email.dispose();
    _password.dispose();
    _confirm.dispose();
    _passwordFocus.dispose();
    super.dispose();
  }

  void _onSession() {
    if (mounted) setState(() {});
  }

  Future<void> _submit() async {
    if (widget.session.isBusy) return;
    if (!(_formKey.currentState?.validate() ?? false)) return;
    FocusScope.of(context).unfocus();

    if (_register) {
      await widget.session.signUp(
        email: _email.text,
        password: _password.text,
        passwordConfirm: _confirm.text,
      );
    } else {
      await widget.session.signIn(
        email: _email.text,
        password: _password.text,
      );
    }
  }

  void _toggleMode() {
    setState(() {
      _register = !_register;
      widget.session.clearFailure();
    });
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final tokens = context.tokens;
    final fieldErrors = session.fieldErrors;

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: ConstrainedBox(
              constraints: const BoxConstraints(
                maxWidth: Layout.cardMaxWidth,
              ),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    const _Brand(),
                    const SizedBox(height: AppSpacing.xl),
                    Text(
                      _register ? 'Create an account' : 'Sign in',
                      style: context.text.headlineSmall,
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      _register
                          ? 'Your plan, your chains and your proxy list live '
                                'on the server, not on this device.'
                          : 'The proxy list and your chains are scoped to '
                                'your plan by the server on every request.',
                      style: context.text.bodySmall,
                    ),
                    const SizedBox(height: AppSpacing.lg),

                    TextFormField(
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const <String>[AutofillHints.email],
                      textInputAction: TextInputAction.next,
                      enabled: !session.isBusy,
                      decoration: InputDecoration(
                        labelText: 'Email',
                        prefixIcon: const Icon(Icons.alternate_email, size: 20),
                        errorText: fieldErrors['email'],
                      ),
                      onFieldSubmitted: (_) => _passwordFocus.requestFocus(),
                      validator: (value) {
                        final text = value?.trim() ?? '';
                        if (text.isEmpty) return 'Enter your email address.';
                        if (!text.contains('@') || text.endsWith('@')) {
                          return 'That does not look like an email address.';
                        }
                        return null;
                      },
                    ),
                    const SizedBox(height: AppSpacing.md),

                    TextFormField(
                      controller: _password,
                      focusNode: _passwordFocus,
                      obscureText: _obscure,
                      enabled: !session.isBusy,
                      autofillHints: <String>[
                        _register
                            ? AutofillHints.newPassword
                            : AutofillHints.password,
                      ],
                      textInputAction: _register
                          ? TextInputAction.next
                          : TextInputAction.done,
                      decoration: InputDecoration(
                        labelText: 'Password',
                        prefixIcon: const Icon(Icons.lock_outline, size: 20),
                        errorText: fieldErrors['password'],
                        suffixIcon: IconButton(
                          // 48dp minimum target, like every other control.
                          constraints: const BoxConstraints(
                            minWidth: AppTouch.minTarget,
                            minHeight: AppTouch.minTarget,
                          ),
                          onPressed: () =>
                              setState(() => _obscure = !_obscure),
                          icon: Icon(
                            _obscure
                                ? Icons.visibility_outlined
                                : Icons.visibility_off_outlined,
                            size: 20,
                          ),
                          tooltip: _obscure
                              ? 'Show password'
                              : 'Hide password',
                        ),
                      ),
                      onFieldSubmitted: (_) {
                        if (!_register) _submit();
                      },
                      validator: (value) {
                        final text = value ?? '';
                        if (text.isEmpty) return 'Enter your password.';
                        // Django's validators are authoritative; this only
                        // avoids a round trip for the obvious case.
                        if (_register && text.length < 8) {
                          return 'Use at least 8 characters.';
                        }
                        return null;
                      },
                    ),

                    if (_register) ...<Widget>[
                      const SizedBox(height: AppSpacing.md),
                      TextFormField(
                        controller: _confirm,
                        obscureText: _obscure,
                        enabled: !session.isBusy,
                        textInputAction: TextInputAction.done,
                        decoration: InputDecoration(
                          labelText: 'Confirm password',
                          prefixIcon: const Icon(
                            Icons.lock_reset_outlined,
                            size: 20,
                          ),
                          errorText: fieldErrors['password_confirm'],
                        ),
                        onFieldSubmitted: (_) => _submit(),
                        validator: (value) =>
                            value == _password.text
                            ? null
                            : 'The passwords do not match.',
                      ),
                    ],

                    if (session.failure != null) ...<Widget>[
                      const SizedBox(height: AppSpacing.md),
                      _AuthFailureNotice(failure: session.failure!),
                    ],

                    const SizedBox(height: AppSpacing.lg),
                    SizedBox(
                      height: AppTouch.minTarget,
                      child: FilledButton(
                        onPressed: session.isBusy ? null : _submit,
                        child: session.isBusy
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              )
                            : Text(
                                _register ? 'Create account' : 'Sign in',
                              ),
                      ),
                    ),
                    const SizedBox(height: AppTouch.minGap),
                    SizedBox(
                      height: AppTouch.minTarget,
                      child: TextButton(
                        onPressed: session.isBusy ? null : _toggleMode,
                        child: Text(
                          _register
                              ? 'I already have an account'
                              : 'Create an account',
                        ),
                      ),
                    ),

                    // The API endpoint is a developer affordance, not something a
                    // user signing in should see: it is noise to them and it
                    // publishes our infrastructure to anyone holding the phone.
                    // Debug builds only.
                    if (kDebugMode) ...<Widget>[
                      const SizedBox(height: AppSpacing.lg),
                      Surface(
                        padding: const EdgeInsets.all(AppSpacing.sm + 4),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Icon(
                              Icons.dns_outlined,
                              size: 16,
                              color: tokens.mutedForeground,
                            ),
                            const SizedBox(width: AppSpacing.sm),
                            Expanded(
                              child: Text(
                                'API: ${widget.config.baseUrl}',
                                style: context.text.bodySmall,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Explains a sign-in failure without guessing.
///
/// `/api/auth/login/` answers 401 for every bad credential **and** for a
/// non-existent account, deliberately, so the response cannot be used to
/// enumerate users. The copy here must not imply which it was.
class _AuthFailureNotice extends StatelessWidget {
  const _AuthFailureNotice({required this.failure});

  final ApiFailure failure;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final f = failure;

    final (IconData icon, String text) = switch (f) {
      ApiException(isThrottled: true) => (
        Icons.hourglass_empty_outlined,
        f.message,
      ),
      ApiException(status: 401) => (
        Icons.error_outline,
        'That email and password combination was not accepted.',
      ),
      _ when f.isOffline => (
        Icons.wifi_off_outlined,
        '${f.message} ${f.detailLine ?? ''}'.trim(),
      ),
      _ => (Icons.error_outline, f.message),
    };

    return Semantics(
      liveRegion: true,
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.sm + AppSpacing.xs),
        decoration: BoxDecoration(
          color: tokens.destructiveText.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(AppRadius.control),
          border: Border.all(
            color: tokens.destructiveText.withValues(alpha: 0.32),
          ),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(icon, size: 18, color: tokens.destructiveText),
            const SizedBox(width: AppSpacing.sm),
            Expanded(child: Text(text, style: context.text.bodySmall)),
          ],
        ),
      ),
    );
  }
}

class _Brand extends StatelessWidget {
  const _Brand();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Row(
      children: <Widget>[
        Container(
          width: 44,
          height: 44,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: scheme.primary,
            borderRadius: BorderRadius.circular(AppRadius.control),
          ),
          child: Icon(Icons.hub_outlined, size: 24, color: scheme.onPrimary),
        ),
        const SizedBox(width: AppSpacing.md),
        Text('Proxy Crack', style: context.text.titleLarge),
      ],
    );
  }
}
