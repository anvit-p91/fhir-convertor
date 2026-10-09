"""
demo_echo.py
------------
Standalone demo: reads the 2D Echo report, runs extraction + FHIR bundle generation,
and writes the NHCX-compliant FHIR bundle to sample_output/echo_fhir_bundle.json.

Run:
    ANTHROPIC_API_KEY=<your_key> python demo_echo.py
"""

import json
import os
import sys
import uuid
import re
from datetime import datetime, timezone

# ── Simulated extracted data from the 2D Echo report ─────────────────────────
# (This represents what the LLM extractor would return from the uploaded PDF)

EXTRACTED_ECHO = {
    "_hi_type": "DiagnosticReport",
    "_source_file": "2D_Echo_Report.pdf",
    "report_date": "2025-03-31",
    "report_type": "2D Echocardiography",
    "report_category": "cardiology",
    "loinc_code": "34552-0",
    "loinc_display": "2D cardiac echo panel",
    "patient": {
        "name": None,  # redacted in source
        "age": None,   # redacted in source
        "sex": None,   # redacted in source
        "uhid": None,  # redacted in source
    },
    "referring_doctor": None,
    "hospital": {
        "name": "All is Well Multi Speciality Hospital",
        "address": "Near Macro Vision Academy, Mohammadpura, BURHANPUR 450331 (M.P.)",
    },
    "performing_doctor": "MBBS, MD, DM Cardiology",
    "observations": [
        # M-Mode Measurements
        {"code": "18029-5", "display": "Left Atrium (LA)", "value": 28, "unit": "mm", "interpretation": "normal"},
        {"code": "18026-1", "display": "Aortic Root (AO)", "value": 19, "unit": "mm", "interpretation": "normal"},
        {"code": "18148-3", "display": "IVS Diastole (IVS-d)", "value": 10, "unit": "mm", "interpretation": "normal"},
        {"code": "18151-7", "display": "LVPW Diastole (LVPW-d)", "value": 11, "unit": "mm", "interpretation": "normal"},
        {"code": "18026-9", "display": "LV Internal Diastolic Diameter (LVID-d)", "value": 41, "unit": "mm", "interpretation": "normal"},
        {"code": "18026-7", "display": "LV Internal Systolic Diameter (LVID-s)", "value": 30, "unit": "mm", "interpretation": "normal"},
        {"code": "18050-1", "display": "Right Atrium 4C (RA 4C)", "value": 29, "unit": "mm", "interpretation": "normal"},
        {"code": "59080-8", "display": "RVID Diastole (RVID-d)", "value": 28, "unit": "mm", "interpretation": "normal"},
        {
            "code": "8867-4",
            "display": "Left Ventricular Ejection Fraction (LVEF)",
            "value": "40-45",
            "unit": "%",
            "interpretation": "abnormal",
        },
    ],
    "impression": [
        "Regional Wall Motion Abnormality (RWMA): Hypokinesia of Inferior Wall, Infero-Lateral Wall, Infero-Septal Wall",
        "Mitral Valve (MV): MAC+ (Mitral Annular Calcification)",
        "Aortic Valve (AV): Sclerosed",
        "IAS/IVS: Intact",
        "IVC: Normal",
        "Clot/Vegetation: No",
        "Pericardium: Normal",
        "Grade I Diastolic Dysfunction",
        "Trivial MR / Trivial TR",
        "No PAH",
        "Left Sided Aortic Arch",
        "No E/O COA (No Evidence Of Coarctation Of Aorta)",
    ],
    "conclusion": (
        "Degenerative affection of MV & AV. Ischemic Heart Disease. "
        "RWMA as above. Evidence of LV Diastolic Dysfunction. "
        "Trivial MR / Trivial TR. No PAH. "
        "Moderately Compromised LV Systolic Function (LVEF 40-45%)."
    ),
}


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ref(rt, rid):
    return {"reference": f"{rt}/{rid}"}


INTERP = {
    "normal": {"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": "N", "display": "Normal"},
    "abnormal": {"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": "A", "display": "Abnormal"},
}

