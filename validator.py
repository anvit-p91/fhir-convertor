"""
FHIR Bundle Validator
----------------------
Validates generated FHIR bundles against NRCeS / NHCX profile rules.

Validation tiers:
1. Structural  — required fields, resource types, FHIR R4 cardinality
2. Profile     — ABDM / NHCX profile-specific rules
3. Terminology — checks coding system URIs are well-formed
4. Business    — NHCX-specific rules (e.g. Claim must reference at least one document)

This is an offline, rule-based validator. For production, complement with the
official HAPI FHIR validator (https://hapifhir.io/hapi-fhir/docs/validation/).
"""

import logging
from typing import Any, Dict, List, Optional

from models import ValidationIssue, ValidationReport

logger = logging.getLogger(__name__)


# ── Profile-level required field rules ───────────────────────────────────────

REQUIRED_FIELDS: Dict[str, List[str]] = {
    "Bundle": ["resourceType", "type", "timestamp", "entry"],
    "DiagnosticReport": ["resourceType", "status", "code", "subject"],
    "Composition": ["resourceType", "status", "type", "subject", "date", "author", "title"],
    "Patient": ["resourceType"],
    "Observation": ["resourceType", "status", "code", "subject"],
    "Condition": ["resourceType", "clinicalStatus", "code", "subject"],
    "Claim": ["resourceType", "status", "type", "use", "patient", "created", "insurer", "provider", "priority"],
    "Organization": ["resourceType"],
    "Practitioner": ["resourceType"],
    "MedicationRequest": ["resourceType", "status", "intent", "medicationCodeableConcept", "subject"],
    "Procedure": ["resourceType", "status", "subject", "code"],
}

VALID_STATUSES: Dict[str, List[str]] = {
    "DiagnosticReport": ["registered", "partial", "preliminary", "final", "amended", "corrected", "appended", "cancelled", "entered-in-error", "unknown"],
    "Observation": ["registered", "preliminary", "final", "amended", "corrected", "cancelled", "entered-in-error", "unknown"],
    "Composition": ["preliminary", "final", "amended", "entered-in-error"],
    "Claim": ["active", "cancelled", "draft", "entered-in-error"],
    "MedicationRequest": ["active", "on-hold", "cancelled", "completed", "entered-in-error", "stopped", "draft", "unknown"],
    "Procedure": ["preparation", "in-progress", "not-done", "on-hold", "stopped", "completed", "entered-in-error", "unknown"],
}


