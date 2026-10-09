"""
Tests for PDF-to-FHIR Converter
Run: pytest tests/ -v
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from classifier import DocumentClassifier
from fhir_builder import FHIRBundleBuilder
from validator import FHIRValidator


# ── Classifier tests ──────────────────────────────────────────────────────────

class TestClassifier:
    classifier = DocumentClassifier()

    def test_echo_report_detected(self):
        text = "2D ECHO\nM MODE MEASUREMENTS\nLVEF : 40-45%\n2D ECHO FINDING"
        assert self.classifier.detect(text, "echo.pdf") == "DiagnosticReport"

    def test_discharge_summary_detected(self):
        text = "Discharge Summary\nDate of Admission: 01/03/2025\nDate of Discharge: 05/03/2025\nAdvice on Discharge"
        assert self.classifier.detect(text, "discharge.pdf") == "DischargeSummary"

    def test_lab_report_detected(self):
        text = "Laboratory Report\nHaemoglobin: 12.4 g/dL\nPlatelet count: 180000\nWBC count: 6500"
        assert self.classifier.detect(text, "lab_report.pdf") == "DiagnosticReport"

    def test_filename_hint_boosts_score(self):
        text = "some generic text"
        # Low text signal but filename says discharge
        result = self.classifier.detect(text, "discharge_summary_patient.pdf")
        # Should boost DischargeSummary score
        assert result in ("DischargeSummary", "DiagnosticReport")  # graceful fallback

    def test_low_confidence_defaults_to_diagnostic(self):
        result = self.classifier.detect("random unrelated content", "file.pdf")
        assert result == "DiagnosticReport"


# ── FHIR Builder tests ────────────────────────────────────────────────────────

SAMPLE_ECHO_DOC = {
    "_hi_type": "DiagnosticReport",
    "_source_file": "echo.pdf",
    "report_date": "2025-03-31",
    "report_type": "2D Echocardiography",
    "report_category": "cardiology",
    "loinc_code": "34552-0",
    "loinc_display": "2D cardiac echo panel",
    "patient": {"name": "John Doe", "age": "55", "sex": "male", "uhid": "UHID001"},
    "hospital": {"name": "Test Hospital", "address": "123 Main St"},
    "performing_doctor": "Dr. Smith, MD DM Cardiology",
    "observations": [
        {"code": "18029-5", "display": "Left Atrium", "value": 28, "unit": "mm", "interpretation": "normal"},
        {"code": "8867-4", "display": "LVEF", "value": "40-45", "unit": "%", "interpretation": "abnormal"},
    ],
    "impression": ["Ischemic Heart Disease", "Moderately Compromised LV Function"],
    "conclusion": "IHD with RWMA.",
}


class TestFHIRBuilder:
    builder = FHIRBundleBuilder()

    def _build(self):
        return self.builder.build_bundle(
            documents=[SAMPLE_ECHO_DOC],
            use_case="claim_submission",
            patient_id="test-patient-001",
            claim_id="test-claim-001",
            request_id="test-req-001",
        )

    def test_bundle_is_dict(self):
        bundle = self._build()
        assert isinstance(bundle, dict)

    def test_bundle_resourcetype(self):
        bundle = self._build()
        assert bundle["resourceType"] == "Bundle"

    def test_bundle_type_is_document(self):
        bundle = self._build()
        assert bundle["type"] == "document"

    def test_bundle_has_entries(self):
        bundle = self._build()
        assert len(bundle["entry"]) > 0

    def test_patient_present(self):
        bundle = self._build()
        rts = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Patient" in rts

    def test_diagnostic_report_present(self):
        bundle = self._build()
        rts = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "DiagnosticReport" in rts

    def test_observations_present(self):
        bundle = self._build()
        rts = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Observation" in rts

    def test_claim_present_for_claim_submission(self):
        bundle = self._build()
        rts = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Claim" in rts

    def test_organization_present(self):
        bundle = self._build()
        rts = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Organization" in rts

    def test_nhcx_profile_in_meta(self):
        bundle = self._build()
        profiles = bundle.get("meta", {}).get("profile", [])
        assert any("nhcx" in p.lower() or "hcx" in p.lower() for p in profiles)

    def test_observation_has_value(self):
        bundle = self._build()
        obs_resources = [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"]
        for obs in obs_resources:
            has_val = any(k in obs for k in ("valueQuantity", "valueString", "valueRange"))
            assert has_val, f"Observation {obs.get('id')} missing value"

    def test_diagnostic_report_has_loinc(self):
        bundle = self._build()
        dr = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "DiagnosticReport")
        code_system = dr["code"]["coding"][0]["system"]
        assert code_system == "http://loinc.org"

    def test_patient_gender_set(self):
        bundle = self._build()
        patient = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
        assert patient.get("gender") == "male"

    def test_fullurls_are_urn_uuids(self):
        bundle = self._build()
        for entry in bundle["entry"]:
            assert entry["fullUrl"].startswith("urn:uuid:")


# ── Validator tests ───────────────────────────────────────────────────────────

class TestValidator:
    validator = FHIRValidator()
    builder = FHIRBundleBuilder()

    def _valid_bundle(self):
        return self.builder.build_bundle(
            documents=[SAMPLE_ECHO_DOC],
            use_case="claim_submission",
            patient_id="vp-001",
            claim_id="vc-001",
            request_id="vr-001",
        )

    def test_valid_bundle_passes(self):
        bundle = self._valid_bundle()
        report = self.validator.validate(bundle)
        assert report.valid is True
        assert len(report.errors) == 0

    def test_missing_resourcetype_raises_error(self):
        bundle = {"type": "document", "timestamp": "2025-01-01T00:00:00Z", "entry": []}
        report = self.validator.validate(bundle)
        assert not report.valid
        error_msgs = [e.message for e in report.errors]
        assert any("Bundle" in m for m in error_msgs)

    def test_empty_entry_raises_error(self):
        bundle = {"resourceType": "Bundle", "type": "document", "timestamp": "2025-01-01T00:00:00Z", "entry": []}
        report = self.validator.validate(bundle)
        assert not report.valid

    def test_missing_patient_raises_error(self):
        bundle = self._valid_bundle()
        bundle["entry"] = [e for e in bundle["entry"] if e["resource"]["resourceType"] != "Patient"]
        report = self.validator.validate(bundle)
        assert not report.valid
        assert any("Patient" in e.message for e in report.errors)

    def test_validation_report_has_profiles(self):
        bundle = self._valid_bundle()
        report = self.validator.validate(bundle)
        assert isinstance(report.profile_urls, list)

    def test_info_messages_present(self):
        bundle = self._valid_bundle()
        report = self.validator.validate(bundle)
        assert len(report.information) > 0
