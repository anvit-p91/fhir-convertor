"""
PDF-to-FHIR Converter Microservice
Open-source solution for ABDM/NHCX-compliant FHIR bundle generation
from clinical PDF documents (DiagnosticReport & DischargeSummary)
"""

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import List, Optional
import uvicorn
import logging
import uuid
import os
from datetime import datetime

from extractor import PDFExtractor
from classifier import DocumentClassifier
from fhir_builder import FHIRBundleBuilder
from validator import FHIRValidator
from models import ConversionResponse, ValidationReport, UseCase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF-to-FHIR Converter",
    description=(
        "Open-source microservice for converting PDF clinical documents "
        "(Diagnostic Reports, Discharge Summaries) to ABDM/NHCX-compliant FHIR R4 bundles. "
        "Supports Claim Submission and Pre-Authorisation workflows."
    ),
    version="1.0.0",
    contact={"name": "NHCX FHIR Converter", "url": "https://github.com/nhcx-fhir-converter"},
    license_info={"name": "MIT"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

extractor = PDFExtractor()
classifier = DocumentClassifier()
builder = FHIRBundleBuilder()
validator = FHIRValidator()


UI_FILE = os.path.join(os.path.dirname(__file__), "fhir_converter_ui.html")


@app.get("/", tags=["Frontend"], include_in_schema=False)
@app.get("/ui", tags=["Frontend"], include_in_schema=False)
def serve_ui():
    if os.path.exists(UI_FILE):
        return FileResponse(UI_FILE, media_type="text/html")
    raise HTTPException(status_code=404, detail="UI file not found. Place fhir_converter_ui.html next to main.py.")


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.post(
    "/convert",
    response_model=ConversionResponse,
    tags=["Conversion"],
    summary="Convert one or more PDF documents to a FHIR Bundle",
)
async def convert_documents(
    files: List[UploadFile] = File(..., description="One or more PDF clinical documents"),
    use_case: UseCase = UseCase.CLAIM_SUBMISSION,
    patient_id: Optional[str] = None,
    claim_id: Optional[str] = None,
    validate: bool = True,
):
    """
    Accepts 1–N clinical PDFs, auto-detects the HI Type (DiagnosticReport /
    DischargeSummary), extracts structured data using LLM, and returns a
    NHCX-compliant FHIR R4 Bundle.

    - **files**: Upload multiple PDFs (e.g., lab reports + discharge summary)
    - **use_case**: `claim_submission` or `pre_authorisation`
    - **patient_id**: Optional ABHA / hospital MRN to link resources
    - **claim_id**: Claim / pre-auth reference number
    - **validate**: Run NHCX profile validation after building bundle
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF file is required.")

    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Processing {len(files)} document(s) for use_case={use_case}")

    all_extracted = []
    errors = []

    for upload in files:
        if not upload.filename.lower().endswith(".pdf"):
            errors.append({"file": upload.filename, "error": "Only PDF files are accepted."})
            continue

        try:
            raw_bytes = await upload.read()
            text = extractor.extract_text(raw_bytes)
            hi_type = classifier.detect(text, upload.filename)
            extracted = extractor.extract_structured(text, hi_type)
            all_extracted.append({
                "hi_type": hi_type,
                "data": extracted,
                "filename": upload.filename,
            })
            logger.info(f"[{request_id}] {upload.filename} → {hi_type}")
        except Exception as e:
            logger.error(f"[{request_id}] Failed to process {upload.filename}: {e}")
            errors.append({"file": upload.filename, "error": str(e)})

    if not all_extracted:
        raise HTTPException(
            status_code=422,
            detail={"message": "No documents could be processed.", "errors": errors},
        )

    fhir_bundle = builder.build_bundle(
        documents=all_extracted,
        use_case=use_case,
        patient_id=patient_id,
        claim_id=claim_id,
    )

    validation_report = None
    if validate:
        validation_report = validator.validate(fhir_bundle)

    return ConversionResponse(
        request_id=request_id,
        use_case=use_case,
        documents_processed=len(all_extracted),
        # Fixed: use "hi_type" key, not "_hi_type"
        hi_types_detected=[d["hi_type"] for d in all_extracted],
        fhir_bundle=fhir_bundle,
        validation_report=validation_report,
        processing_errors=errors if errors else None,
    )


@app.post(
    "/validate",
    response_model=ValidationReport,
    tags=["Validation"],
    summary="Validate an existing FHIR Bundle against NHCX profiles",
)
async def validate_bundle(bundle: dict):
    """
    Validates a pre-built FHIR bundle against NRCeS-defined NHCX profiles.
    Returns a structured report with errors, warnings, and profile conformance status.
    """
    report = validator.validate(bundle)
    return report


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
