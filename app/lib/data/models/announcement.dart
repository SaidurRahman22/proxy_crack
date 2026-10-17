import 'package:flutter/foundation.dart';

/// Operator copy from `GET /api/content/`.
///
/// Readable without a token — onboarding text is needed before a user has
/// one — and unpublished rows are never exposed.
@immutable
class Announcement {
  const Announcement({
    required this.id,
    required this.key,
    required this.kind,
    required this.title,
    required this.body,
    this.priority = 0,
    this.publishedAt,
    this.updatedAt,
  });

  final int id;

  /// Stable lookup key, e.g. `onboarding.welcome`.
  final String key;

  /// `announcement` | `notice` | `onboarding` — `ContentKind`.
  final String kind;

  final String title;
  final String body;

  /// Higher sorts first.
  final int priority;

  final DateTime? publishedAt;
  final DateTime? updatedAt;

  /// A stable identity for a `ValueKey` on a dynamic list item: the row id
  /// plus the update stamp, so edited copy re-renders instead of being
  /// treated as unchanged.
  String get listKey => '$id:${updatedAt?.millisecondsSinceEpoch ?? 0}';

  factory Announcement.fromJson(Map<String, dynamic> json) => Announcement(
    id: (json['id'] as num?)?.toInt() ?? 0,
    key: json['key'] as String? ?? '',
    kind: json['kind'] as String? ?? 'announcement',
    title: json['title'] as String? ?? '',
    body: json['body'] as String? ?? '',
    priority: (json['priority'] as num?)?.toInt() ?? 0,
    publishedAt: DateTime.tryParse(json['published_at'] as String? ?? ''),
    updatedAt: DateTime.tryParse(json['updated_at'] as String? ?? ''),
  );
}
