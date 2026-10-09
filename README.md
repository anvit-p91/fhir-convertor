# PDF-to-FHIR Converter Microservice

> **Open-source solution for converting PDF clinical documents into ABDM/NHCX-compliant FHIR R4 bundles.**
> Addresses the problem statement for the NHCX FHIR Utility for Providers — Category: *Clinical Documents to FHIR Structured Data Converter*.

---

## Overview

Hospitals produce clinical documents (diagnostic reports, discharge summaries) as PDFs. ABDM and NHCX require structured FHIR R4 data for claim submission workflows. This microservice bridges that gap automatically — upload one or more PDFs, get back a fully validated, NHCX-compliant FHIR R4 Bundle.

```
PDF (Diagnostic Report / Discharge Summary)
        │
        ▼
┌──────────────────────────────────────────────┐
│  PDF-to-FHIR Converter Microservice          │
│                                              │
│  1. PDFExtractor  ── text / OCR extraction   │
│  2. Classifier    ── HI Type auto-detection  │
│  3. LLM           ── structured extraction  │
│  4. FHIRBuilder   ── ABDM/NHCX resource gen │
│  5. Validator     ── profile conformance     │
└──────────────────────────────────────────────┘
        │
        ▼
NHCX-compliant FHIR R4 Bundle (JSON)
```

---

## Requirements Coverage

| # | Requirement | Status | How |
|---|---|---|---|
| 1 | Claim submission with multiple PDFs | ✅ | `main.py` accepts `List[UploadFile]`; all docs merged into one Bundle with a `Claim` resource |
| 2 | Accept PDF clinical documents | ✅ | PDF-only enforced in `main.py`; PyMuPDF for digital PDFs + pytesseract OCR for scanned/image pages |
| 3 | Detect HI type (DischargeSummary / DiagnosticReport) | ✅ | `classifier.py` keyword scoring on full text + filename hints; auto-detects on drop |
| 4 | Transform extracted data into FHIR R4 resources | ✅ | `fhir_builder.py` builds 13+ resource types per document |
| 5 | FHIR bundles conforming to NHCX profiles | ✅ | All resources carry `meta.profile` URLs from `ig.hcxprotocol.io/v0.8` and `nrces.in/ndhm/fhir/r4` |
| 6 | Validation against NRCeS NHCX profiles + error reporting | ✅ | 4-tier offline validator; errors displayed in UI with Valid/Invalid badge |

---

## Features

| Feature | Details |
|---|---|
| **Use Case** | Claim Submission (default) |
| **HI Types** | DiagnosticReport, DischargeSummary (Composition) |
| **PDF Input** | Digital PDFs (PyMuPDF) + Scanned/Image PDFs (Tesseract OCR) |
| **LLM Providers** | Anthropic Claude (default), OpenAI GPT-4o, Google Gemini — switchable via env var |
| **Extraction Quality** | 10-rule clinically-aware prompt: accurate gender, ISO dates, correct ICD-10 codes, verbatim hospital/doctor names, all lab values and medications |
| **Multi-document** | Upload N PDFs, get one unified FHIR Bundle |
| **Auto-classify** | Drop any PDF — HI type detected automatically, no manual selection needed |
| **Medications** | Each discharge medication → individual `MedicationRequest` with name, dose, route, frequency, duration |
| **Profiles** | NRCeS ABDM + NHCX/HCX IG v0.8 profiles on all resources |
| **Validation** | 4-tier offline rule-based validator (structural, profile, terminology, business) |
| **Web UI** | Drag-and-drop interface with sticky action bar, live progress steps, JSON viewer |
| **Language** | Python 3.11, FastAPI, FHIR R4 |
| **Deployment** | Docker, standalone, or library import |

---

## Architecture

