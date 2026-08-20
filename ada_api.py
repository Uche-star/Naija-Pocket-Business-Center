from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

from ada_controller import AdaController


app = FastAPI(
    title="Ada Intelligence API - Naija Pocket"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


BASE_DIR = Path(__file__).resolve().parent


class ChatRequest(BaseModel):
    message: str
    service: str | None = None


@app.get("/")
def home():
    return FileResponse(
        BASE_DIR / "index.html"
    )


@app.get("/conversation.html")
def conversation():
    return FileResponse(
        BASE_DIR / "conversation.html"
    )


@app.get("/workspace.html")
def workspace():
    return FileResponse(
        BASE_DIR / "workspace.html"
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Ada FastAPI"
    }


@app.post("/api/chat")
def chat(req: ChatRequest):

    print()
    print("=" * 60)
    print("FASTAPI → ADA CONTROLLER")
    print("=" * 60)
    print("Service:", req.service)
    print("Message:", req.message)
    print("=" * 60)

    try:

        controller = AdaController()

        reply = controller.process_message(
            message=req.message,
            service=req.service
        )

        return {
            "success": True,
            "reply": str(reply)
        }

    except Exception as error:

        import traceback

        print()
        print("=" * 60)
        print("FASTAPI → ADA ERROR")
        print("=" * 60)

        traceback.print_exc()

        print("=" * 60)

        return {
            "success": False,
            "reply": (
                "No wahala. "
                "Ada encountered a temporary problem. "
                "Please try again."
            ),
            "error": str(error)
        }
