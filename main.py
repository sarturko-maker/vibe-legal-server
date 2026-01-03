import os
import uuid
import logging
import re
import shutil
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from fastapi import FastAPI, File, UploadFile, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from models import Job, JobStatus, Playbook, PlaybookRule
from job_processor import process_job

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("vibelegal")

app = FastAPI(title="VibeLegal Python Server")

# 2. CORS wide open
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Job storage is in-memory
jobs: Dict[str, Job] = {}

# Default Playbooks
DEFAULT_PLAYBOOKS = [
    {
        "id": "nda-standard",
        "name": "Standard NDA Review",
        "description": "Standard review for Non-Disclosure Agreements focusing on balanced terms.",
        "playbookText": "Review the NDA for mutual protection.",
        "rules": [
            {"condition": "indemnity is uncapped", "action": "cap at contract value"},
            {"condition": "governing law != England", "action": "flag for review"}
        ]
    },
    {
        "id": "supply-aggressive",
        "name": "Supply Agreement (Aggressive)",
        "description": "Aggressive review for Supply Agreements favoring the buyer.",
        "playbookText": "Review the Supply Agreement with a focus on buyer leverage.",
        "rules": [
            {"condition": "payment terms < 60 days", "action": "change to 90 days"},
            {"condition": "exclusivity is missing", "action": "require exclusivity"}
        ]
    }
]

# Initialize playbooks
playbooks: Dict[str, Playbook] = {}
for pb_data in DEFAULT_PLAYBOOKS:
    pb = Playbook(**pb_data)
    playbooks[pb.id] = pb

# Directories
UPLOAD_DIR = os.path.join(os.getcwd(), "Uploads")
PROCESSED_DIR = os.path.join(os.getcwd(), "ProcessedDocs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)

# Models for Request Bodies where needed (rest use Form/File)
class DownloadBatchRequest(BaseModel):
    jobIds: List[str]

class LegacyAmendRequest(BaseModel):
    ooxml: str
    originalText: str
    modifiedText: str
    author: str

# API Endpoints

@app.get("/")
async def root():
    return "VibeLegalServer is running. API endpoints available."

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

@app.post("/api/process")
async def process_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    playbookId: str = Form(...),
    aiProvider: str = Form(...),
    model: str = Form(...),
    apiKey: Optional[str] = Form(None)
):
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")
    
    # Generate Job ID
    job_id = str(uuid.uuid4())
    
    # Save file
    file_ext = os.path.splitext(file.filename)[1]
    save_path = os.path.join(UPLOAD_DIR, f"{job_id}{file_ext}")
    
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Create Job
    job = Job(
        id=job_id,
        status=JobStatus.QUEUED,
        original_file_path=save_path,
        original_filename=file.filename,
        playbook_id=playbookId,
        ai_provider=aiProvider,
        ai_model=model,
        api_key=apiKey,
        created_at=datetime.utcnow()
    )
    
    jobs[job_id] = job
    
    # Start processing in background
    background_tasks.add_task(process_job, job, jobs, playbooks)
    
    return {
        "job_id": job_id,
        "status": "queued"
    }

@app.get("/api/status/{job_id}")
async def get_job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        # 404 Empty Body as per requirements
        return JSONResponse(content={}, status_code=404)
        
    return {
        "job_id": job.id,
        "id": job.id,  # Frontend expects 'id' not 'job_id'
        "status": job.status.value,  # Convert enum to string
        "progress": job.progress,
        "current_phase": job.current_phase,
        "operations_complete": job.operations_complete,
        "operations_total": job.operations_total,
        "errors": job.errors,
        "warnings": job.warnings
    }


