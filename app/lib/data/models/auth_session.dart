import 'package:flutter/foundation.dart';

import '../api/transport.dart';

/// The signed-in user, as `UserSerializer` sends it.
@immutable
class AccountUser {
  const AccountUser({
    required this.id,
    required this.email,
    this.firstName = '',
    this.lastName = '',
    this.dateJoined,
  });

  final int id;
  final String email;
  final String firstName;
  final String lastName;
  final DateTime? dateJoined;

  /// What the UI puts next to the sign-out button.
  String get displayName {
    final full = '$firstName $lastName'.trim();
    return full.isEmpty ? email : full;
  }

  /// Two letters for an avatar chip, never an emoji.
  String get initials {
    final first = firstName.trim();
    final last = lastName.trim();
    if (first.isNotEmpty && last.isNotEmpty) {
      return '${first[0]}${last[0]}'.toUpperCase();
    }
    final local = email.split('@').first;
    if (local.length >= 2) return local.substring(0, 2).toUpperCase();
    return local.isEmpty ? '?' : local.toUpperCase();
  }

  factory AccountUser.fromJson(Map<String, dynamic> json) => AccountUser(
    id: (json['id'] as num?)?.toInt() ?? 0,
    email: json['email'] as String? ?? '',
    firstName: json['first_name'] as String? ?? '',
    lastName: json['last_name'] as String? ?? '',
    dateJoined: DateTime.tryParse(json['date_joined'] as String? ?? ''),
  );
}

/// What `/auth/login/` and `/auth/register/` both return: a token pair plus
/// the user, so signing in costs one round trip rather than two.
@immutable
class AuthSession {
  const AuthSession({required this.tokens, required this.user});

  final TokenPair tokens;
  final AccountUser user;

  factory AuthSession.fromJson(Map<String, dynamic> json) => AuthSession(
    tokens: TokenPair.fromJson(json),
    user: AccountUser.fromJson(
      (json['user'] as Map<String, dynamic>?) ?? const <String, dynamic>{},
    ),
  );

  /// No token ever reaches a log line.
  @override
  String toString() => 'AuthSession(${user.email})';
}
