package com.jarvis.companion

/**
 * Future voice transport boundary. JARVIS Core remains the voice/AI source of
 * truth; the Android app will provide microphone frames and play returned audio.
 */
interface VoiceTransport {
    fun startInput(onPcm16: (ByteArray) -> Unit)
    fun stopInput()
    fun playOutput(pcm16: ByteArray, sampleRate: Int)
    fun stopOutput()
}
