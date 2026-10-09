"""
PDF Extractor — v5  (PRODUCTION-GRADE)
------------------------------------------
Covers all extraction gaps identified in bug reports plus enhanced prompts
for complete clinical data extraction.

Changes from v4:
  - OPENAI_MODEL default changed to gpt-4o-mini (as requested)
  - DIAGNOSTIC_REPORT_PROMPT: stricter date rules, all APTT/Blood Group/PT-INR sections,
    comments, method, structured ref ranges enforced via explicit examples
  - DISCHARGE_SUMMARY_PROMPT: birth_date derivation, multi-consultant, admission time,
    bed/room/panel, vitals split, exam findings, hospital course verbatim,
    in-hospital meds with route/dose/freq/duration, billing line items,
    follow-up appointments, emergency certificate, physio/diet/respiratory care
  - Added _post_validate() to catch common extraction failures before returning
  - Both prompts now return null for missing fields (never fabricate)

Provider: OpenAI | Gemini | Anthropic (via LLM_PROVIDER env var, default: anthropic)
"""

import io
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()

# ── Diagnostic Report prompt ──────────────────────────────────────────────────

DIAGNOSTIC_REPORT_PROMPT = """\
You are a clinical data extraction expert. Extract ALL structured information from the
diagnostic report text below and return ONLY valid JSON — no markdown fences, no preamble.

════════════════════════════════════════════════════════
CRITICAL RULES — NEVER BREAK THESE
════════════════════════════════════════════════════════
1. DATES — ISO 8601 YYYY-MM-DD ONLY.
   Use ONLY dates that appear in the document — NEVER use today's date.
   The report_date, specimen.collected_date, and every observation.date
   must ALL be derived from dates actually printed on the document.

2. LOINC CODES — Use real numeric LOINC codes. NEVER use the word "LOINC" as a code value.
   Common examples:
     Hemoglobin      → 718-7     HCT/PCV       → 4544-3    RBC          → 789-8
     WBC/TLC         → 6690-2   Platelets      → 777-3     Neutrophil   → 770-8
     Lymphocyte      → 731-0    MCV            → 787-2     MCH          → 785-6
     MCHC            → 786-4    RDW            → 788-0     ESR          → 30341-2
     PT              → 5902-2   INR            → 6301-6    APTT         → 3173-2
     Fibrinogen      → 3255-7   Blood Group    → 882-7     Glucose      → 2345-7
     Creatinine      → 2160-0   BUN/Urea       → 3094-0    ALT/SGPT     → 1742-6
     AST/SGOT        → 1920-8   ALP            → 6768-6    Bilirubin T  → 1975-2
     Protein Total   → 2885-2   Albumin        → 1751-7    Sodium       → 2951-2
     Potassium       → 2823-3   Calcium        → 17861-6   TSH          → 3016-3
     Dengue NS1      → 75377-7  CRP            → 1988-5    Troponin I   → 42757-5
     NT-proBNP       → 33762-6  Vitamin D      → 14635-7   Vitamin B12  → 2132-9
     Urine Routine   → 24356-8  Urine Culture  → 630-2     Blood Culture→ 600-5
     Malaria Ag      → 32700-7  CBC panel      → 58410-2   LFT panel    → 24325-3
     ECG             → 11524-6  Echo 2D        → 34552-0   Chest X-ray  → 24748-6

3. CATEGORY — "LAB" for blood/urine/biochemistry/serology; "RAD" for imaging/ECG/Echo/EEG/NCV.

4. REFERENCE RANGES — Extract numeric ref_low and ref_high separately.
   Example: "12-15 g/dL" → ref_low=12, ref_high=15, reference_range="12-15 g/dL"

5. METHOD — Extract test methodology if stated (e.g. "Electrical impedance and flow cytometry").

6. SPECIMEN — Extract specimen type (blood/urine/serum/plasma) and collection date from document.

7. CAPTURE ALL SECTIONS — If document contains multiple report sections
   (CBC + PT/INR + APTT + Blood Group + any others), capture EVERY parameter
   across ALL sections as separate observation entries.

8. COMMENTS/CONCLUSION — Populate conclusion with any PBS comments or clinical notes.

9. NEVER FABRICATE — If a field is not present in the document, return null.

════════════════════════════════════════════════════════
RETURN THIS EXACT JSON SCHEMA (null for missing):
════════════════════════════════════════════════════════
{
  "report_date": "YYYY-MM-DD or null",
  "report_type": "e.g. Complete Blood Count",
  "report_category": "LAB|RAD",
  "patient": {
    "name": "string or null",
    "age": "string or null",
    "sex": "male|female|other|null",
    "uhid": "string or null",
    "mobile": "string or null"
  },
  "referring_doctor": "string or null",
  "hospital": {
    "name": "string or null",
    "address": "string or null"
  },
  "specimen": {
    "type": "blood|urine|serum|plasma|string or null",
    "collected_date": "YYYY-MM-DD or null"
  },
  "observations": [
    {
      "code": "real LOINC numeric code e.g. 718-7  — NEVER the word LOINC",
      "display": "parameter name e.g. Hemoglobin",
      "value": "numeric or string value",
      "unit": "unit string e.g. g/dL",
      "interpretation": "normal|abnormal|high|low|critical|null",
      "ref_low": numeric lower bound or null,
      "ref_high": numeric upper bound or null,
      "reference_range": "full text e.g. 12-15 g/dL or null",
      "method": "test method if stated or null",
      "date": "YYYY-MM-DD from document or null"
    }
  ],
  "impression": ["list of finding strings"],
  "conclusion": "free-text conclusion or PBS comments or null",
  "comments": "additional clinical notes or null",
  "performing_doctor": "string or null",
  "loinc_code": "LOINC code for overall study type or null",
  "loinc_display": "LOINC display text or null"
}

TEXT:
"""