@app.get("/api/download/{job_id}")
async def download_result(job_id: str):
    job = jobs.get(job_id)
    if not job:
         return JSONResponse(content={}, status_code=404)
         
    if job.status != JobStatus.COMPLETE or not job.result_file_path or not os.path.exists(job.result_file_path):
        return JSONResponse(content="Job not complete or file missing", status_code=400)
    
    # Construct filename: redlined_{original}
    filename = f"redlined_{job.original_filename}" if job.original_filename else "redlined_document.docx"
    
    return FileResponse(
        path=job.result_file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

@app.post("/api/download-batch")
async def download_batch(request: DownloadBatchRequest):
    if not request.jobIds:
         return JSONResponse(content="No job IDs provided", status_code=400)
         
    # Collect files
    valid_jobs = []
    for jid in request.jobIds:
        job = jobs.get(jid)
        if job and job.status == JobStatus.COMPLETE and job.result_file_path and os.path.exists(job.result_file_path):
            valid_jobs.append(job)
            
    if not valid_jobs:
        return JSONResponse(content="No completed files found", status_code=400)
        
    # Create ZIP
    zip_filename = "redlined_documents.zip"
    zip_path = os.path.join(PROCESSED_DIR, zip_filename)
    
    import zipfile
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for job in valid_jobs:
            arcname = f"redlined_{job.original_filename}" if job.original_filename else f"redlined_{job.id}.docx"
            zipf.write(job.result_file_path, arcname)
            
    return FileResponse(
        path=zip_path,
        filename=zip_filename,
        media_type="application/zip"
    )

@app.get("/api/playbooks")
async def list_playbooks():
    return {
        "playbooks": [pb.model_dump() for pb in playbooks.values()]
    }

@app.post("/api/playbooks")
async def create_playbook(
    name: str = Form(...),
    description: str = Form(""),
    file: Optional[UploadFile] = File(None)
):
    if not name:
        return JSONResponse(content="Name is required", status_code=400)

    playbook_id = name.lower().replace(" ", "-")
    
    playbook_text = ""
    if file and file.filename:
        try:
            from ooxml_lib import DocumentEditor
            from document_extractor import extract_document
            
            # Save file
            playbooks_dir = os.path.join(os.getcwd(), "Playbooks")
            os.makedirs(playbooks_dir, exist_ok=True)
            file_path = os.path.join(playbooks_dir, f"{playbook_id}.docx")
            
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
                
            # Extract text
            editor = DocumentEditor(file_path)
            doc_content = extract_document(editor)
            playbook_text = doc_content.get("text", "")
            logger.info(f"Extracted {len(playbook_text)} chars from playbook file")
        except Exception as e:
            logger.error(f"Failed to extract text from playbook: {e}")
            playbook_text = f"Error reading playbook file: {str(e)}"
        
    playbook = Playbook(
        id=playbook_id,
        name=name,
        description=description,
        playbookText=playbook_text
    )
    
    playbooks[playbook.id] = playbook
    
    return {"message": "Playbook created", "playbook": playbook.model_dump()}


@app.post("/api/apply-amend")
async def apply_amend_endpoint(request: LegacyAmendRequest):
    """
    Legacy endpoint for Word Add-in compatibility.
    """
    # Placeholder logic matching the example
    return {
        "success": True,
        "ooxml": request.ooxml.replace("</w:p>", "<w:ins>...</w:ins></w:p>"), # Mock change
        "hasChanges": True,
        "error": None,
        "debug": {
            "paragraphFound": True,
            "originalNumId": "1",
            "originalIlvl": "0",
            "resultNumId": "1",
            "resultIlvl": "0",
            "deletions": 1,
            "insertions": 1,
            "paragraphsInDocument": 15,
            "paragraphPreviews": ["First paragraph...", "Second paragraph..."]
        }
    }

class ConnectionRequest(BaseModel):
    api_key: str
    provider: str = "gemini"

@app.post("/api/test-connection")
async def test_connection(request: ConnectionRequest):
    from ai_client import get_gemini_models
    try:
        if request.provider.lower() == "gemini":
            models = await get_gemini_models(request.api_key)
            return {"success": True, "models": models}
        else:
            return JSONResponse(content={"success": False, "error": "Provider not supported"}, status_code=400)
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=400)

if __name__ == "__main__":
    import uvicorn
    # In development, run with reload but exclude node_modules
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        reload_excludes=["vibelegal-frontend/node_modules/*", "VibeLegalFrontend/*"]
    )

