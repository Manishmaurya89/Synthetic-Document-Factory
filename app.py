import os
import asyncio
import sys
from pathlib import Path
from typing import List, Dict, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from main_pipeline import DocumentGenerator
from pipeline_architecture import DocumentType

app = FastAPI(title="Synthetic Document Factory API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure output directory exists
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Shared generator instance - Properly initialized from .env
generator = DocumentGenerator(
    ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
    primary_model=os.getenv("OLLAMA_MODEL", "llama3"),
    pexels_key=os.getenv("UNSPLASH_ACCESS_KEY"),  # We use Unsplash now
)


class GenerationRequest(BaseModel):
    topic: str
    pages: int = 1
    template: str = "article"
    languages: List[str] = ["eng"]
    mixing_ratio: Dict[str, int] = {"eng": 100}
    format: str = "pdf"
    include_tables: bool = True
    include_images: bool = True


@app.post("/api/generate")
async def generate(req: GenerationRequest):
    # Map template name to enum
    try:
        template_type = DocumentType(req.template)
    except ValueError:
        template_type = DocumentType.ARTICLE

    try:
        # Map boolean toggles to total counts
        total_tables = req.pages if req.include_tables else 0
        total_images = req.pages if req.include_images else 0

        result = await generator.generate(
            topic=req.topic,
            target_pages=req.pages,
            template_type=template_type,
            languages=req.languages,
            mixing_ratio=req.mixing_ratio,
            format_type=req.format,
            total_tables=total_tables,
            total_images=total_images,
            total_charts=0,
        )

        if result["success"]:
            filename = os.path.basename(result["output_file"])
            return {
                "success": True,
                "filename": filename,
                "file_url": f"/api/download/{filename}",
                "page_count": result["page_count"],
                "word_count": result["word_count"],
            }
        else:
            return {"success": False, "errors": result["errors"]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download/{filename}")
async def download(filename: str):
    file_path = OUTPUT_DIR / filename
    if file_path.exists():
        return FileResponse(file_path, filename=filename)
    raise HTTPException(status_code=404, detail="File not found")


@app.get("/api/preview/{filename}")
async def preview(filename: str):
    file_path = OUTPUT_DIR / filename
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="File not found")


# Static files for the frontend (MUST be last when mounting at /)
app.mount("/", StaticFiles(directory="web", html=True), name="web")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5001)
