package com.jarvis.companion

import android.Manifest
import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Bundle
import android.os.BatteryManager
import android.content.Intent
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import okhttp3.*
import okio.ByteString
import okio.ByteString.Companion.toByteString
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import javax.net.ssl.SSLHandshakeException
import java.util.concurrent.TimeUnit

class MainActivity : Activity() {
    private val client = OkHttpClient.Builder().readTimeout(30, TimeUnit.SECONDS).build()
    private lateinit var status: TextView
    private lateinit var activeTask: TextView
    private lateinit var events: TextView
    private lateinit var serverInput: EditText
    private lateinit var pinInput: EditText
    private lateinit var commandInput: EditText
    private lateinit var voiceButton: Button
    private lateinit var content: LinearLayout
    private lateinit var screenTitle: TextView
    private lateinit var errorText: TextView
    private var lastState = JSONObject()
    private var token = ""
    private var deviceToken = ""
    private var deviceId = ""
    private var socket: WebSocket? = null
    private var recording = false
    private var recorder: AudioRecord? = null
    private var recordThread: Thread? = null
    private var speaker: AudioTrack? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        createNotificationChannel()
        requestNotificationPermission()
        buildUi()
        loadCredentials()
        consumePairingIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        consumePairingIntent(intent)
    }

    private fun consumePairingIntent(intent: Intent?) {
        if (intent?.action != Intent.ACTION_VIEW || intent.data?.scheme != "jarvis") return
        val server = intent.data?.getQueryParameter("server").orEmpty()
        val key = intent.data?.getQueryParameter("key").orEmpty()
        showScreen("SETTINGS")
        // showScreen recreates the settings fields; populate them afterwards.
        serverInput.setText(server)
        pinInput.setText(key)
        if (server.isBlank() || key.isBlank()) {
            showError("This QR code is incomplete. Generate a fresh code in JARVIS.")
            return
        }
        pair()
    }

    private fun buildUi() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 28, 24, 16)
            setBackgroundColor(0xFF071018.toInt())
        }
        val header = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        screenTitle = label("COMMAND CENTER")
        screenTitle.textSize = 20f
        header.addView(screenTitle, LinearLayout.LayoutParams(0, -2, 1f))
        status = label("OFFLINE")
        status.textSize = 12f
        header.addView(status)
        root.addView(header)
        errorText = label("")
        errorText.setTextColor(0xFFFF8A8A.toInt())
        errorText.visibility = View.GONE
        root.addView(errorText)
        content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val scroll = ScrollView(this).apply { addView(content) }
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))

        val nav = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        listOf("HOME", "TASKS", "ACTIVITY", "VOICE", "SETTINGS").forEach { name ->
            nav.addView(Button(this).apply {
                text = name
                textSize = 10f
                setOnClickListener { showScreen(name) }
            }, LinearLayout.LayoutParams(0, -2, 1f))
        }
        root.addView(nav)
        // Keep the existing sync/event targets alive while screens are rebuilt.
        serverInput = input("Core URL")
        pinInput = input("Pairing key")
        commandInput = input("Command")
        activeTask = label("No active task")
        events = label("")
        voiceButton = Button(this).apply {
            text = "START VOICE"
            setOnClickListener { if (recording) stopVoice() else startVoice() }
        }
        setContentView(root)
        showScreen("HOME")
    }

    private fun showScreen(name: String) {
        content.removeAllViews()
        screenTitle.text = when (name) {
            "HOME" -> "COMMAND CENTER"
            "TASKS" -> "TASK CENTER"
            "ACTIVITY" -> "ACTIVITY"
            "VOICE" -> "JARVIS VOICE"
            "SETTINGS" -> "CONNECTION"
            else -> name
        }
        when (name) {
            "HOME" -> showHome()
            "TASKS" -> showTasks()
            "ACTIVITY" -> showActivity()
            "VOICE" -> showVoice()
            "SETTINGS" -> showSettings()
        }
    }

    private fun showHome() {
        content.addView(label("JARVIS", 28f))
        content.addView(card("SYSTEM STATUS", status.text.toString(), "Core connection and live state"))
        val task = lastState.optJSONObject("active_task")
        content.addView(card("CURRENT ACTIVITY",
            if (task == null) "Waiting for work" else task.optString("objective", "Working"),
            if (task == null) "No active task" else task.optString("current_step", "In progress")))
        val tasks = lastState.optJSONArray("tasks")
        val schedule = lastState.optJSONArray("schedule")
        content.addView(card("TODAY", "${tasks?.length() ?: 0} tasks  •  ${schedule?.length() ?: 0} events",
            "Live data from JARVIS Core"))
        content.addView(section("QUICK ACTIONS"))
        val ask = input("Ask JARVIS what to do")
        content.addView(ask)
        content.addView(Button(this).apply {
            text = "SEND TO JARVIS"
            setOnClickListener {
                commandInput = ask
                sendCommand()
            }
        })
        content.addView(Button(this).apply {
            text = "WHAT HAPPENED WHILE I WAS AWAY?"
            setOnClickListener { commandInput = input(""); commandInput.setText("What happened while I was away?"); sendCommand() }
        })
    }

    private fun showTasks() {
        content.addView(section("TASKS FROM JARVIS CORE"))
        val tasks = lastState.optJSONArray("tasks")
        if (tasks == null || tasks.length() == 0) {
            content.addView(label("No tasks available. Ask JARVIS to create one."))
            return
        }
        for (i in 0 until tasks.length()) {
            val task = tasks.getJSONObject(i)
            content.addView(card(task.optString("title", "Untitled"),
                "${task.optString("status", "open").uppercase()}  •  ${task.optString("priority", "normal")}",
                listOf(task.optString("due_at"), task.optString("source")).filter { it.isNotBlank() }.joinToString("  • ")))
        }
    }

    private fun showActivity() {
        content.addView(section("RECENT JARVIS ACTIVITY"))
        if (events.text.toString().trim().isEmpty()) content.addView(label("No activity received yet."))
        else content.addView(label(events.text.toString()))
    }

    private fun showVoice() {
        content.addView(section("VOICE CHANNEL"))
        content.addView(card("VOICE STATE", if (recording) "LISTENING" else "WAITING",
            "Audio is routed through JARVIS Core. The phone is an endpoint, not a second assistant."))
        content.addView(voiceButton)
        content.addView(label("Use the microphone button to speak. JARVIS response audio returns through this device."))
        commandInput = input("Or type a command")
        content.addView(commandInput)
        content.addView(Button(this).apply { text = "SEND COMMAND"; setOnClickListener { sendCommand() } })
    }

    private fun showSettings() {
        content.addView(section("CORE CONNECTION"))
        serverInput = input("Core URL, e.g. https://192.168.1.10:8000")
        serverInput.setText(securePrefs().getString("server", "") ?: "")
        content.addView(serverInput)
        pinInput = input("One-time pairing key")
        content.addView(pinInput)
        content.addView(Button(this).apply { text = "PAIR / CONNECT"; setOnClickListener { pairOrReconnect() } })
        content.addView(card("DEVICE", if (deviceId.isBlank()) "Not paired" else "Paired",
            "Device credentials are stored using Android Keystore-backed preferences."))
        content.addView(section("PLUGIN CENTER"))
        val plugins = lastState.optJSONArray("plugins")
        if (plugins == null || plugins.length() == 0) {
            content.addView(label("No plugin status received yet."))
        } else {
            for (i in 0 until plugins.length()) {
                val plugin = plugins.getJSONObject(i)
                val connection = if (plugin.optBoolean("connected", false)) "CONNECTED" else "DISCONNECTED"
                val enabled = if (plugin.optBoolean("enabled", false)) "ENABLED" else "DISABLED"
                content.addView(card(plugin.optString("name", "Plugin"),
                    "$connection  •  $enabled",
                    plugin.optString("provider", "") + "  •  " + plugin.optString("error", "")))
            }
        }
        content.addView(label("Permissions and external actions remain controlled by JARVIS Core."))
    }

    private fun section(text: String) = label(text, 12f).apply {
        setTextColor(0xFF38BDF8.toInt())
        setPadding(0, 22, 0, 8)
    }

    private fun card(title: String, value: String, detail: String) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(16, 14, 16, 14)
        setBackgroundColor(0xFF10202C.toInt())
        addView(label(title, 11f).apply { setTextColor(0xFF7DD3FC.toInt()) })
        addView(label(value, 19f))
        addView(label(detail, 12f).apply { setTextColor(0xFF94A3B8.toInt()) })
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, 6, 0, 6) }
    }

    private fun label(text: String, size: Float = 15f) = TextView(this).apply {
        this.text = text
        textSize = size
        setTextColor(0xFFE2E8F0.toInt())
        setPadding(0, 10, 0, 10)
    }

    private fun input(hint: String) = EditText(this).apply {
        this.hint = hint
        setTextColor(0xFFE2E8F0.toInt())
        setHintTextColor(0xFF7C8B9A.toInt())
        setSingleLine(true)
        setPadding(12, 10, 12, 10)
    }

    private fun loadCredentials() {
        val prefs = securePrefs()
        serverInput.setText(prefs.getString("server", "") ?: "")
        token = prefs.getString("token", "") ?: ""
        deviceToken = prefs.getString("device_token", "") ?: ""
        deviceId = prefs.getString("device_id", "") ?: ""
        if (token.isNotEmpty() && deviceToken.isNotEmpty()) reconnect()
    }

    private fun baseUrl(): String = serverInput.text.toString().trim().trimEnd('/')

    private fun pairOrReconnect() {
        if (deviceToken.isNotEmpty()) reconnect() else pair()
    }

    private fun pair() {
        val server = baseUrl()
        val key = pinInput.text.toString().trim()
        if (server.isBlank()) {
            showError("Enter the JARVIS Core URL.")
            return
        }
        if (key.isBlank()) {
            showError("Enter a fresh pairing key from JARVIS.")
            return
        }
        setStatus("CONNECTING")
        showError("")
        val body = JSONObject().put("pin", key)
        request("/login", body) { json ->
            if (!json.optBoolean("ok")) throw IOException(json.optString("error", "Pairing failed"))
            token = json.getString("token")
            deviceToken = json.getString("device_token")
            deviceId = json.getString("device_id")
            saveCredentials()
            openSocket()
            sync()
        }
    }

    private fun reconnect() {
        setStatus("CONNECTING")
        request("/api/device-login", JSONObject().put("device_token", deviceToken)) { json ->
            if (!json.optBoolean("ok")) throw IOException("Authentication required")
            token = json.getString("token")
            deviceId = json.optString("device_id", deviceId)
            saveCredentials()
            openSocket()
            sync()
        }
    }

    private fun openSocket() {
        socket?.close(1000, "reconnect")
        val wsUrl = baseUrl().replaceFirst("https://", "wss://").replaceFirst("http://", "ws://") + "/ws?token=$token"
        socket = client.newWebSocket(Request.Builder().url(wsUrl).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                setStatus("CONNECTED")
                sendTelemetry(webSocket)
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                setStatus("ERROR")
                appendEvent("Connection error: ${t.message}")
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = setStatus("DISCONNECTED")
            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val message = JSONObject(text)
                    if (message.optString("type") == "event") handleEvent(message.getJSONObject("event"))
                    if (message.optString("type") == "mobile_notification") {
                        val title = message.optString("title", "JARVIS")
                        val body = message.optString("message", "")
                        appendEvent("$title: $body")
                        notifyUser(title, body)
                        webSocket.send(JSONObject()
                            .put("type", "mobile_result")
                            .put("request_id", message.optString("request_id"))
                            .put("ok", true).toString())
                    }
                    if (message.optString("type") == "mobile_message") {
                        appendEvent("JARVIS: ${message.optString("message", "")}")
                        webSocket.send(JSONObject()
                            .put("type", "mobile_result")
                            .put("request_id", message.optString("request_id"))
                            .put("ok", true).toString())
                    }
                    if (message.optString("type") == "status") setStatus(message.optString("state", "CONNECTED"))
                } catch (_: Exception) { }
            }
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                playPcm(bytes.toByteArray())
            }
        })
    }

    private fun sendTelemetry(webSocket: WebSocket) {
        val battery = getSystemService(BATTERY_SERVICE) as BatteryManager
        val level = battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val notificationPermission = android.os.Build.VERSION.SDK_INT < 33 ||
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        webSocket.send(JSONObject()
            .put("type", "mobile_telemetry")
            .put("telemetry", JSONObject()
                .put("battery", level)
                .put("notification_permission", notificationPermission)
                .put("voice_permission", checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
                .put("command_permission", true)
                .put("transport", "websocket"))
            .toString())
    }

    private fun sync() {
        get("/api/phone/state") { json ->
            val state = json.optJSONObject("state") ?: return@get
            lastState = state
            val task = state.optJSONObject("active_task")
            runOnUiThread { activeTask.text = if (task == null) "No active task" else "Task: ${task.optString("objective", "")}\nStep: ${task.optString("current_step", "")}" }
        }
        get("/api/phone/events") { json ->
            val list = json.optJSONArray("events") ?: JSONArray()
            for (i in 0 until list.length()) handleEvent(list.getJSONObject(i))
        }
    }

    private fun handleEvent(event: JSONObject) {
        val id = event.optString("event_id")
        val text = "${event.optString("event_type")}: ${event.optString("message")}"
        appendEvent(text)
        if (event.optString("importance") in listOf("IMPORTANT", "URGENT")) notifyUser(event.optString("event_type"), event.optString("message"))
        post("/api/phone/events/ack", JSONObject().put("event_ids", JSONArray().put(id)))
    }

    private fun sendCommand() {
        val text = commandInput.text.toString().trim()
        if (text.isEmpty() || token.isEmpty()) return
        post("/api/command", JSONObject().put("text", text))
        commandInput.text.clear()
    }

    private fun startVoice() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 101)
            return
        }
        if (token.isEmpty()) return
        val wsProto = baseUrl().replaceFirst("https://", "wss://").replaceFirst("http://", "ws://")
        val voiceRequest = Request.Builder().url("$wsProto/ws/phone-audio?token=$token").build()
        val voiceSocket = client.newWebSocket(voiceRequest, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                runOnUiThread { voiceButton.text = "STOP VOICE"; recording = true }
                val min = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
                recorder = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min * 2, 4096))
                recorder?.startRecording()
                recordThread = Thread {
                    val buffer = ByteArray(2048)
                    while (recording) {
                        val count = recorder?.read(buffer, 0, buffer.size) ?: 0
                        if (count > 0) webSocket.send(buffer.toByteString(0, count))
                    }
                }.also { it.start() }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { appendEvent("Voice connection error: ${t.message}"); stopVoice() }
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { runOnUiThread { stopVoice() } }
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) { playPcm(bytes.toByteArray()) }
        })
        socket = voiceSocket
    }

    private fun stopVoice() {
        recording = false
        recorder?.stop()
        recorder?.release()
        recorder = null
        socket?.close(1000, "voice stopped")
        runOnUiThread { voiceButton.text = "START VOICE" }
    }

    private fun playPcm(pcm: ByteArray) {
        runOnUiThread {
            if (speaker == null) {
                speaker = AudioTrack.Builder().setAudioAttributes(android.media.AudioAttributes.Builder().setUsage(android.media.AudioAttributes.USAGE_ASSISTANT).setContentType(android.media.AudioAttributes.CONTENT_TYPE_SPEECH).build()).setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(24000).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build()).setBufferSizeInBytes(8192).setTransferMode(AudioTrack.MODE_STREAM).build()
                speaker?.play()
            }
            speaker?.write(pcm, 0, pcm.size)
        }
    }

    private fun request(path: String, body: JSONObject, onSuccess: (JSONObject) -> Unit) {
        val request = Request.Builder().url(baseUrl() + path).post(body.toString().toRequestBody("application/json".toMediaType())).build()
        client.newCall(request).enqueue(callback(onSuccess))
    }

    private fun post(path: String, body: JSONObject) {
        val request = Request.Builder().url(baseUrl() + path).addHeader("Authorization", "Bearer $token").post(body.toString().toRequestBody("application/json".toMediaType())).build()
        client.newCall(request).enqueue(callback({}))
    }

    private fun get(path: String, onSuccess: (JSONObject) -> Unit) {
        val request = Request.Builder().url(baseUrl() + path).addHeader("Authorization", "Bearer $token").get().build()
        client.newCall(request).enqueue(callback(onSuccess))
    }

    private fun callback(onSuccess: (JSONObject) -> Unit) = object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            runOnUiThread {
                setStatus("ERROR")
                val message = networkError(e)
                showError(message)
                appendEvent("Connection failed: ${call.request().url} — $message")
            }
        }
        override fun onResponse(call: Call, response: Response) {
            response.use {
                val json = JSONObject(it.body?.string() ?: "{}")
                if (!it.isSuccessful) {
                    runOnUiThread {
                        setStatus(if (it.code == 401) "AUTHENTICATION_REQUIRED" else "ERROR")
                        val message = json.optString("error", "request rejected")
                        showError("Core returned HTTP ${it.code}: $message")
                        appendEvent("Core returned HTTP ${it.code}: $message")
                    }
                    return
                }
                try {
                    onSuccess(json)
                } catch (error: Exception) {
                    runOnUiThread {
                        setStatus("ERROR")
                        val message = error.message ?: "invalid response"
                        showError("Pairing response error: $message")
                        appendEvent("Pairing response error: $message")
                    }
                }
            }

        }
    }

    private fun networkError(error: IOException): String = when (error) {
        is SSLHandshakeException ->
            "Secure certificate rejected. Scan a fresh QR code or use the Android LAN URL."
        is SocketTimeoutException ->
            "JARVIS Core did not respond. Check that Mac and phone are on the same Wi-Fi."
        is ConnectException ->
            "Cannot reach JARVIS Core. Check 192.168.1.2 and that JARVIS is running."
        else -> "Connection failed: ${error.message ?: "unknown network error"}"
    }

    private fun showError(message: String) {
        if (!::errorText.isInitialized) return
        runOnUiThread {
            errorText.text = message
            errorText.visibility = if (message.isBlank()) View.GONE else View.VISIBLE
        }
    }

    private fun appendEvent(text: String) = runOnUiThread {
        events.append("\n$text")
    }

    private fun setStatus(value: String) = runOnUiThread { status.text = value }

    private fun saveCredentials() {
        securePrefs().edit()
            .putString("server", baseUrl()).putString("token", token)
            .putString("device_token", deviceToken).putString("device_id", deviceId).apply()
    }

    private fun securePrefs() = EncryptedSharedPreferences.create(
        this,
        "jarvis_secure",
        MasterKey.Builder(this).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("jarvis_events", "JARVIS Events", NotificationManager.IMPORTANCE_DEFAULT))
    }

    private fun notifyUser(title: String, message: String) {
        val manager = getSystemService(NotificationManager::class.java)
        val notification = android.app.Notification.Builder(this, "jarvis_events")
            .setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle(title).setContentText(message)
            .setAutoCancel(true).build()
        manager.notify(title.hashCode(), notification)
    }

    private fun requestNotificationPermission() {
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
        }
    }
}
