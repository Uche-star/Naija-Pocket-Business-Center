
"""
phone_bridge.py

Naija Pocket Business Center
Website / Phone → Ada Bridge

ARCHITECTURE
------------
Customer Website
       ↓
Cloudflare Worker
       ↓
Flask /api/chat
       ↓
AdaController
       ↓
AdaAIEngine V8
       ↓
Groq
       ↓
Ada response
       ↓
Website

This file also preserves the existing phone voice
recording bridge.

IMPORTANT
---------
• AdaController remains the controller.
• AdaAIEngine V8 remains the primary intelligence.
• Groq remains the AI provider.
• This file does NOT perform OCR.
• This file does NOT use NumPy.
• This file does NOT use OpenCV.
• This file does NOT use sounddevice.
• This file does NOT replace Ada's intelligence.
"""

import os
import uuid
import datetime

from flask import (
    Flask,
    request,
    jsonify,
    render_template_string
)

from flask_cors import CORS

from ada_controller import AdaController


# ==========================================================
# CONFIGURATION
# ==========================================================

HOST = "0.0.0.0"
PORT = 8080

AUDIO_FOLDER = "phone_voice_records"

os.makedirs(
    AUDIO_FOLDER,
    exist_ok=True
)


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

    print(
        "Ada Controller loaded successfully."
    )

except Exception as error:

    ada_controller = None

    print()
    print("=" * 60)
    print("ADA CONTROLLER STARTUP ERROR")
    print("=" * 60)

    print(
        "Error:",
        repr(error)
    )

    print("=" * 60)
    print()


# ==========================================================
# CUSTOMER PHONE PAGE
# ==========================================================

