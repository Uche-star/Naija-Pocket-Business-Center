"""
phone_bridge.py
Naija Pocket Business Center

ARCHITECTURE
------------
/                   -> Welcome Page
/conversation.html  -> Conversation Page
/workspace           -> Workspace
/voice               -> Voice / Phone Page
/api/chat            -> AdaController -> AdaAIEngine V8 -> Groq
/upload              -> Voice recording upload

The voice page is NOT removed.
It is simply moved from "/" to "/voice".
"""

import os
import uuid
import datetime

from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

from ada_controller import AdaController


# ==========================================================
# CONFIGURATION
# ==========================================================

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 10000))

AUDIO_FOLDER = "phone_voice_records"

os.makedirs(AUDIO_FOLDER, exist_ok=True)


# ==========================================================
# FLASK APPLICATION
# ==========================================================

app = Flask(__name__)
CORS(app)


# ==========================================================
# ADA CONTROLLER
# ==========================================================

print()
print("=" * 60)
print("NAIJA POCKET BUSINESS CENTER")
print("STARTING ADA CONNECTION")
print("=" * 60)
print()

try:
    ada_controller = AdaController()
    print("Ada Controller loaded successfully.")
except Exception as error:
    ada_controller = None

    print()
    print("=" * 60)
    print("ADA CONTROLLER STARTUP ERROR")
    print("=" * 60)
    print("Error:", repr(error))
    print("=" * 60)
    print()


# ==========================================================
# WELCOME PAGE
# ==========================================================

WELCOME_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width,
             initial-scale=1.0,
             viewport-fit=cover"
>

<title>Naija Pocket Business Center</title>

<style>

* {
    box-sizing: border-box;
}

html,
body {
    margin: 0;
    padding: 0;
    width: 100%;
    min-height: 100%;
}

body {
    font-family: Arial, sans-serif;
    background: #0B0B0B;
    color: white;
    overflow-x: hidden;
}

.page {
    width: 100%;
    min-height: 100vh;
    min-height: 100dvh;

    display: flex;
    flex-direction: column;
    align-items: center;

    padding:
        max(24px, env(safe-area-inset-top))
        16px
        calc(95px + env(safe-area-inset-bottom));

    text-align: center;
}

.content {
    width: 100%;
    max-width: 600px;
    margin: auto;
}

.brand-name {
    font-size: clamp(24px, 7vw, 36px);
    line-height: 1.12;
    font-weight: 700;
    margin: 0 0 12px;
}

.identity {
    font-size: clamp(15px, 4.5vw, 22px);
    line-height: 1.25;
    font-weight: 700;
    margin-bottom: 16px;
}

.value {
    font-size: clamp(13px, 3.9vw, 18px);
    line-height: 1.45;
    font-weight: 700;
    margin: 5px 0;
}

.services-title {
    color: #1E90FF;
    font-size: clamp(14px, 4vw, 19px);
    line-height: 1.3;
    font-weight: 700;
    margin: 22px 0 12px;
}

.services {
    font-size: clamp(13px, 3.7vw, 17px);
    line-height: 1.55;
    margin-bottom: 20px;
}

.services strong {
    font-weight: 700;
}

.start-button {
    display: flex;
    align-items: center;
    justify-content: center;

    width: 100%;
    max-width: 430px;
    min-height: 52px;

    margin: 0 auto;
    padding: 14px 18px;

    border: none;
    border-radius: 6px;

    background: #CC0000;
    color: white;

    font-size: clamp(13px, 3.8vw, 17px);
    line-height: 1.25;
    font-weight: 700;

    text-decoration: none;
    cursor: pointer;

    box-shadow:
        0 4px 12px rgba(0, 0, 0, 0.4);

    -webkit-tap-highlight-color: transparent;
}

.start-button:active {
    background: #990000;
    transform: scale(0.98);
}

.statement {
    font-size: clamp(12px, 3.5vw, 16px);
    line-height: 1.45;
    margin-top: 18px;
}

