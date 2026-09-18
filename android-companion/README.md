# JARVIS Android Companion

This is the first-stage Android endpoint for the existing JARVIS Core. It is not a second assistant and does not store JARVIS memory.

## Build

Open this directory in Android Studio 2025.3 or newer, allow Gradle sync, and run the `app` configuration on an Android 8+ device or emulator. The project uses Android Gradle Plugin 8.7.3 with Gradle 8.9 and Java 17.

If Android Studio has not already cached Gradle 8.9, the first sync downloads it once. A complete download is required; later syncs use the local Gradle distribution. The wrapper timeout is set to ten minutes to tolerate the Gradle CDN redirect.

## Pair

1. Start JARVIS on the computer.
2. Use JARVIS's existing **Remote Control** button to display the one-time pairing key or QR target.
3. Enter the Core URL and pairing key in the app.
4. The app receives a device token and stores it in Android Keystore-backed encrypted preferences.
5. Later launches reconnect through `/api/device-login` without re-pairing.

The app uses authenticated HTTPS/WebSocket endpoints when the Core has TLS enabled. Do not expose the server publicly without TLS and a network-level protection layer.
