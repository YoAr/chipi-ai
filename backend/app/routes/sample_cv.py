"""Sam's sample CV for first runs and demos (fixtures/sample-cv-sam.md).

GET  /api/sample-cv      the CV as Markdown, for the profile screen to show
POST /api/sample-cv/use  load it as if the user had uploaded it (same steps as POST /api/cv)

Works in both modes. In real mode the UI then starts POST /api/profile/build, which
reads the CV text from the `cv_text_path` setting, exactly as after a real upload.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException

from ..config import Settings
from ..deps import get_settings, get_store
from ..storage.wiki import save_raw

router = APIRouter()

SAMPLE_NAME = "sample-cv-sam.md"


def _sample_text(settings: Settings) -> str:
    path = settings.fixtures_dir / SAMPLE_NAME
    if not path.is_file():
        raise HTTPException(404, "The sample CV isn't available on this server")
    return path.read_text(encoding="utf-8")


@router.get("/api/sample-cv")
def sample_cv(settings: Settings = Depends(get_settings)) -> dict:
    return {"filename": SAMPLE_NAME, "markdown": _sample_text(settings)}


@router.get("/api/sample-cv/pdf")
def sample_cv_pdf(settings: Settings = Depends(get_settings)) -> FileResponse:
    """The same CV as a PDF (built by scripts/build_sample_cv_pdf.py), to download and upload in demos."""
    path = settings.fixtures_dir / "sample-cv-sam.pdf"
    if not path.is_file():
        raise HTTPException(404, "The sample CV PDF isn't available on this server")
    return FileResponse(path, media_type="application/pdf", filename="Sam-Verhoeven-CV-sample.pdf")


@router.post("/api/sample-cv/use")
def use_sample_cv(store=Depends(get_store), settings: Settings = Depends(get_settings)) -> dict:
    text = _sample_text(settings)
    if settings.mock_mode:
        return store.add_cv(SAMPLE_NAME)
    persona = store.add_cv(SAMPLE_NAME, text.encode("utf-8"))
    text_path = save_raw(settings.data_dir, f"{SAMPLE_NAME}.txt", text.encode("utf-8"))  # text for the model
    store.set_setting("cv_text_path", text_path)  # the profile pipeline reads the latest CV from here
    return persona