```
pdf_fhir_converter/
├── main.py              # FastAPI routes (/convert, /validate, /health)
├── extractor.py         # PDF text extraction + multi-provider LLM structured extraction
├── classifier.py        # HI Type auto-detection (keyword scoring + filename hints)
├── fhir_builder.py      # FHIR R4 resource construction (13+ resource types)
├── validator.py         # 4-tier NHCX profile validation
├── models.py            # Pydantic API contracts
├── tests/
│   └── test_converter.py
├── sample_output/
│   └── echo_fhir_bundle.json   # Example output from 2D Echo PDF
├── demo_echo.py         # Standalone demo (no server needed)
├── index.html           # Browser-based drag-and-drop UI
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## FHIR Resources Generated

### DiagnosticReport (e.g. 2D Echo, Lab Report, Radiology)

```
Bundle (NHCX HCXClaimBundle)
├── Patient              ← name, gender, age, UHID, ABHA ID
├── Organization         ← hospital name (verbatim from letterhead), address, telecom
├── Practitioner         ← performing/interpreting doctor name and qualifications
├── Observation[0..N]    ← each measured parameter with LOINC codes
│                          (normal/abnormal/critical interpretation flagged)
│                          e.g. LA, AO, IVS-d, LVID-d, LVEF — range values supported
├── DiagnosticReport     ← LOINC study code, links all observations + conclusion
└── Claim                ← institutional claim, ICD-10 diagnosis, DiagnosticReport
                           as supportingInfo
```

### DischargeSummary

```
Bundle (NHCX HCXClaimBundle)
├── Patient              ← gender, age, UHID, ABHA ID, payer type
├── Organization         ← hospital (verbatim name from letterhead)
├── Practitioner         ← attending doctor (from signature / Doctor In-Charge field)
├── Encounter            ← admission date, discharge date, ward, IP number
├── Appointment          ← linked to Encounter
├── Condition[0..N]      ← chief complaints + primary/secondary diagnoses (ICD-10)
├── Procedure[0..N]      ← procedures performed (SNOMED coded)
├── Observation[0..N]    ← investigation results with values and LOINC codes
├── DiagnosticReport     ← investigation summary
├── MedicationRequest[0..N] ← each discharge medication (name is dynamic from
│                             document — dose, route, frequency, duration captured)
├── CarePlan             ← follow-up instructions and diet advice
├── DocumentReference    ← links the source PDF
├── Composition          ← DischargeSummary document with all sections
└── Claim                ← NHCX claim with accurate ICD-10 codes
                           e.g. DKA → E10.1, T1DM → E10.9, Ascites → R18
```

> **Note on Medication coding:** Medicine names are fully dynamic — extracted verbatim from the document (e.g. "INJ NOVARAPID", "INJ GILARCIN"). The SNOMED code `372687004` (Medicinal product) is used as a generic placeholder since per-drug SNOMED/RxNorm lookup requires a live terminology server.

---

## FHIR Profiles Targeted

| Resource | Profile URL |
|---|---|
| Bundle (Claim) | `https://ig.hcxprotocol.io/v0.8/StructureDefinition-HCXClaimBundle.html` |
| DiagnosticReport | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReport` |
| DischargeSummary | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/DischargeSummaryRecord` |
| Composition | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Composition` |
| Patient | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient` |
| Observation | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation` |
| Condition | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Condition` |
| MedicationRequest | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/MedicationRequest` |
| Procedure | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Procedure` |
| Encounter | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Encounter` |
| Organization | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization` |
| Practitioner | `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner` |
| Claim | `https://ig.hcxprotocol.io/v0.8/StructureDefinition-Claim.html` |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/your-org/pdf-fhir-converter
cd pdf-fhir-converter
pip install -r requirements.txt
```

### 2. Set LLM provider API key

```bash
# Default: Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...

# Or switch to OpenAI
export LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...

# Or switch to Google Gemini
export LLM_PROVIDER=gemini
export GEMINI_API_KEY=...
```

### 3. Run the API server

```bash
uvicorn main:app --reload
# Swagger docs at: http://localhost:8000/docs
```

### 4. Run the demo (no server needed)

```bash
python demo_echo.py
# Output: sample_output/echo_fhir_bundle.json
```

### 5. Convert a single PDF

```bash
curl -X POST http://localhost:8000/convert \
  -F "files=@discharge_summary.pdf" \
  -F "use_case=claim_submission" \
  -F "claim_id=CLM-2025-001"
