"""
Pydantic models for API contracts.
"""

from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from enum import Enum


class UseCase(str, Enum):
    CLAIM_SUBMISSION = "claim_submission"
    PRE_AUTHORISATION = "pre_authorisation"


class HIType(str, Enum):
    DIAGNOSTIC_REPORT = "DiagnosticReport"
    DISCHARGE_SUMMARY = "DischargeSummary"
    PRESCRIPTION = "Prescription"
    UNKNOWN = "Unknown"


class ValidationIssue(BaseModel):
    severity: str = Field(..., description="error | warning | information")
    location: str = Field(..., description="FHIR path of the issue")
    message: str
    profile: Optional[str] = None


class ValidationReport(BaseModel):
    valid: bool
    profile_urls: List[str]
    total_issues: int
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
    information: List[ValidationIssue] = []


class ConversionResponse(BaseModel):
    request_id: str
    use_case: UseCase
    documents_processed: int
    hi_types_detected: List[str]
    fhir_bundle: Dict[str, Any] = Field(..., description="NHCX-compliant FHIR R4 Bundle JSON")
    validation_report: Optional[ValidationReport] = None
    processing_errors: Optional[List[Dict[str, str]]] = None