# ── Discharge Summary prompt ──────────────────────────────────────────────────

DISCHARGE_SUMMARY_PROMPT = """\
You are a clinical data extraction expert. Extract ALL structured information from the
discharge summary and any attached bills/certificates below.
Return ONLY valid JSON — no markdown fences, no preamble.

════════════════════════════════════════════════════════
CRITICAL RULES — NEVER BREAK THESE
════════════════════════════════════════════════════════
1. DATES — ISO 8601 only.  Include TIME when present:  "2025-09-09T16:55:00"
   Use dates ONLY from the document — never fabricate.

2. GENDER — "male" | "female" | "other" only (lowercase).

3. HOSPITAL NAME — verbatim from letterhead.

4. CONSULTANTS — list ALL doctors found in the document (signature block, header, etc.)
   with name, qualification, specialty.

5. DIAGNOSIS — actual clinical diagnosis text from document.
   Separate primary from secondary diagnoses.
   ICD-10 examples:
     Left lower limb cellulitis  → L03.116    Dengue fever          → A90
     HTN                         → I10        Old MCA infarct       → I69.351
     Necrotizing fasciitis       → M72.6      Thrombocytopenia      → D69.6
     Mixed sensorimotor neuropathy → G62.9    AFI                   → A99
     UTI (E.coli)                → N39.0      Polyarthralgia        → M79.3

6. VITALS — split BP "189/89 mmHg" → bp_systolic=189, bp_diastolic=89 (numerics only, no units).
   If BP missing from vitals_on_admission but present on emergency certificate, use that value.

7. MOBILE — extract patient mobile/phone number if anywhere in the document.

8. ADDRESS — extract full patient address.

9. BED/ROOM/WARD — capture bed number, room name, ward from document.

10. BIRTH_DATE — derive from age if possible:
    "72 y 5 m 29 d" admitted 25-09-2025 → birth_date ≈ "1952" (year only is acceptable).
    If a full DOB is present, use it.

11. HOSPITAL COURSE — copy the hospital course / clinical narrative VERBATIM as a single string.

12. EXAMINATION FINDINGS — capture CNS, CVS, Chest, Abdomen/P/A, Local Examination findings.

13. IN-HOSPITAL MEDICATIONS — ALL medicines given during admission with:
    name, dose, route (IV/oral/IM/SC/topical/inhaled), frequency, duration.
    Parse "1-0-1" as twice daily, "1-1-1" as three times daily.

14. DISCHARGE MEDICATIONS — ALL medicines at discharge with same structure.

15. BILLING — extract EVERY line item from summary bill or detailed bill with:
    description, amount (numeric), quantity (numeric), date.

16. EMERGENCY CERTIFICATE — if present, capture date, doctor, diagnosis, BP, pulse, temperature.

17. FOLLOW-UP — capture structured appointments: department, doctor, date, days_after_discharge, note.

18. PHYSIOTHERAPY, DIET, RESPIRATORY CARE — extract if present.

19. DISCHARGE CONDITION — "improved" | "stable" | "deceased" | "against_advice" | "recovered" | null.

20. NEVER FABRICATE — null for anything not in the document.

════════════════════════════════════════════════════════
RETURN THIS EXACT JSON SCHEMA (null for missing, [] for empty lists):
════════════════════════════════════════════════════════
{
  "patient": {
    "name": "string or null",
    "age": "string or null",
    "sex": "male|female|other|null",
    "uhid": "string or null",
    "abha_id": "string or null",
    "mobile": "string or null",
    "address": "string or null",
    "birth_date": "YYYY or YYYY-MM-DD or null"
  },
  "admission": {
    "date": "YYYY-MM-DD or null",
    "time": "HH:MM or null",
    "datetime": "YYYY-MM-DDTHH:MM:SS or null",
    "ward": "string or null",
    "bed": "string or null",
    "room": "string or null",
    "ip_number": "string or null"
  },
  "discharge": {
    "date": "YYYY-MM-DD or null",
    "time": "HH:MM or null",
    "datetime": "YYYY-MM-DDTHH:MM:SS or null",
    "condition": "improved|stable|deceased|against_advice|recovered|null",
    "disposition": "home|transfer|deceased|against_advice|null"
  },
  "hospital": {
    "name": "string or null",
    "address": "string or null",
    "phone": "string or null"
  },
  "attending_doctor": "string or null",
  "consultants": [
    {
      "name": "string",
      "qualification": "string or null",
      "specialty": "string or null"
    }
  ],
  "department": "string or null",
  "payer_type": "CGHS|CAPF|AYUSHMAN|SELF|string or null",
  "chief_complaints": ["list of complaint strings"],
  "history": "string or null",
  "allergies": [
    {
      "substance": "string",
      "reaction": "string or null",
      "severity": "mild|moderate|severe|null"
    }
  ],
  "past_medical_history": [
    {
      "condition": "string",
      "icd10": "string or null",
      "status": "active|resolved|null"
    }
  ],
  "diagnosis": {
    "primary": "string or null",
    "secondary": ["list of secondary diagnosis strings"],
    "icd10_codes": ["codes in order: primary first, then secondary"]
  },
  "procedures": [
    {
      "name": "string",
      "date": "YYYY-MM-DD or null",
      "snomed_code": "string or null",
      "icd10_pcs": "string or null",
      "surgeon": "string or null",
      "anesthetist": "string or null"
    }
  ],
  "investigations": [
    {
      "name": "string",
      "result": "string or null",
      "unit": "string or null",
      "date": "YYYY-MM-DD or null",
      "loinc_code": "real LOINC numeric code or null",
      "interpretation": "normal|abnormal|high|low|critical|null",
      "ref_low": "numeric or null",
      "ref_high": "numeric or null",
      "reference_range": "text or null",
      "method": "string or null"
    }
  ],
  "vitals_on_admission": {
    "bp_systolic": "numeric string or null",
    "bp_diastolic": "numeric string or null",
    "pulse": "numeric string or null",
    "spo2": "numeric string or null",
    "temperature": "numeric string or null",
    "rr": "numeric string or null",
    "rbs": "numeric string or null"
  },
  "vitals_at_discharge": {
    "bp_systolic": "numeric string or null",
    "bp_diastolic": "numeric string or null",
    "pulse": "numeric string or null",
    "spo2": "numeric string or null",
    "temperature": "numeric string or null"
  },
  "examination_findings": {
    "CNS": "string or null",
    "CVS": "string or null",
    "Chest": "string or null",
    "Abdomen": "string or null",
    "Local_Examination": "string or null"
  },
  "hospital_course": "full verbatim hospital course narrative or null",
  "in_hospital_medications": [
    {
      "name": "string",
      "dose": "string or null",
      "route": "IV|oral|IM|SC|topical|inhaled|null",
      "frequency": "string or null",
      "duration": "string or null"
    }
  ],
  "medications_at_discharge": [
    {
      "name": "string",
      "dose": "string or null",
      "route": "IV|oral|IM|SC|topical|inhaled|null",
      "frequency": "string or null",
      "duration": "string or null"
    }
  ],
  "billing_items": [
    {
      "description": "string",
      "amount": "numeric or null",
      "quantity": "numeric or null",
      "date": "YYYY-MM-DD or null"
    }
  ],
  "investigations_summary": "string or null",
  "follow_up": "string or null",
  "follow_up_appointments": [
    {
      "department": "string or null",
      "doctor": "string or null",
      "date": "YYYY-MM-DD or null",
      "days_after_discharge": "string or null",
      "note": "string or null"
    }
  ],
  "diet_advice": "string or null",
  "physiotherapy_advice": "string or null",
  "respiratory_care": "string or null",
  "care_plan": "string or null",
  "emergency_certificate": {
    "date": "YYYY-MM-DD or null",
    "doctor": "string or null",
    "diagnosis": "string or null",
    "bp": "string or null",
    "pulse": "string or null",
    "temperature": "string or null"
  }
}

TEXT:
"""