.signature {
    font-size: clamp(12px, 3.5vw, 16px);
    line-height: 1.45;
    font-weight: 700;
    margin-top: 12px;
}

.wave {
    position: fixed;
    left: 0;
    bottom: 0;

    width: 100%;
    height: 55px;

    pointer-events: none;
    z-index: 1;
}

.wave svg {
    display: block;
    width: 100%;
    height: 55px;
}

@media (max-height: 700px) {

    .page {
        padding-top: 18px;
        padding-bottom: 75px;
    }

    .brand-name {
        margin-bottom: 8px;
    }

    .identity {
        margin-bottom: 10px;
    }

    .services-title {
        margin-top: 16px;
        margin-bottom: 8px;
    }

    .services {
        line-height: 1.4;
        margin-bottom: 14px;
    }

    .statement {
        margin-top: 12px;
    }

    .signature {
        margin-top: 8px;
    }
}

@media (max-width: 360px) {

    .page {
        padding-left: 12px;
        padding-right: 12px;
    }

    .brand-name {
        font-size: 23px;
    }

    .services {
        line-height: 1.4;
    }

    .start-button {
        min-height: 48px;
        padding: 12px 14px;
    }
}

</style>
</head>

<body>

<main class="page">

<section class="content">

<h1 class="brand-name">
    NAIJA POCKET<br>
    BUSINESS CENTER
</h1>

<div class="identity">
    THE DOCUMENT PEOPLE
</div>

<div class="value">
    Fast • Convenient • Open 24/7
</div>

<div class="value">
    Prepare Your Documents In Minutes
</div>

<div class="value">
    Do Everything Concerning Your Documents<br>
    From Your Phone, Wherever You Are In Nigeria
</div>

<div class="services-title">
    ENJOY THE BEST IN DOCUMENT PROCESSING
</div>

<div class="services">

<strong>SERVICES</strong>

<br><br>

✓ CVs &amp; Résumés<br>
✓ Handwritten Note Typing<br>
✓ Assignments &amp; Projects<br>
✓ Business Documents<br>
✓ Letters &amp; Proposals<br>
✓ Editing &amp; Formatting<br>
✓ PDF Services<br>
✓ Document Conversion<br>
✓ Translation<br>
✓ Printing Preparation<br>
✓ And Much More

</div>

<a
    class="start-button"
    href="/conversation.html"
>
    TAP HERE TO START YOUR REQUEST
</a>

<div class="statement">
    Get your documents, business services and<br>
    everyday office work done from your phone.
</div>

<div class="signature">
    Naija Pocket Business Center<br>
    Your Business Center In Your Pocket
</div>

</section>

</main>

<div class="wave">

<svg
    viewBox="0 0 800 70"
    preserveAspectRatio="none"
    xmlns="http://www.w3.org/2000/svg"
>

<path
    d="M0 53 Q200 17 400 53 T800 53"
    fill="none"
    stroke="#1E90FF"
    stroke-width="8"
/>

<path
    d="M0 48 Q200 12 400 48 T800 48"
    fill="none"
    stroke="white"
    stroke-width="3"
/>

<path
    d="M0 62 Q200 38 400 62 T800 62"
    fill="none"
    stroke="#1E90FF"
    stroke-width="3"
/>

</svg>

</div>

</body>
</html>
"""


# ==========================================================
# VOICE / PHONE PAGE
# ==========================================================

PHONE_PAGE = """
<!DOCTYPE html>
<html>

<head>

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Ada Voice</title>

<style>

body {
    background: #0B0B0B;
    color: white;
    font-family: Arial, sans-serif;
    text-align: center;
    padding: 25px;
}

.container {
    max-width: 500px;
    margin: auto;
}

h1 {
    margin-bottom: 8px;
}

.subtitle {
    margin-bottom: 20px;
}

.status {
    background: white;
    color: black;
    padding: 20px;
    border-radius: 10px;
    margin: 25px 0;
    font-size: 16px;
}

button {
    width: 100%;
    padding: 16px;
    margin: 8px 0;
    border: none;
    border-radius: 8px;
    font-size: 16px;
    font-weight: bold;
}

