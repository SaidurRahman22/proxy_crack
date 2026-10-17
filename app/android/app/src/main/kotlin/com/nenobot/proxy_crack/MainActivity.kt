package com.nenobot.proxy_crack

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.util.Log
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * The Flutter <-> Android seam (layer 3 of engine-decision.md §4.1).
 *
 * Commands go down a `MethodChannel`; state comes up two `EventChannel`s.
 * State is never polled over the method channel — that reinvents an event
 * stream badly, and the service already has one.
 *
 * The consent dance lives here rather than in the service because
 * `VpnService.prepare()` returns an `Intent` that only an Activity can
 * launch, and the result only an Activity can receive.
 */
class MainActivity : FlutterActivity() {

    companion object {
        private const val TAG = "MainActivity"

        private const val METHOD_CHANNEL = "com.nenobot.proxy_crack/vpn"
        private const val STATUS_CHANNEL = "com.nenobot.proxy_crack/vpn_status"
        private const val STATS_CHANNEL = "com.nenobot.proxy_crack/vpn_stats"

        private const val REQUEST_VPN_CONSENT = 0x7001
        private const val REQUEST_NOTIFICATIONS = 0x7002
    }

    /**
     * The in-flight `requestPermission` call.
     *
     * Kept on the Activity, not the service: the Activity can be recreated
     * while the consent dialog is up, and completing a `Result` twice
     * throws. Cleared before completion, every time.
     */
    private var pendingConsent: MethodChannel.Result? = null

    private var statusSink: EventChannel.EventSink? = null
    private var statsSink: EventChannel.EventSink? = null

    private val statusListener: (Map<String, Any?>) -> Unit = { event ->
        statusSink?.success(event)
    }
    private val statsListener: (Map<String, Any?>) -> Unit = { event ->
        statsSink?.success(event)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotificationPermissionIfNeeded()
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger

        MethodChannel(messenger, METHOD_CHANNEL).setMethodCallHandler { call, result ->
            handle(call, result)
        }

        EventChannel(messenger, STATUS_CHANNEL).setStreamHandler(
            object : EventChannel.StreamHandler {
                override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                    statusSink = events
                    // addStatusListener replays the current snapshot, so a
                    // screen attaching to an already-running tunnel is told
                    // the truth immediately rather than on the next change.
                    VpnStateBus.addStatusListener(statusListener)
                }

                override fun onCancel(arguments: Any?) {
                    VpnStateBus.removeStatusListener(statusListener)
                    statusSink = null
                }
            }
        )