PHONE_PAGE = """
<!DOCTYPE html>

<html>

<head>

<meta
    name="viewport"
    content="width=device-width,
             initial-scale=1.0"
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

</div>


<script>

let mediaRecorder = null;
let audioChunks = [];
let recordedBlob = null;
let recordedUrl = null;
let activeStream = null;


// ==========================================================
// FIND SUPPORTED AUDIO FORMAT
// ==========================================================

function getSupportedMimeType() {

    const types = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4"
    ];

    for (const type of types) {

        if (
            typeof MediaRecorder !== "undefined"
            &&
            MediaRecorder.isTypeSupported(type)
        ) {

            return type;

        }

    }

    return "";
}


// ==========================================================
// RESET RECORDED AUDIO
// ==========================================================

function clearRecording() {

    recordedBlob = null;
    audioChunks = [];

    const player =
        document.getElementById(
            "audioPlayer"
        );

    player.pause();

    player.removeAttribute(
        "src"
    );

    player.style.display =
        "none";

    if (recordedUrl) {

        URL.revokeObjectURL(
            recordedUrl
        );

        recordedUrl = null;
    }

    document.getElementById(
        "playButton"
    ).disabled = true;

    document.getElementById(
        "recordAgainButton"
    ).disabled = true;

    document.getElementById(
        "sendButton"
    ).disabled = true;
}


// ==========================================================
// START RECORDING
// ==========================================================

async function startRecording() {

    try {

        if (
            !navigator.mediaDevices
            ||
            !navigator.mediaDevices.getUserMedia
        ) {

            document.getElementById(
                "status"
            ).innerText =
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

        if (mimeType) {

            mediaRecorder =
                new MediaRecorder(
                    activeStream,
                    {
                        mimeType: mimeType
                    }
                );

        } else {

            mediaRecorder =
                new MediaRecorder(
                    activeStream
                );
        }

        audioChunks = [];

        mediaRecorder.ondataavailable =
            function(event) {

                if (
                    event.data
                    &&
                    event.data.size > 0
                ) {

                    audioChunks.push(
                        event.data
                    );
                }
            };


        mediaRecorder.onstop =
            function() {

                const finalType =
                    mediaRecorder.mimeType
                    ||
                    "audio/webm";

                recordedBlob =
                    new Blob(
                        audioChunks,
                        {
                            type: finalType
                        }
                    );

                recordedUrl =
                    URL.createObjectURL(
                        recordedBlob
                    );

                const player =
                    document.getElementById(
                        "audioPlayer"
                    );

                player.src =
                    recordedUrl;

                player.style.display =
                    "block";

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
                            track =>
                                track.stop()
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
            "playButton"
        ).disabled = true;

        document.getElementById(
            "recordAgainButton"
        ).disabled = true;

        document.getElementById(
            "sendButton"
        ).disabled = true;

        document.getElementById(
            "status"
        ).innerText =
            "🎙 Recording... Speak normally to Ada.";


    } catch (error) {

        document.getElementById(
            "status"
        ).innerText =
            "Microphone error: "
            +
            error.message;

        if (activeStream) {

            activeStream
                .getTracks()
                .forEach(
                    track =>
                        track.stop()
                );

            activeStream = null;
        }
    }
}


// ==========================================================
// STOP RECORDING
// ==========================================================

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


// ==========================================================
// PLAY RECORDING
// ==========================================================

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


// ==========================================================
// RECORD AGAIN
// ==========================================================

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


// ==========================================================
// SEND RECORDING TO ADA
// ==========================================================

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

    const filename =
        "ada_voice_message."
        +
        extension;

    formData.append(
        "audio",
        recordedBlob,
        filename
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
                "Server returned HTTP "
                +
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

            document.getElementById(
                "playButton"
            ).disabled = true;

            document.getElementById(
                "recordAgainButton"
            ).disabled = true;

        } else {

            document.getElementById(
                "status"
            ).innerText =
                "❌ Ada could not receive the recording.";

            document.getElementById(
                "result"
            ).innerText =
                result.error
                ||
                "Unknown upload error.";

            document.getElementById(
                "sendButton"
            ).disabled = false;
        }


    } catch (error) {

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
# HOME
# ==========================================================

@app.route("/")
def home():

    return render_template_string(
        PHONE_PAGE
    )


# ==========================================================
# HEALTH CHECK
# ==========================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify(
        {
            "success": True,
            "service": "phone_bridge",
            "status": "online",
            "ada_controller_loaded":
                ada_controller is not None
        }
    )


# ==========================================================
# ADA CHAT API
# ==========================================================

@app.route(
    "/api/chat",
    methods=["POST"]
)
def api_chat():

    print()
    print("=" * 60)
    print("ADA CHAT REQUEST RECEIVED")
    print("=" * 60)


    # ------------------------------------------------------
    # CHECK CONTROLLER
    # ------------------------------------------------------

    if ada_controller is None:

        print(
            "ERROR: AdaController is not loaded."
        )

        return jsonify(
            {
                "success": False,
                "error":
                    "Ada Controller is not available."
            }
        ), 500


    # ------------------------------------------------------
    # READ JSON
    # ------------------------------------------------------

    data = request.get_json(
        silent=True
    )

    print(
        "Request JSON:",
        data
    )


    if not isinstance(data, dict):

        print(
            "ERROR: No valid JSON object received."
        )

        return jsonify(
            {
                "success": False,
                "error":
                    "No valid message data received."
            }
        ), 400


    # ------------------------------------------------------
    # ACCEPT COMMON MESSAGE FIELD NAMES
    # ------------------------------------------------------

    message = (
        data.get("message")
        or data.get("text")
        or data.get("content")
    )


    if message is None:

        print(
            "ERROR: Message field missing."
        )

        return jsonify(
            {
                "success": False,
                "error":
                    "Message field is missing."
            }
        ), 400


    message = str(
        message
    ).strip()


    if not message:

        print(
            "ERROR: Empty message."
        )

        return jsonify(
            {
                "success": False,
                "error":
                    "Message cannot be empty."
            }
        ), 400


    # ------------------------------------------------------
    # SERVICE CONTEXT
    # ------------------------------------------------------

    service = (
        data.get("service")
        or data.get("selected_service")
        or data.get("selectedService")
    )


    if service:

        service = str(
            service
        ).strip()

    else:

        service = None


    print(
        "Website Service Context:",
        service
    )

    print(
        "Customer Message:",
        message
    )

    print("=" * 60)


    # ------------------------------------------------------
    # SEND TO ADA CONTROLLER
    # ------------------------------------------------------

    try:

        reply = (
            ada_controller
            .process_message(
                message,
                service=service
            )
        )


    except Exception as error:

        print()
        print("=" * 60)
        print("ADA CHAT ERROR")
        print("=" * 60)

        print(
            "Exception:",
            repr(error)
        )

        print("=" * 60)
        print()

        return jsonify(
            {
                "success": False,
                "error":
                    "Ada encountered a temporary processing error.",
                "details":
                    str(error)
            }
        ), 500


    # ------------------------------------------------------
    # VALIDATE ADA RESPONSE
    # ------------------------------------------------------

    if reply is None:

        print(
            "ERROR: Ada returned None."
        )

        return jsonify(
            {
                "success": False,
                "error":
                    "Ada did not return a response."
            }
        ), 500


    reply = str(
        reply
    ).strip()


    if not reply:

        print(
            "ERROR: Ada returned an empty response."
        )

        return jsonify(
            {
                "success": False,
                "error":
                    "Ada returned an empty response."
            }
        ), 500


    # ------------------------------------------------------
    # SUCCESS
    # ------------------------------------------------------

    print()
    print("=" * 60)
    print("ADA RESPONSE SUCCESSFUL")
    print("=" * 60)

    print(
        "Reply:",
        reply
    )

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

@app.route(
    "/upload",
    methods=["POST"]
)
def upload_audio():

    if "audio" not in request.files:

        return jsonify(
            {
                "success": False,
                "error":
                    "No audio file received."
            }
        ), 400


    audio_file = request.files[
        "audio"
    ]


    if not audio_file.filename:

        return jsonify(
            {
                "success": False,
                "error":
                    "Empty audio file."
            }
        ), 400


    timestamp = (
        datetime.datetime.now()
        .strftime(
            "%Y%m%d_%H%M%S"
        )
    )


    unique_id = (
        uuid.uuid4()
        .hex[:8]
    )


    original_name = (
        audio_file.filename
        .lower()
    )


    if original_name.endswith(
        ".mp4"
    ):

        extension = "mp4"

    else:

        extension = "webm"


    filename = (
        "ada_voice_"
        +
        timestamp
        +
        "_"
        +
        unique_id
        +
        "."
        +
        extension
    )


    file_path = os.path.join(
        AUDIO_FOLDER,
        filename
    )


    audio_file.save(
        file_path
    )


    file_size = os.path.getsize(
        file_path
    )


    print()
    print("=" * 60)
    print("ADA VOICE RECORDING RECEIVED")
    print("=" * 60)

    print(
        "File:",
        file_path
    )

    print(
        "Size:",
        file_size,
        "bytes"
    )

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
        "Local bridge address:"
    )

    print(
        f"http://127.0.0.1:{PORT}"
    )

    print()

    print(
        "Network bridge address:"
    )

    print(
        f"http://0.0.0.0:{PORT}"
    )

    print()

    print(
        "Ada Controller:",
        "READY"
        if ada_controller is not None
        else "NOT AVAILABLE"
    )

    print()

    print(
        "Available API:"
    )

    print(
        "POST /api/chat"
    )

    print()

    print(
        "Bridge is waiting for website connection..."
    )

    print()

    app.run(
        host=HOST,
        port=PORT,
        debug=True
    ) 

