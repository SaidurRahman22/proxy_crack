import 'package:flutter/foundation.dart';

/// One page of a DRF `PageNumberPagination` response.
///
/// The proxy inventory is **never returned unpaginated** (architecture.md
/// §3) — `PAGE_SIZE` is 25 and `max_page_size` is 100 — so the client has to
/// deal with pages even when the dev database holds a single proxy.
@immutable
class Paged<T> {
  const Paged({
    required this.results,
    required this.count,
    this.next,
    this.previous,
  });

  final List<T> results;

  /// Total across all pages, not the length of [results].
  final int count;

  /// Absolute URL of the next page, or null on the last one.
  final String? next;
  final String? previous;

  bool get hasMore => next != null && next!.isNotEmpty;

  static Paged<T> empty<T>() => Paged<T>(results: const [], count: 0);

  /// Parses either a paginated envelope or a bare list — the content
  /// endpoint returns a bare list when the queryset fits on one page.
  factory Paged.fromJson(
    Object? json,
    T Function(Map<String, dynamic>) item,
  ) {
    if (json is List) {
      final results = json
          .whereType<Map<String, dynamic>>()
          .map(item)
          .toList(growable: false);
      return Paged<T>(results: results, count: results.length);
    }
    if (json is Map<String, dynamic>) {
      final results = (json['results'] as List<dynamic>? ?? const <dynamic>[])
          .whereType<Map<String, dynamic>>()
          .map(item)
          .toList(growable: false);
      return Paged<T>(
        results: results,
        count: (json['count'] as num?)?.toInt() ?? results.length,
        next: json['next'] as String?,
        previous: json['previous'] as String?,
      );
    }
    return Paged<T>(results: const [], count: 0);
  }
}
