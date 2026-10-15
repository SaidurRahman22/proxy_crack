import 'package:flutter/foundation.dart';

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
  const factory AsyncValue.error(String message, {String? detail}) =
      AsyncError<T>;

  bool get isLoading => this is AsyncLoading<T>;

  T? get valueOrNull => switch (this) {
        AsyncData<T>(:final value) => value,
        _ => null,
      };

  R map<R>({
    required R Function() loading,
    required R Function(T value) data,
    required R Function(String message, String? detail) error,
  }) =>
      switch (this) {
        AsyncLoading<T>() => loading(),
        AsyncData<T>(:final value) => data(value),
        AsyncError<T>(:final message, :final detail) => error(message, detail),
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
  const AsyncError(this.message, {this.detail});

  /// One line, user-facing, already actionable.
  final String message;

  /// Optional second line with the specifics.
  final String? detail;
}