        EventChannel(messenger, STATS_CHANNEL).setStreamHandler(
            object : EventChannel.StreamHandler {
                override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                    statsSink = events
                    VpnStateBus.addStatsListener(statsListener)
                }

                override fun onCancel(arguments: Any?) {
                    VpnStateBus.removeStatsListener(statsListener)
                    statsSink = null
                }
            }
        )
    }

    private fun handle(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            // null from prepare() means consent is already granted.
            "prepare" -> result.success(VpnService.prepare(this) == null)

            "requestPermission" -> requestConsent(result)

            "start" -> start(call, result)

            "stop" -> {
                startService(
                    Intent(this, VpnConnectionService::class.java).apply {
                        action = VpnConnectionService.ACTION_DISCONNECT
                    }
                )
                result.success(true)
            }

            "status" -> result.success(VpnStateBus.current())

            "stats" -> result.success(VpnStateBus.currentStats())

            "engineVersion" -> result.success(XrayBridge.version())

            // Hands a generated config to libXray's `testXray`, which builds
            // an instance and closes it without starting. The way to check
            // the config generator against the real parser with no TUN.
            "validateConfig" -> {
                val config = call.argument<String>("config")
                if (config.isNullOrEmpty()) {
                    result.error("unknown", "No config supplied.", null)
                } else {
                    result.success(XrayBridge.testXray(config))
                }
            }

            else -> result.notImplemented()
        }
    }

    private fun requestConsent(result: MethodChannel.Result) {
        val intent = VpnService.prepare(this)
        if (intent == null) {
            result.success(true)
            return
        }
        // A second request while one is outstanding: answer the old one
        // rather than leaving a Dart future hanging forever.
        pendingConsent?.success(false)
        pendingConsent = result
        try {
            startActivityForResult(intent, REQUEST_VPN_CONSENT)
        } catch (t: Throwable) {
            Log.e(TAG, "could not launch the VPN consent dialog", t)
            pendingConsent = null
            result.error(
                VpnStateBus.FAILURE_PERMISSION_DENIED,
                "This device would not show the VPN permission dialog.",
                null
            )
        }
    }

    private fun start(call: MethodCall, result: MethodChannel.Result) {
        val config = call.argument<String>("config")
        if (config.isNullOrEmpty()) {
            result.error(VpnStateBus.FAILURE_UNKNOWN, "No config supplied.", null)
            return
        }
        if (VpnService.prepare(this) != null) {
            result.error(
                VpnStateBus.FAILURE_PERMISSION_DENIED,
                "VPN permission has not been granted.",
                null
            )
            return
        }

        val intent = Intent(this, VpnConnectionService::class.java).apply {
            action = VpnConnectionService.ACTION_CONNECT
            putExtra(VpnConnectionService.EXTRA_CONFIG, config)
            putExtra(VpnConnectionService.EXTRA_MTU, call.argument<Int>("mtu") ?: 1500)
            putExtra(
                VpnConnectionService.EXTRA_IPV4_ADDRESS,
                call.argument<String>("ipv4Address")
            )
            putExtra(
                VpnConnectionService.EXTRA_IPV4_PREFIX,
                call.argument<Int>("ipv4Prefix") ?: 32
            )
            putExtra(
                VpnConnectionService.EXTRA_IPV6_ADDRESS,
                call.argument<String>("ipv6Address")
            )
            putExtra(
                VpnConnectionService.EXTRA_IPV6_PREFIX,
                call.argument<Int>("ipv6Prefix") ?: 126
            )
            putExtra(
                VpnConnectionService.EXTRA_ROUTE_IPV6,
                call.argument<Boolean>("routeIpv6") ?: true
            )
            putStringArrayListExtra(
                VpnConnectionService.EXTRA_DNS_SERVERS,
                ArrayList(call.argument<List<String>>("dnsServers") ?: listOf("1.1.1.1"))
            )
            putExtra(
                VpnConnectionService.EXTRA_SUPPORTS_UDP,
                call.argument<Boolean>("supportsUdp") ?: true
            )
            putExtra(
                VpnConnectionService.EXTRA_HOP_COUNT,
                call.argument<Int>("hopCount") ?: 0
            )
            putExtra(
                VpnConnectionService.EXTRA_ROUTE_SUMMARY,
                call.argument<String>("routeSummary").orEmpty()
            )
            putExtra(
                VpnConnectionService.EXTRA_CHAIN_NAME,
                call.argument<String>("chainName").orEmpty()
            )
        }

        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent)
            } else {
                startService(intent)
            }
            result.success(true)
        } catch (t: Throwable) {
            Log.e(TAG, "could not start the tunnel service", t)
            result.error(
                VpnStateBus.FAILURE_UNKNOWN,
                t.message ?: "The tunnel service would not start.",
                null
            )
        }
    }

    @Deprecated("Flutter's FlutterActivity still routes results here.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == REQUEST_VPN_CONSENT) {
            val pending = pendingConsent
            pendingConsent = null
            pending?.success(resultCode == Activity.RESULT_OK)
            return
        }
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
    }

    /**
     * The foreground service runs whether or not this is granted, but the
     * notification is then hidden — and a VPN with no visible indicator is
     * both a bad experience and, on some OEM builds, a service that gets
     * killed sooner. Asked once, at launch, and never blocked on.
     */
    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) return
        try {
            requestPermissions(
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                REQUEST_NOTIFICATIONS
            )
        } catch (t: Throwable) {
            Log.w(TAG, "could not ask for notification permission", t)
        }
    }
}
