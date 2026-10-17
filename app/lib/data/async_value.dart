import 'package:flutter/foundation.dart';

import 'api/api_failure.dart';

/// A finished-or-not wrapper for anything the UI awaits.
///
/// Every async surface in this app renders one of four states, never a bare
/// spinner on a blank screen: [AsyncLoading] (skeleton), [AsyncError]
/// (explanation plus retry), empty data (guidance plus an action), and data.
@immutable
sealed class AsyncValue<T> {
  const AsyncValue();

  const factory AsyncValue.loading() = AsyncLoading<T>;
  const factory AsyncValue.data(T value) = AsyncData<T>;
  const factory AsyncValue.error(
    String message, {
    String? detail,
    ApiFailure? failure,
  }) = AsyncError<T>;

  bool get isLoading => this is AsyncLoading<T>;

  T? get valueOrNull => switch (this) {
    AsyncData<T>(:final value) => value,
    _ => null,
  };

  /// The typed failure behind an [AsyncError], or null.
  ///
  /// This is what lets a screen branch on `chain_length_exceeded` or on a
  /// 429 instead of matching on the message text — the text is written for
  /// a human and is expected to change.
  ApiFailure? get failureOrNull => switch (this) {
    AsyncError<T>(:final failure) => failure,
    _ => null,
  };

  /// True when the device could not reach the server at all, as opposed to
  /// the server refusing. Drives the offline state rather than the error one.
  bool get isOffline => failureOrNull?.isOffline ?? false;

  R map<R>({
    required R Function() loading,
    required R Function(T value) data,
    required R Function(String message, String? detail, ApiFailure? failure)
    error,
  }) => switch (this) {
    AsyncLoading<T>() => loading(),
    AsyncData<T>(:final value) => data(value),
    AsyncError<T>(:final message, :final detail, :final failure) => error(
      message,
      detail,
      failure,
    ),
  };
}

final class AsyncLoading<T> extends AsyncValue<T> {
  const AsyncLoading();
}

final class AsyncData<T> extends AsyncValue<T> {
  const AsyncData(this.value);
  final T value;
}

final class AsyncError<T> extends AsyncValue<T> {
  const AsyncError(this.message, {this.detail, this.failure});

  /// One line, user-facing, already actionable.
  final String message;

  /// Optional second line with the specifics.
  final String? detail;

  /// The failure this was built from, when there was one. Null for errors
  /// raised by the mock repository or by non-API code.
  final ApiFailure? failure;
}
