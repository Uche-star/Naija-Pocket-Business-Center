from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from ada_controller import AdaController


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
# REQUEST MODEL
# ============================================================

class ChatRequest(BaseModel):
    message: str
    service: str
    event: Optional[str] = None
    activate_intelligence: Optional[bool] = True


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
# ADA CHAT ENDPOINT
# ============================================================

@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        controller = AdaController()

        result = controller.process_message(
            message=req.message,
            service=req.service
        )

        if isinstance(result, dict):
            return {
                "success": result.get("success", True),
                "reply": result.get(
                    "reply",
                    result.get("response", str(result))
                ),
                "price": result.get("price", None)
            }

        if isinstance(result, str):
            return {
                "success": True,
                "reply": result,
                "price": None
            }

        return {
            "success": True,
            "reply": str(result),
            "price": None
        }

    except Exception as e:
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