```

### 6. Multiple documents in one claim

```bash
curl -X POST http://localhost:8000/convert \
  -F "files=@discharge_summary.pdf" \
  -F "files=@lab_report.pdf" \
  -F "files=@echo_report.pdf" \
  -F "use_case=claim_submission" \
  -F "claim_id=CLM-2025-001" \
  -F "patient_id=ABHA-1234-5678-9012"
```

### 7. Docker

```bash
docker build -t pdf-fhir-converter .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... pdf-fhir-converter
```

---

## Web UI

Open `index.html` in a browser for a drag-and-drop interface:

- **Drag-and-drop or click** to upload PDFs — conversion starts automatically with no manual type selection
- **Sticky action bar** always visible at the bottom: Upload Discharge Summary, Upload Diagnostic Report, Upload Another File
- **Live progress steps**: Reading PDF → Extracting text → AI clinical extraction → Building FHIR bundle → Validating
- **Results panel**: Valid/Invalid badge, resource type summary, full JSON viewer with copy and download
- Only **errors** are shown in the validation panel — warnings and info suppressed to reduce noise

---

## API Reference

### `POST /convert`

| Parameter | Type | Description |
|---|---|---|
| `files` | `File[]` | One or more PDF documents |
| `use_case` | `enum` | `claim_submission` (default) |
| `patient_id` | `string?` | ABHA ID / MRN (auto-generated if absent) |
| `claim_id` | `string?` | Claim reference number (auto-generated if absent) |
| `validate` | `bool` | Run NHCX profile validation (default: `true`) |

**Response:**

```json
{
  "request_id": "uuid",
  "use_case": "claim_submission",
  "documents_processed": 2,
  "hi_types_detected": ["DischargeSummary", "DiagnosticReport"],
  "fhir_bundle": { ... },
  "validation_report": {
    "valid": true,
    "total_issues": 0,
    "errors": [],
    "warnings": [],
    "information": []
  }
}
```

### `POST /validate`

Accepts an existing FHIR Bundle JSON body. Returns a `ValidationReport`.

### `GET /health`

Returns `{"status": "healthy", "timestamp": "..."}`.

### `GET /`

Returns service info, version, supported HI types, and supported use cases.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | LLM backend: `anthropic` \| `openai` \| `gemini` |
| `ANTHROPIC_API_KEY` | — | Required when using Anthropic (default) |
| `ANTHROPIC_MODEL` | `claude-opus-4-6` | Anthropic model name |
| `OPENAI_API_KEY` | — | Required when `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model name |
| `GEMINI_API_KEY` | — | Required when `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL` | `gemini-1.5-pro` | Gemini model name |
| `PORT` | `8000` | Service port |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Extraction Quality — Clinical Accuracy

The LLM extraction prompt enforces 10 critical rules:

1. **Gender** — extracted from M/F/Male/Female anywhere in the document; never defaults to "unknown" if stated
2. **Dates** — all dates converted to ISO 8601 (DD/MM/YYYY → YYYY-MM-DD); never substitutes today's date
3. **Hospital name** — verbatim from letterhead (e.g. "Punjab Institute of Medical Sciences")
4. **Doctor name** — from signature block, Doctor In-Charge field, or follow-up instructions (e.g. "Dr. Jatinder")
5. **Diagnosis** — actual clinical diagnosis only (e.g. "Diabetic Ketoacidosis"), never invented or substituted
6. **ICD-10 codes** — accurately mapped: DKA → E10.1, T1DM → E10.9, T2DM → E11.9, Ascites → R18, IHD → I25.9, Hypertension → I10, Pneumonia → J18.9
7. **Investigations** — all lab values with results and LOINC codes (CBC → 58410-2, HbA1c → 4548-4, RBS → 2345-7)
8. **Medications** — all discharge medicines with dose, route, frequency, and duration
9. **Payer type** — Ayushman Bharat, CGHS, Self, etc.
10. **Department** — treating department (Pediatrics, Cardiology, etc.)