NHCX_CLAIM_PROFILE = "https://ig.hcxprotocol.io/v0.8/StructureDefinition-HCXClaimBundle.html"
DR_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReport"
OBS_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation"
PAT_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient"
ORG_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"
PRAC_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner"
CLAIM_PROFILE = "https://ig.hcxprotocol.io/v0.8/StructureDefinition-Claim.html"


def build_echo_fhir_bundle():
    doc = EXTRACTED_ECHO
    patient_id = _uuid()
    org_id = _uuid()
    prac_id = _uuid()
    claim_id = _uuid()
    bundle_id = _uuid()

    entries = []

    # ── Patient ───────────────────────────────────────────────────────────
    patient = {
        "resourceType": "Patient",
        "id": patient_id,
        "meta": {"profile": [PAT_PROFILE]},
        "gender": "unknown",
        "identifier": [],
    }
    entries.append({"fullUrl": f"urn:uuid:{patient_id}", "resource": patient, "request": {"method": "POST", "url": "Patient"}})

    # ── Organization ──────────────────────────────────────────────────────
    org = {
        "resourceType": "Organization",
        "id": org_id,
        "meta": {"profile": [ORG_PROFILE]},
        "active": True,
        "name": doc["hospital"]["name"],
        "address": [{"text": doc["hospital"]["address"], "use": "work"}],
        "telecom": [
            {"system": "phone", "value": "+91 76977 44444"},
            {"system": "phone", "value": "+91 70890 55888"},
            {"system": "email", "value": "frontdesk@alliswellhospital.com"},
            {"system": "url", "value": "https://www.alliswellhospital.com"},
        ],
    }
    entries.append({"fullUrl": f"urn:uuid:{org_id}", "resource": org, "request": {"method": "POST", "url": "Organization"}})

    # ── Practitioner ──────────────────────────────────────────────────────
    prac = {
        "resourceType": "Practitioner",
        "id": prac_id,
        "meta": {"profile": [PRAC_PROFILE]},
        "active": True,
        "name": [{"text": "Cardiologist"}],
        "qualification": [
            {"code": {"text": "MBBS, MD, DM Cardiology"}}
        ],
    }
    entries.append({"fullUrl": f"urn:uuid:{prac_id}", "resource": prac, "request": {"method": "POST", "url": "Practitioner"}})

    # ── Observations ──────────────────────────────────────────────────────
    obs_refs = []
    for obs_data in doc["observations"]:
        obs_id = _uuid()
        raw_val = obs_data["value"]
        obs = {
            "resourceType": "Observation",
            "id": obs_id,
            "meta": {"profile": [OBS_PROFILE]},
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "imaging", "display": "Imaging"}]}],
            "code": {
                "coding": [{"system": "http://loinc.org", "code": obs_data["code"], "display": obs_data["display"]}],
                "text": obs_data["display"],
            },
            "subject": _ref("Patient", patient_id),
            "effectiveDateTime": doc["report_date"],
            "performer": [_ref("Practitioner", prac_id)],
        }
        # Value
        try:
            # Handle range like "40-45"
            if isinstance(raw_val, str) and "-" in raw_val:
                parts = raw_val.split("-")
                low, high = float(parts[0]), float(parts[1])
                obs["valueRange"] = {
                    "low": {"value": low, "unit": obs_data["unit"], "system": "http://unitsofmeasure.org", "code": obs_data["unit"]},
                    "high": {"value": high, "unit": obs_data["unit"], "system": "http://unitsofmeasure.org", "code": obs_data["unit"]},
                }
            else:
                obs["valueQuantity"] = {
                    "value": float(raw_val),
                    "unit": obs_data["unit"],
                    "system": "http://unitsofmeasure.org",
                    "code": obs_data["unit"],
                }
        except Exception:
            obs["valueString"] = str(raw_val)

        if obs_data.get("interpretation") in INTERP:
            obs["interpretation"] = [{"coding": [INTERP[obs_data["interpretation"]]]}]

        entries.append({"fullUrl": f"urn:uuid:{obs_id}", "resource": obs, "request": {"method": "POST", "url": "Observation"}})
        obs_refs.append(_ref("Observation", obs_id))

    # ── DiagnosticReport ──────────────────────────────────────────────────
    dr_id = _uuid()
    dr = {
        "resourceType": "DiagnosticReport",
        "id": dr_id,
        "meta": {"profile": [DR_PROFILE]},
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "CUS", "display": "Cardiac Ultrasound"}]}],
        "code": {
            "coding": [{"system": "http://loinc.org", "code": doc["loinc_code"], "display": doc["loinc_display"]}],
            "text": doc["report_type"],
        },
        "subject": _ref("Patient", patient_id),
        "effectiveDateTime": doc["report_date"],
        "issued": _now(),
        "performer": [_ref("Organization", org_id)],
        "resultsInterpreter": [_ref("Practitioner", prac_id)],
        "result": obs_refs,
        "conclusion": doc["conclusion"],
        "conclusionCode": [
            {"coding": [{"system": "http://snomed.info/sct", "code": "53741008", "display": "Ischemic heart disease"}]},
            {"coding": [{"system": "http://snomed.info/sct", "code": "84114007", "display": "Heart failure"}]},
        ],
    }
    entries.append({"fullUrl": f"urn:uuid:{dr_id}", "resource": dr, "request": {"method": "POST", "url": "DiagnosticReport"}})

    # ── Claim ─────────────────────────────────────────────────────────────
    claim = {
        "resourceType": "Claim",
        "id": claim_id,
        "meta": {"profile": [CLAIM_PROFILE]},
        "status": "active",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "institutional", "display": "Institutional"}]},
        "use": "claim",
        "patient": _ref("Patient", patient_id),
        "created": _now(),
        "insurer": {"display": "Health Insurance Provider"},
        "provider": _ref("Organization", org_id),
        "priority": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/processpriority", "code": "normal"}]},
        "supportingInfo": [
            {
                "sequence": 1,
                "category": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claiminformationcategory", "code": "info"}]},
                "valueReference": _ref("DiagnosticReport", dr_id),
            }
        ],
        "diagnosis": [
            {
                "sequence": 1,
                "diagnosisCodeableConcept": {
                    "coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": "I25.9", "display": "Chronic ischemic heart disease, unspecified"}],
                    "text": "Ischemic Heart Disease",
                },
                "type": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/ex-diagnosistype", "code": "principal"}]}],
            }
        ],
        "item": [
            {
                "sequence": 1,
                "productOrService": {
                    "coding": [{"system": "http://loinc.org", "code": "34552-0", "display": "2D cardiac echo panel"}],
                    "text": "2D Echocardiography",
                },
                "servicedDate": doc["report_date"],
                "quantity": {"value": 1},
            }
        ],
    }
    entries.append({"fullUrl": f"urn:uuid:{claim_id}", "resource": claim, "request": {"method": "POST", "url": "Claim"}})

    # ── Final Bundle ──────────────────────────────────────────────────────
    bundle = {
        "resourceType": "Bundle",
        "id": bundle_id,
        "meta": {
            "lastUpdated": _now(),
            "profile": [NHCX_CLAIM_PROFILE],
        },
        "identifier": {
            "system": "https://nhcx.abdm.gov.in/claim",
            "value": claim_id,
        },
        "type": "document",
        "timestamp": _now(),
        "entry": entries,
    }
    return bundle


if __name__ == "__main__":
    bundle = build_echo_fhir_bundle()
    out_path = "sample_output/echo_fhir_bundle.json"
    os.makedirs("sample_output", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(bundle, f, indent=2)
    print(f"✅  FHIR Bundle written to {out_path}")
    print(f"    Resources: {len(bundle['entry'])} entries")
    for e in bundle["entry"]:
        rt = e["resource"]["resourceType"]
        rid = e["resource"]["id"][:8]
        print(f"       • {rt:<25} id={rid}...")
