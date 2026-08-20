from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from ada_controller import AdaController


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Ada Intelligence API - Naija Pocket"
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# FILE LOCATIONS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent


# ============================================================
# REQUEST MODEL
# ============================================================

class ChatRequest(BaseModel):
    message: str
    service: str
    event: Optional[str] = None
    activate_intelligence: Optional[bool] = True


# ============================================================
# FRONTEND
# ============================================================

@app.get("/")
def home():
    return FileResponse(
        BASE_DIR / "index.html"
    )


@app.get("/workspace.html")
def workspace():
    return FileResponse(
        BASE_DIR / "workspace.html"
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Ada FastAPI"
    }


# ============================================================
# ADA CHAT
# ============================================================

@app.post("/api/chat")
def chat(req: ChatRequest):

    try:

        # Create the existing Ada controller.
        # We are NOT changing AdaController.

        controller = AdaController()

        # Existing confirmed signature:
        # AdaController.process_message(message, service)

        result = controller.process_message(
            message=req.message,
            service=req.service
        )

        # ====================================================
        # RESULT IS A DICTIONARY
        # ====================================================

        if isinstance(result, dict):

            return {
                "success": result.get(
                    "success",
                    True
                ),

                "reply": result.get(
                    "reply",
                    result.get(
                        "response",
                        str(result)
                    )
                ),

                "price": result.get(
                    "price",
                    None
                )
            }

        # ====================================================
        # RESULT IS A STRING
        # ====================================================

        if isinstance(result, str):

            return {
                "success": True,
                "reply": result,
                "price": None
            }

        # ====================================================
        # OTHER RESULT TYPE
        # ====================================================

        return {
            "success": True,
            "reply": str(result),
            "price": None
        }

    except Exception as e:

        # Print complete error in Render logs.

        import traceback

        traceback.print_exc()

        return {
            "success": False,
            "error": str(e),
            "reply": (
                "Ada encountered an error. "
                "Please try again."
            )
        }