# ── LLM callers ───────────────────────────────────────────────────────────────

def _call_openai(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    # Default changed to gpt-4o-mini as per deployment requirement.
    # Override via OPENAI_MODEL env var.
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a clinical data extraction assistant. "
                    "Respond with valid JSON only. Never wrap in markdown fences. "
                    "Never use today's date — only dates from the document."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=4096,
        temperature=0,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content.strip()


def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))
    model = genai.GenerativeModel(
        os.getenv("GEMINI_MODEL", "gemini-1.5-pro"),
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0,
            max_output_tokens=4096,
        ),
    )
    return model.generate_content(prompt).text.strip()


def _call_anthropic(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=(
            "You are a clinical data extraction assistant. "
            "Respond with valid JSON only. Never wrap in markdown fences. "
            "Never use today's date — only dates found in the document."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def _call_llm(prompt: str) -> str:
    logger.info("Using LLM provider: %s", LLM_PROVIDER)
    if LLM_PROVIDER == "openai":
        return _call_openai(prompt)
    elif LLM_PROVIDER == "gemini":
        return _call_gemini(prompt)
    elif LLM_PROVIDER == "anthropic":
        return _call_anthropic(prompt)
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER='{LLM_PROVIDER}'. Choose: openai|gemini|anthropic"
        )


def _parse_json(raw: str) -> Dict[str, Any]:
    """Strip any accidental markdown fences then parse JSON."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"```\s*$", "", cleaned.strip())
    return json.loads(cleaned)


# ── Post-validation helpers ────────────────────────────────────────────────────

_LOINC_PLACEHOLDER = re.compile(r"^loinc$", re.I)
_TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fix_loinc_placeholders(result: Dict[str, Any]) -> None:
    """Replace any 'LOINC' placeholder codes with empty string so the builder
    will fall back to its own lookup table."""
    for obs in result.get("observations", []):
        code = str(obs.get("code", ""))
        if _LOINC_PLACEHOLDER.match(code.strip()) or code.strip().upper() == "LOINC":
            obs["code"] = ""
            logger.warning("Replaced LOINC placeholder for obs: %s", obs.get("display"))

    for inv in result.get("investigations", []):
        code = str(inv.get("loinc_code", "") or "")
        if _LOINC_PLACEHOLDER.match(code.strip()) or code.strip().upper() == "LOINC":
            inv["loinc_code"] = None


def _fix_today_dates(result: Dict[str, Any]) -> None:
    """Warn and nullify any dates that are today (likely hallucinated)."""

    def _check(d: Optional[str], field: str) -> Optional[str]:
        if isinstance(d, str) and d.startswith(_TODAY):
            logger.warning("Removing hallucinated today-date in field '%s': %s", field, d)
            return None
        return d

    result["report_date"] = _check(result.get("report_date"), "report_date")

    spec = result.get("specimen", {})
    if isinstance(spec, dict):
        spec["collected_date"] = _check(spec.get("collected_date"), "specimen.collected_date")

    for obs in result.get("observations", []):
        obs["date"] = _check(obs.get("date"), "obs.date")

    adm = result.get("admission", {})
    if isinstance(adm, dict):
        for k in ("date", "datetime"):
            adm[k] = _check(adm.get(k), f"admission.{k}")

    dc = result.get("discharge", {})
    if isinstance(dc, dict):
        for k in ("date", "datetime"):
            dc[k] = _check(dc.get(k), f"discharge.{k}")


def _fix_bp_split(result: Dict[str, Any]) -> None:
    """If bp_systolic/diastolic are missing but a combined bp string exists, split it."""
    for vitals_key in ("vitals_on_admission", "vitals_at_discharge"):
        vitals = result.get(vitals_key, {})
        if not isinstance(vitals, dict):
            continue
        if vitals.get("bp_systolic") is None and vitals.get("bp") is not None:
            bp_str = str(vitals["bp"])
            m = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", bp_str)
            if m:
                vitals["bp_systolic"] = m.group(1)
                vitals["bp_diastolic"] = m.group(2)


def _ensure_lists(result: Dict[str, Any], keys: List[str]) -> None:
    for k in keys:
        if result.get(k) is None:
            result[k] = []
        elif not isinstance(result[k], list):
            result[k] = [result[k]]


def _post_validate(result: Dict[str, Any], hi_type: str) -> Dict[str, Any]:
    """Apply all post-extraction corrections."""
    _fix_loinc_placeholders(result)
    _fix_today_dates(result)
    _fix_bp_split(result)

    if hi_type == "DiagnosticReport":
        _ensure_lists(result, ["observations", "impression"])

    if hi_type == "DischargeSummary":
        _ensure_lists(
            result,
            [
                "chief_complaints",
                "consultants",
                "allergies",
                "past_medical_history",
                "procedures",
                "investigations",
                "in_hospital_medications",
                "medications_at_discharge",
                "billing_items",
                "follow_up_appointments",
            ],
        )

        # Ensure diagnosis dict is well-formed
        dx = result.setdefault("diagnosis", {})
        dx.setdefault("primary", None)
        dx.setdefault("secondary", [])
        dx.setdefault("icd10_codes", [])

        # Ensure patient sub-keys
        pat = result.setdefault("patient", {})
        for k in ("name", "age", "sex", "uhid", "abha_id", "mobile", "address", "birth_date"):
            pat.setdefault(k, None)

    return result


# ── Extractor ─────────────────────────────────────────────────────────────────


class PDFExtractor:
    """PDF → raw text → structured JSON pipeline (v5 — production-grade)."""

    # ── Text extraction ───────────────────────────────────────────────────────

    def extract_text(self, pdf_bytes: bytes) -> str:
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for page in doc:
                text = page.get_text("text")
                if text.strip():
                    pages.append(text.strip())
                else:
                    pages.append(self._ocr_page(page))
            doc.close()
            full = "\n".join(pages)
            logger.info("Extracted %d chars from PDF via PyMuPDF", len(full))
            return full
        except ImportError:
            logger.warning("PyMuPDF not installed; trying pdfplumber fallback.")
            return self._fallback_extract(pdf_bytes)

    def _ocr_page(self, page: Any) -> str:
        try:
            import pytesseract
            from PIL import Image

            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return pytesseract.image_to_string(img, lang="eng")
        except Exception as exc:
            logger.warning("OCR failed on page: %s", exc)
            return ""

    def _fallback_extract(self, pdf_bytes: bytes) -> str:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            logger.info("Extracted %d chars via pdfplumber", len(text))
            return text
        except Exception as exc:
            raise RuntimeError(f"Cannot extract PDF text: {exc}") from exc

    # ── Structured extraction ─────────────────────────────────────────────────

    def extract_structured(self, text: str, hi_type: str) -> Dict[str, Any]:
        """Send extracted text to LLM and return validated structured JSON."""
        if hi_type == "DiagnosticReport":
            prompt = DIAGNOSTIC_REPORT_PROMPT + text
        elif hi_type == "DischargeSummary":
            prompt = DISCHARGE_SUMMARY_PROMPT + text
        else:
            prompt = (
                "Extract all clinical information as structured JSON. "
                "Return ONLY valid JSON — no markdown fences.\n\n" + text
            )

        try:
            raw = _call_llm(prompt)
            result = _parse_json(raw)
            result = _post_validate(result, hi_type)
            logger.info(
                "Extraction complete: %d top-level fields, %d observations",
                len(result),
                len(result.get("observations", result.get("investigations", []))),
            )
            return result
        except json.JSONDecodeError as exc:
            logger.error("LLM returned non-JSON: %s", exc)
            return {"_raw_text": text, "_extraction_error": str(exc)}
        except Exception as exc:
            logger.error("LLM extraction failed: %s", exc)
            raise RuntimeError(f"Structured extraction failed: {exc}") from exc