.start {
    background: #008000;
    color: white;
}

.stop {
    background: #990000;
    color: white;
}

.play {
    background: #555555;
    color: white;
}

.record-again {
    background: #777777;
    color: white;
}

.send {
    background: #008000;
    color: white;
}

button:disabled {
    opacity: 0.45;
}

audio {
    width: 100%;
    margin-top: 15px;
    display: none;
}

#result {
    margin-top: 20px;
    word-break: break-word;
}

.back {
    display: block;
    margin-top: 20px;
    color: white;
    text-decoration: none;
}

</style>

</head>

<body>

<div class="container">

<h1>🎤 Talk to Ada</h1>

<p class="subtitle">
Naija Pocket Business Center
</p>

<div class="status">

<div id="status">
Ready to record your message for Ada.
</div>

</div>

<button
    id="startButton"
    class="start"
    onclick="startRecording()"
>
🎙 Start Recording
</button>

<button
    id="stopButton"
    class="stop"
    onclick="stopRecording()"
    disabled
>
⏹ Stop Recording
</button>

<audio
    id="audioPlayer"
    controls
></audio>

<button
    id="playButton"
    class="play"
    onclick="playRecording()"
    disabled
>
▶ Play Recording
</button>

<button
    id="recordAgainButton"
    class="record-again"
    onclick="recordAgain()"
    disabled
>
🔄 Record Again
</button>

<button
    id="sendButton"
    class="send"
    onclick="sendToAda()"
    disabled
>
🎤 Send Recording to Ada
</button>

<div id="result"></div>

<a
    class="back"
    href="/"
>
← Back to Welcome Page
</a>

</div>


<script>

let mediaRecorder = null;
let audioChunks = [];
let recordedBlob = null;
let recordedUrl = null;
let activeStream = null;


function getSupportedMimeType() {

    const types = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4"
    ];

    for (const type of types) {

        if (
            typeof MediaRecorder !== "undefined" &&
            MediaRecorder.isTypeSupported(type)
        ) {
            return type;
        }

    }

    return "";

}


function clearRecording() {

    recordedBlob = null;
    audioChunks = [];

    const player =
        document.getElementById("audioPlayer");

    player.pause();
    player.removeAttribute("src");
    player.style.display = "none";

    if (recordedUrl) {

        URL.revokeObjectURL(recordedUrl);
        recordedUrl = null;

    }

    document.getElementById("playButton").disabled = true;
    document.getElementById("recordAgainButton").disabled = true;
    document.getElementById("sendButton").disabled = true;

}


async function startRecording() {

    try {

        if (
            !navigator.mediaDevices ||
            !navigator.mediaDevices.getUserMedia
        ) {

            document.getElementById("status").innerText =
                "Your browser does not support microphone recording.";

            return;

        }

        clearRecording();

        activeStream =
            await navigator.mediaDevices.getUserMedia({
                audio: true
            });

        const mimeType =
            getSupportedMimeType();

        mediaRecorder =
            mimeType
            ? new MediaRecorder(
                activeStream,
                { mimeType: mimeType }
            )
            : new MediaRecorder(activeStream);

        audioChunks = [];

        mediaRecorder.ondataavailable =
            function(event) {

                if (
                    event.data &&
                    event.data.size > 0
                ) {

                    audioChunks.push(event.data);

                }

            };


        mediaRecorder.onstop =
            function() {

                const finalType =
                    mediaRecorder.mimeType ||
                    "audio/webm";

                recordedBlob =
                    new Blob(
                        audioChunks,
                        { type: finalType }
                    );

                recordedUrl =
                    URL.createObjectURL(
                        recordedBlob
                    );

                const player =
                    document.getElementById(
                        "audioPlayer"
                    );

                player.src = recordedUrl;
                player.style.display = "block";

                document.getElementById(
                    "playButton"
                ).disabled = false;

                document.getElementById(
                    "recordAgainButton"
                ).disabled = false;

                document.getElementById(
                    "sendButton"
                ).disabled = false;

                document.getElementById(
                    "status"
                ).innerText =
                    "✅ Recording ready. You can play it or send it to Ada.";

                if (activeStream) {

                    activeStream
                        .getTracks()
                        .forEach(
                            track => track.stop()
                        );

                    activeStream = null;

                }

            };


        mediaRecorder.start();

        document.getElementById(
            "startButton"
        ).disabled = true;

        document.getElementById(
            "stopButton"
        ).disabled = false;

        document.getElementById(
            "status"
        ).innerText =
            "🎙 Recording... Speak normally to Ada.";

    }

    catch (error) {

        document.getElementById(
            "status"
        ).innerText =
            "Microphone error: " +
            error.message;

        if (activeStream) {

            activeStream
                .getTracks()
                .forEach(
                    track => track.stop()
                );

            activeStream = null;

        }

    }

}