class FHIRValidator:
    """Rule-based FHIR bundle validator for ABDM/NHCX profiles."""

    def validate(self, bundle: Dict[str, Any]) -> ValidationReport:
        errors: List[ValidationIssue] = []
        warnings: List[ValidationIssue] = []
        info: List[ValidationIssue] = []

        profiles = []
        try:
            profiles = bundle.get("meta", {}).get("profile", [])
        except Exception:
            pass

        # ── 1. Bundle-level structural checks ────────────────────────────
        self._check_required("Bundle", bundle, errors)

        if bundle.get("resourceType") != "Bundle":
            errors.append(ValidationIssue(
                severity="error",
                location="Bundle.resourceType",
                message="Root resource must be 'Bundle'",
            ))

        bundle_type = bundle.get("type")
        if bundle_type not in ("document", "transaction", "collection", "message", "history", "searchset", "batch"):
            errors.append(ValidationIssue(
                severity="error",
                location="Bundle.type",
                message=f"Invalid Bundle type '{bundle_type}'. Expected 'document' for clinical documents.",
            ))

        entries = bundle.get("entry", [])
        if not entries:
            errors.append(ValidationIssue(
                severity="error",
                location="Bundle.entry",
                message="Bundle must contain at least one entry.",
            ))

        # ── 2. Entry-level checks ─────────────────────────────────────────
        resource_types_found = []
        for i, entry in enumerate(entries):
            loc_prefix = f"Bundle.entry[{i}]"
            resource = entry.get("resource", {})
            rt = resource.get("resourceType", "Unknown")
            resource_types_found.append(rt)

            if not resource:
                errors.append(ValidationIssue(
                    severity="error",
                    location=f"{loc_prefix}.resource",
                    message="Entry is missing a resource.",
                ))
                continue

            if not entry.get("fullUrl"):
                warnings.append(ValidationIssue(
                    severity="warning",
                    location=f"{loc_prefix}.fullUrl",
                    message="Entry should have a fullUrl for reliable referencing.",
                ))

            # Required fields per resource type
            self._check_required(rt, resource, errors, f"{loc_prefix}.resource")

            # Status validation
            self._check_status(rt, resource, errors, f"{loc_prefix}.resource")

            # Coding system checks
            self._check_codings(resource, warnings, f"{loc_prefix}.resource")

            # Profile conformance hints
            self._check_profile_conformance(rt, resource, errors, warnings, f"{loc_prefix}.resource")

        # ── 3. Bundle composition rules ───────────────────────────────────
        # For claim bundles, a Claim resource must be present
        if any("HCXClaim" in p for p in profiles):
            if "Claim" not in resource_types_found:
                errors.append(ValidationIssue(
                    severity="error",
                    location="Bundle",
                    message="NHCX Claim Bundle must contain a Claim resource.",
                    profile=profiles[0] if profiles else None,
                ))

        # Patient must always be present
        if "Patient" not in resource_types_found:
            errors.append(ValidationIssue(
                severity="error",
                location="Bundle.entry",
                message="Bundle must contain a Patient resource.",
            ))

        # At least one clinical document
        clinical_types = {"DiagnosticReport", "Composition"}
        if not clinical_types.intersection(set(resource_types_found)):
            warnings.append(ValidationIssue(
                severity="warning",
                location="Bundle.entry",
                message="Bundle should contain at least one clinical document resource (DiagnosticReport or Composition/DischargeSummary).",
            ))

        # ── 4. Info messages ──────────────────────────────────────────────
        info.append(ValidationIssue(
            severity="information",
            location="Bundle",
            message=(
                f"Validated {len(entries)} entries. Resource types found: "
                f"{', '.join(sorted(set(resource_types_found)))}."
            ),
        ))

        info.append(ValidationIssue(
            severity="information",
            location="Bundle",
            message=(
                "For full HL7 profile conformance, also run against the official HAPI FHIR "
                "validator at https://validator.fhir.org or use the NRCeS FHIR sandbox."
            ),
        ))

        valid = len(errors) == 0
        return ValidationReport(
            valid=valid,
            profile_urls=profiles,
            total_issues=len(errors) + len(warnings) + len(info),
            errors=errors,
            warnings=warnings,
            information=info,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_required(
        self,
        resource_type: str,
        resource: Dict,
        errors: List[ValidationIssue],
        prefix: str = "",
    ):
        required = REQUIRED_FIELDS.get(resource_type, [])
        for field in required:
            if not resource.get(field):
                errors.append(ValidationIssue(
                    severity="error",
                    location=f"{prefix}.{field}".lstrip("."),
                    message=f"{resource_type}.{field} is required but missing or empty.",
                ))

    def _check_status(
        self,
        resource_type: str,
        resource: Dict,
        errors: List[ValidationIssue],
        prefix: str,
    ):
        valid_statuses = VALID_STATUSES.get(resource_type)
        if valid_statuses and "status" in resource:
            status = resource.get("status")
            if status not in valid_statuses:
                errors.append(ValidationIssue(
                    severity="error",
                    location=f"{prefix}.status",
                    message=(
                        f"Invalid status '{status}' for {resource_type}. "
                        f"Allowed: {', '.join(valid_statuses)}"
                    ),
                ))

    def _check_codings(
        self,
        resource: Dict,
        warnings: List[ValidationIssue],
        prefix: str,
    ):
        known_systems = {
            "http://loinc.org",
            "http://snomed.info/sct",
            "http://terminology.hl7.org/CodeSystem/v2-0074",
            "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "http://terminology.hl7.org/CodeSystem/condition-category",
            "http://terminology.hl7.org/CodeSystem/condition-clinical",
            "http://terminology.hl7.org/CodeSystem/claim-type",
            "http://terminology.hl7.org/CodeSystem/claiminformationcategory",
            "http://unitsofmeasure.org",
            "https://healthid.ndhm.gov.in",
        }

        def walk(obj, path):
            if isinstance(obj, dict):
                if "system" in obj and "code" in obj:
                    sys = obj["system"]
                    if sys and not sys.startswith("http") and not sys.startswith("urn"):
                        warnings.append(ValidationIssue(
                            severity="warning",
                            location=path,
                            message=f"Coding system '{sys}' may not be a valid URI. Prefer a recognized system.",
                        ))
                for k, v in obj.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    walk(item, f"{path}[{i}]")

        walk(resource, prefix)

    def _check_profile_conformance(
        self,
        rt: str,
        resource: Dict,
        errors: List[ValidationIssue],
        warnings: List[ValidationIssue],
        prefix: str,
    ):
        if rt == "DiagnosticReport":
            # Must have a subject reference
            subj = resource.get("subject", {}).get("reference", "")
            if not subj.startswith("Patient/") and not subj.startswith("urn:"):
                warnings.append(ValidationIssue(
                    severity="warning",
                    location=f"{prefix}.subject",
                    message="DiagnosticReport.subject should reference a Patient resource.",
                ))

        if rt == "Observation":
            # Must have either valueQuantity or valueString or dataAbsentReason
            has_value = any(k in resource for k in (
                "valueQuantity", "valueString", "valueBoolean", "valueInteger",
                "valueRange", "valueRatio", "valueSampledData", "valueTime",
                "valueDateTime", "valuePeriod", "dataAbsentReason", "component",
            ))
            if not has_value:
                warnings.append(ValidationIssue(
                    severity="warning",
                    location=f"{prefix}",
                    message="Observation should have a value[x] or dataAbsentReason.",
                ))

        if rt == "Patient":
            if not resource.get("name") and not resource.get("identifier"):
                warnings.append(ValidationIssue(
                    severity="warning",
                    location=f"{prefix}",
                    message="Patient resource should have at least a name or identifier.",
                ))