---

## Validation

`validator.py` runs 4 tiers against every generated bundle:

1. **Structural** — required FHIR R4 fields, resource types, cardinality
2. **Profile** — ABDM/NHCX-specific rules (e.g. HCXClaimBundle must contain a `Claim` resource)
3. **Terminology** — coding system URI well-formedness (LOINC, SNOMED, ICD-10, UCUM)
4. **Business** — `Claim` must reference a clinical document; `Patient` must be present in every bundle

Returns a `ValidationReport` with `valid` bool, `errors[]`, `warnings[]`, and `information[]`.

For full HL7 conformance certification in production, also run the official HAPI FHIR validator:

```bash
java -jar validator_cli.jar bundle.json \
  -ig https://nrces.in/ndhm/fhir/r4 \
  -ig https://ig.hcxprotocol.io/v0.8
```

Or use the online validator at [https://validator.fhir.org](https://validator.fhir.org).

---

## Running Tests

```bash
pytest test_converter.py -v
```

Tests cover: classifier HI type detection, FHIR bundle structure, all resource types present, LOINC codes on DiagnosticReport, patient gender, `urn:uuid:` fullUrl format, observation value presence, and validator pass/fail cases.

---

## Real-World Examples

### PIMS Discharge Summary — Diabetic Ketoacidosis

A discharge slip from Punjab Institute of Medical Sciences, Jalandhar for a female pediatric patient with Type 1 Diabetes Mellitus / DKA produces:

- **Patient**: gender = female, department = Pediatrics
- **Encounter**: admission = 2024-12-24, discharge = 2025-01-03 (10 days, ICU)
- **Conditions**: DKA (E10.1), Type 1 DM (E10.9), Severe Ascitis (R18)
- **Investigations**: RBS 490 mg/dL, HB 13.8 GM/DL, TLC 18660/CMM, Platelets 5.19 Lacs, ABG pH 7.28 / PCO2 23 / HCO3 10, Urine Ketones positive
- **Medications**: INJ NOVARAPID 10U SC 8hrly (15 min before meal), INJ GILARCIN 15U SC HS
- **Organization**: Punjab Institute of Medical Sciences, Garha Road, Jalandhar
- **Practitioner**: Dr. Jatinder, Pediatrics Department

### 2D Echo Report — Ischemic Heart Disease

Generated from All is Well Multi Speciality Hospital, Burhanpur (31/03/2025) — **14 FHIR resources**:

- 9× Observation: LA 28mm, AO 19mm, IVS-d 10mm, LVPW-d 11mm, LVID-d 41mm, LVID-s 30mm, RA 4C 29mm, RVID-d 28mm, LVEF 40–45% (flagged abnormal)
- 1× DiagnosticReport: LOINC 34552-0, conclusion = IHD, RWMA, Moderately Compromised LV Systolic Function
- 1× Claim: ICD-10 I25.9, institutional type, DiagnosticReport as supportingInfo

---

## Extending the Service

### Add a new HI Type

1. Add keyword signals in `classifier.py → _SIGNALS`
2. Add an extraction prompt in `extractor.py`
3. Add a builder method in `fhir_builder.py`
4. Add profile validation rules in `validator.py`

### Swap LLM provider at runtime

```bash
export LLM_PROVIDER=openai   # no code changes needed
```

---

## License

MIT License — free to use, modify, and distribute.

---

## References

- [ABDM FHIR Implementation Guide (NRCeS)](https://nrces.in/ndhm/fhir/r4)
- [NHCX FHIR IG v0.8](https://ig.hcxprotocol.io/v0.8)
- [HAPI FHIR Validator](https://hapifhir.io/hapi-fhir/docs/validation/)
- [HL7 FHIR R4 Specification](https://hl7.org/fhir/R4/)
- [Online FHIR Validator](https://validator.fhir.org)
