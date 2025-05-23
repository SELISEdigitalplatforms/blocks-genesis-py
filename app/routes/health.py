from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/health")
def health_check(request: Request):
    return {
        "status": "healthy",
        "trace_id": getattr(request.state, "trace_id", None)
    }