function stopRecording() {

    if (!mediaRecorder) {
        return;
    }

    if (
        mediaRecorder.state === "recording"
    ) {
        mediaRecorder.stop();
    }

    document.getElementById(
        "startButton"
    ).disabled = false;

    document.getElementById(
        "stopButton"
    ).disabled = true;

}


function playRecording() {

    const player =
        document.getElementById(
            "audioPlayer"
        );

    if (!recordedBlob) {
        return;
    }

    player.play();

}


function recordAgain() {

    clearRecording();

    document.getElementById(
        "status"
    ).innerText =
        "Ready to record again.";

    document.getElementById(
        "startButton"
    ).disabled = false;

    document.getElementById(
        "stopButton"
    ).disabled = true;

    document.getElementById(
        "result"
    ).innerText = "";

}


async function sendToAda() {

    if (!recordedBlob) {
        return;
    }

    document.getElementById(
        "sendButton"
    ).disabled = true;

    document.getElementById(
        "status"
    ).innerText =
        "🎤 Sending your recording to Ada...";

    const formData =
        new FormData();

    const extension =
        recordedBlob.type.includes("mp4")
        ? "mp4"
        : "webm";

    formData.append(
        "audio",
        recordedBlob,
        "ada_voice_message." + extension
    );

    try {

        const response =
            await fetch(
                "/upload",
                {
                    method: "POST",
                    body: formData
                }
            );

        if (!response.ok) {

            throw new Error(
                "Server returned HTTP " +
                response.status
            );

        }

        const result =
            await response.json();

        if (result.success) {

            document.getElementById(
                "status"
            ).innerText =
                "✅ Ada has received your recording.";

            document.getElementById(
                "result"
            ).innerText =
                "Your voice message has been received.";

        }

        else {

            document.getElementById(
                "status"
            ).innerText =
                "❌ Ada could not receive the recording.";

            document.getElementById(
                "result"
            ).innerText =
                result.error ||
                "Unknown upload error.";

            document.getElementById(
                "sendButton"
            ).disabled = false;

        }

    }

    catch (error) {

        document.getElementById(
            "status"
        ).innerText =
            "❌ Connection error.";

        document.getElementById(
            "result"
        ).innerText =
            error.message;

        document.getElementById(
            "sendButton"
        ).disabled = false;

    }

}

</script>

