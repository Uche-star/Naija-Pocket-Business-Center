import os
import uuid
import datetime

from flask import Flask, request, jsonify, render_template_string


# ============================================================
# NAIJA POCKET BUSINESS CENTER
# PHONE TO ADA VOICE BRIDGE
# ============================================================

HOST = "0.0.0.0"
PORT = 8080

AUDIO_FOLDER = "phone_voice_records"

os.makedirs(AUDIO_FOLDER, exist_ok=True)

app = Flask(__name__)


# ============================================================
# PHONE VOICE PAGE
# ============================================================

PHONE_PAGE = """
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Ada Voice</title>

<style>

* {
    box-sizing: border-box;
}

body {
    background: #0B0B0B;
    color: white;
    font-family: Arial, sans-serif;
    text-align: center;
    padding: 25px;
    margin: 0;
}

.container {
    width: 100%;
    max-width: 500px;
    margin: auto;
}

h1 {
    margin-bottom: 8px;
}

.status {
    background: white;
    color: black;
    padding: 20px;
    border-radius: 10px;
    margin: 25px 0;
}

button {
    width: 100%;
    padding: 16px;
    margin: 8px 0;
    border: none;
    border-radius: 8px;
    font-size: 16px;
    font-weight: bold;
    cursor: pointer;
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

.again {
    background: #777777;
    color: white;
}

.send {
    background: #008000;
    color: white;
}

button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
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

<h1>Talk to Ada</h1>

<p>Naija Pocket Business Center</p>


<div class="status">

<div id="status">
Ready to record your message.
</div>

</div>


<button
    id="startButton"
    class="start"
    onclick="startRecording()"
>
Start Recording
</button>


<button
    id="stopButton"
    class="stop"
    onclick="stopRecording()"
    disabled
>
Stop Recording
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
Play Recording
</button>


<button
    id="againButton"
    class="again"
    onclick="recordAgain()"
    disabled
>
Record Again
</button>


<button
    id="sendButton"
    class="send"
    onclick="sendToAda()"
    disabled
>
Send Recording to Ada
</button>


<div id="result"></div>

</div>


<script>

let mediaRecorder = null;

let audioChunks = [];

let recordedBlob = null;

let recordedUrl = null;

let activeStream = null;


function stopMicrophone() {

    if (!activeStream) {
        return;
    }

    activeStream
        .getTracks()
        .forEach(function(track) {
            track.stop();
        });

    activeStream = null;
}


function clearRecording() {

    recordedBlob = null;

    audioChunks = [];

    const player =
        document.getElementById("audioPlayer");

    player.pause();

    player.removeAttribute("src");

    player.load();

    player.style.display = "none";


    if (recordedUrl) {

        URL.revokeObjectURL(recordedUrl);

        recordedUrl = null;
    }


    document.getElementById(
        "playButton"
    ).disabled = true;


    document.getElementById(
        "againButton"
    ).disabled = true;


    document.getElementById(
        "sendButton"
    ).disabled = true;
}


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


async function startRecording() {

    try {

        if (
            !navigator.mediaDevices ||
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
                new MediaRecorder(activeStream);
        }


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
                        {
                            type: finalType
                        }
                    );


                if (recordedUrl) {

                    URL.revokeObjectURL(
                        recordedUrl
                    );
                }


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
                    "againButton"
                ).disabled = false;


                document.getElementById(
                    "sendButton"
                ).disabled = false;


                document.getElementById(
                    "status"
                ).innerText =
                    "Recording ready. You can play it or send it to Ada.";


                stopMicrophone();
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
            "againButton"
        ).disabled = true;


        document.getElementById(
            "sendButton"
        ).disabled = true;


        document.getElementById(
            "result"
        ).innerText = "";


        document.getElementById(
            "status"
        ).innerText =
            "Recording. Speak normally to Ada.";


    } catch (error) {

        stopMicrophone();


        document.getElementById(
            "status"
        ).innerText =
            "Microphone access failed. Please allow microphone access and try again.";
    }
}


function stopRecording() {

    if (!mediaRecorder) {
        return;
    }


    if (mediaRecorder.state === "recording") {

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

    if (!recordedBlob) {
        return;
    }


    const player =
        document.getElementById(
            "audioPlayer"
        );


    player.play().catch(function() {

        document.getElementById(
            "status"
        ).innerText =
            "Tap the play control to hear your recording.";
    });
}


function recordAgain() {

    clearRecording();


    document.getElementById(
        "startButton"
    ).disabled = false;


    document.getElementById(
        "stopButton"
    ).disabled = true;


    document.getElementById(
        "result"
    ).innerText = "";


    document.getElementById(
        "status"
    ).innerText =
        "Ready to record again.";
}


async function sendToAda() {

    if (!recordedBlob) {
        return;
    }


    const sendButton =
        document.getElementById(
            "sendButton"
        );


    sendButton.disabled = true;


    document.getElementById(
        "status"
    ).innerText =
        "Sending your recording to Ada...";


    document.getElementById(
        "result"
    ).innerText = "";


    const formData =
        new FormData();


    let extension = "webm";


    if (
        recordedBlob.type &&
        recordedBlob.type.includes("mp4")
    ) {

        extension = "mp4";
    }


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


        let result = null;


        try {

            result =
                await response.json();

        } catch (jsonError) {

            result = {
                success: false,
                error:
                    "The server returned an invalid response."
            };
        }


        if (
            response.ok &&
            result &&
            result.success
        ) {

            document.getElementById(
                "status"
            ).innerText =
                "Your recording has been received by Ada.";


            document.getElementById(
                "result"
            ).innerText =
                "Voice message received successfully.";


            document.getElementById(
                "playButton"
            ).disabled = true;


            document.getElementById(
                "againButton"
            ).disabled = true;


        } else {

            document.getElementById(
                "status"
            ).innerText =
                "Ada could not receive the recording.";


            document.getElementById(
                "result"
            ).innerText =
                (result && result.error) ||
                "Please try again.";


            sendButton.disabled = false;
        }


    } catch (error) {

        document.getElementById(
            "status"
        ).innerText =
            "Ada could not receive the recording.";


        document.getElementById(
            "result"
        ).innerText =
            "Please check the connection and try again.";


        sendButton.disabled = false;
    }
}

</script>

</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return render_template_string(PHONE_PAGE)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "success": True,
        "service": "phone_bridge",
        "status": "online"
    })


# ============================================================
# AUDIO UPLOAD
# ============================================================

@app.route("/upload", methods=["POST"])
def upload_audio():

    try:

        if "audio" not in request.files:

            return jsonify({
                "success": False,
                "error": "No recording was received."
            }), 400


        audio_file = request.files["audio"]


        if not audio_file.filename:

            return jsonify({
                "success": False,
                "error": "The recording was empty."
            }), 400


        original_name = audio_file.filename.lower()


        if original_name.endswith(".mp4"):

            extension = "mp4"

        else:

            extension = "webm"


        timestamp = datetime.datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )


        unique_id = uuid.uuid4().hex[:8]


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


        if not os.path.exists(file_path):

            raise RuntimeError(
                "Recording could not be saved."
            )


        file_size = os.path.getsize(file_path)


        if file_size <= 0:

            os.remove(file_path)

            raise RuntimeError(
                "The received recording was empty."
            )


        print()
        print("=" * 60)
        print("ADA VOICE RECORDING RECEIVED")
        print("=" * 60)
        print("File:", file_path)
        print("Size:", file_size, "bytes")
        print("=" * 60)
        print()


        return jsonify({
            "success": True,
            "filename": filename,
            "message": "Voice recording received."
        })


    except Exception as error:

        print()
        print("=" * 60)
        print("ADA VOICE UPLOAD ERROR")
        print("=" * 60)
        print("Error:", repr(error))
        print("=" * 60)
        print()


        return jsonify({
            "success": False,
            "error": "Ada could not receive the recording."
        }), 500


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("NAIJA POCKET BUSINESS CENTER")
    print("PHONE TO ADA VOICE BRIDGE")
    print("=" * 60)
    print()

    print("Local bridge:")
    print("http://127.0.0.1:8080/")

    print()

    print("Direct network bridge:")
    print("http://<VPS-IP>:8080/")

    print()

    print("Bridge is waiting for phone connection...")
    print()


    app.run(
        host=HOST,
        port=PORT,
        debug=False
    ) 