</body>
</html>
"""


# ==========================================================
# WELCOME PAGE — FRONT DOOR
# ==========================================================

@app.route("/", methods=["GET"])
def home():
    return render_template_string(WELCOME_PAGE)


# ==========================================================
# VOICE PAGE
# ==========================================================

@app.route("/voice", methods=["GET"])
def voice_page():
    return render_template_string(PHONE_PAGE)


# ==========================================================
# HEALTH CHECK
# ==========================================================

@app.route("/health", methods=["GET"])
def health():

    return jsonify(
        {
            "success": True,
            "service": "naija_pocket_business_center",
            "status": "online",
            "ada_controller_loaded":
                ada_controller is not None
        }
    )


# ==========================================================
# ADA CHAT API
# ==========================================================

@app.route("/api/chat", methods=["POST"])
def api_chat():

    print()
    print("=" * 60)
    print("ADA CHAT REQUEST RECEIVED")
    print("=" * 60)

    if ada_controller is None:

        return jsonify(
            {
                "success": False,
                "error":
                    "Ada Controller is not available."
            }
        ), 500


    data = request.get_json(silent=True)

    print("Request JSON:", data)

    if not isinstance(data, dict):

        return jsonify(
            {
                "success": False,
                "error":
                    "No valid message data received."
            }
        ), 400


    message = (
        data.get("message")
        or data.get("text")
        or data.get("content")
    )

    if message is None:

        return jsonify(
            {
                "success": False,
                "error":
                    "Message field is missing."
            }
        ), 400


    message = str(message).strip()

    if not message:

        return jsonify(
            {
                "success": False,
                "error":
                    "Message cannot be empty."
            }
        ), 400


    service = (
        data.get("service")
        or data.get("selected_service")
        or data.get("selectedService")
    )

    if service:
        service = str(service).strip()
    else:
        service = None


    print("Website Service Context:", service)
    print("Customer Message:", message)


    try:

        reply = ada_controller.process_message(
            message,
            service=service
        )

    except Exception as error:

        print()
        print("=" * 60)
        print("ADA CHAT ERROR")
        print("=" * 60)
        print("Exception:", repr(error))
        print("=" * 60)

        return jsonify(
            {
                "success": False,
                "error":
                    "Ada encountered a temporary processing error.",
                "details":
                    str(error)
            }
        ), 500


    if reply is None:

        return jsonify(
            {
                "success": False,
                "error":
                    "Ada did not return a response."
            }
        ), 500


    reply = str(reply).strip()

    if not reply:

        return jsonify(
            {
                "success": False,
                "error":
                    "Ada returned an empty response."
            }
        ), 500


    print()
    print("=" * 60)
    print("ADA RESPONSE SUCCESSFUL")
    print("=" * 60)
    print("Reply:", reply)
    print("=" * 60)
    print()


    return jsonify(
        {
            "success": True,
            "reply": reply,
            "message": reply
        }
    ), 200


# ==========================================================
# RECEIVE PHONE AUDIO
# ==========================================================

@app.route("/upload", methods=["POST"])
def upload_audio():

    if "audio" not in request.files:

        return jsonify(
            {
                "success": False,
                "error":
                    "No audio file received."
            }
        ), 400


    audio_file = request.files["audio"]

    if not audio_file.filename:

        return jsonify(
            {
                "success": False,
                "error":
                    "Empty audio file."
            }
        ), 400


    timestamp = datetime.datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    unique_id = uuid.uuid4().hex[:8]

    original_name = (
        audio_file.filename.lower()
    )

    extension = (
        "mp4"
        if original_name.endswith(".mp4")
        else "webm"
    )

    filename = (
        "ada_voice_"
        + timestamp
        + "_"
        + unique_id
        + "."
        + extension
    )

    file_path = os.path.join(
        AUDIO_FOLDER,
        filename
    )

    audio_file.save(file_path)

    file_size = os.path.getsize(file_path)

    print()
    print("=" * 60)
    print("ADA VOICE RECORDING RECEIVED")
    print("=" * 60)
    print("File:", file_path)
    print("Size:", file_size, "bytes")
    print("=" * 60)
    print()


    return jsonify(
        {
            "success": True,
            "filename": filename,
            "message":
                "Voice recording received for Ada."
        }
    )


# ==========================================================
# START SERVER
# ==========================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("NAIJA POCKET BUSINESS CENTER")
    print("ADA WEBSITE / PHONE BRIDGE")
    print("=" * 60)
    print()

    print(
        "Ada Controller:",
        "READY"
        if ada_controller is not None
        else "NOT AVAILABLE"
    )

    print()

    print("Available routes:")
    print("GET  /")
    print("GET  /voice")
    print("GET  /health")
    print("POST /api/chat")
    print("POST /upload")

    print()
    print(
        "Bridge is waiting for website connection..."
    )
    print()

    app.run(
        host=HOST,
        port=PORT,
        debug=False
    ) 
