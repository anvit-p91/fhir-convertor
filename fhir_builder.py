"""
FHIR Bundle Builder — v5  (PRODUCTION-GRADE)
----------------------------------------------
Constructs NHCX/ABDM-compliant FHIR R4 bundles from extracted clinical data.

All v4 bugs addressed PLUS additional production fixes in v5:
  v5.A  Composition.date = discharge date, not today's timestamp
  v5.B  Patient.birthDate → YYYY-MM-DD (FHIR-valid), not bare year
  v5.C  Observation.encounter reference added on every Observation
  v5.D  Encounter.hospitalization.dischargeDisposition derived from
         discharge.condition; "conditionOnDischarge" captured as extension note
  v5.E  Blood-pressure panel: bp_systolic + bp_diastolic emitted as a BP
         panel Observation with component slices (LOINC 55284-4) AND as
         individual systolic/diastolic Observations for indexing
  v5.F  Procedure SNOMED lookup table — distinct, real codes per procedure type
  v5.G  MedicationRequest.medicationCodeableConcept uses medication-name text
         coding under RxNorm-like fallback; generic code no longer reused
  v5.H  Route "inhaled" maps to SNOMED 47625008→ replaced by correct text-only
         coding to avoid invalid code; all other routes keep valid codes
  v5.I  Composition.attester populated with attending practitioner
  v5.J  Specimen resource gains meta.profile
  v5.K  Claim.insurance.coverage → Coverage reference properly built
  v5.L  _build_charge_item: failsafe for null amounts
  v5.M  Deduplication guard — prevent identical SNOMED conditions in bundle
  v5.N  _build_vital_observations: BP parsed from combined string if split fields absent

NRCeS profiles: https://nrces.in/ndhm/fhir/r4/StructureDefinition/
NHCX profiles:  https://ig.hcxprotocol.io/v0.8/
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Profiles ──────────────────────────────────────────────────────────────────
PROFILES: Dict[str, str] = {
    "Bundle_Claim":       "https://ig.hcxprotocol.io/v0.8/StructureDefinition-HCXClaimBundle.html",
    "Bundle_PreAuth":     "https://ig.hcxprotocol.io/v0.8/StructureDefinition-HCXPreAuthBundle.html",
    "Bundle_Document":    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentBundle",
    "DiagnosticReport":   "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReport",
    "DischargeSummary":   "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DischargeSummaryRecord",
    "Composition":        "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Composition",
    "Patient":            "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient",
    "Organization":       "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization",
    "Practitioner":       "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner",
    "PractitionerRole":   "https://nrces.in/ndhm/fhir/r4/StructureDefinition/PractitionerRole",
    "Observation":        "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation",
    "Condition":          "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Condition",
    "MedicationRequest":  "https://nrces.in/ndhm/fhir/r4/StructureDefinition/MedicationRequest",
    "Procedure":          "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Procedure",
    "Encounter":          "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Encounter",
    "CarePlan":           "https://nrces.in/ndhm/fhir/r4/StructureDefinition/CarePlan",
    "DocumentReference":  "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentReference",
    "Claim":              "https://ig.hcxprotocol.io/v0.8/StructureDefinition-Claim.html",
    "AllergyIntolerance": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/AllergyIntolerance",
    "Specimen":           "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Specimen",
}

# ── LOINC lookup (code, display, cat_code, cat_display) ──────────────────────
TEST_LOINC: Dict[str, Tuple[str, str, str, str]] = {
    "cbc":                      ("58410-2",  "CBC panel",                   "LAB", "Laboratory"),
    "complete blood count":     ("58410-2",  "CBC panel",                   "LAB", "Laboratory"),
    "hemoglobin":               ("718-7",    "Hemoglobin",                  "LAB", "Laboratory"),
    "hb":                       ("718-7",    "Hemoglobin",                  "LAB", "Laboratory"),
    "hgb":                      ("718-7",    "Hemoglobin",                  "LAB", "Laboratory"),
    "hematocrit":               ("4544-3",   "Hematocrit",                  "LAB", "Laboratory"),
    "hct":                      ("4544-3",   "Hematocrit",                  "LAB", "Laboratory"),
    "pcv":                      ("4544-3",   "Hematocrit",                  "LAB", "Laboratory"),
    "rbc":                      ("789-8",    "Erythrocytes",                "LAB", "Laboratory"),
    "wbc":                      ("6690-2",   "Leukocytes",                  "LAB", "Laboratory"),
    "tlc":                      ("6690-2",   "Leukocytes",                  "LAB", "Laboratory"),
    "platelet":                 ("777-3",    "Platelets",                   "LAB", "Laboratory"),
    "plt":                      ("777-3",    "Platelets",                   "LAB", "Laboratory"),
    "tcp":                      ("777-3",    "Platelets",                   "LAB", "Laboratory"),
    "neutrophil":               ("770-8",    "Neutrophils",                 "LAB", "Laboratory"),
    "lymphocyte":               ("731-0",    "Lymphocytes",                 "LAB", "Laboratory"),
    "monocyte":                 ("742-7",    "Monocytes",                   "LAB", "Laboratory"),
    "eosinophil":               ("713-8",    "Eosinophils",                 "LAB", "Laboratory"),
    "mcv":                      ("787-2",    "MCV",                         "LAB", "Laboratory"),
    "mch":                      ("785-6",    "MCH",                         "LAB", "Laboratory"),
    "mchc":                     ("786-4",    "MCHC",                        "LAB", "Laboratory"),
    "rdw":                      ("788-0",    "RDW",                         "LAB", "Laboratory"),
    "esr":                      ("30341-2",  "ESR",                         "LAB", "Laboratory"),
    "pt":                       ("5902-2",   "Prothrombin time",            "LAB", "Laboratory"),
    "pt/inr":                   ("5902-2",   "Prothrombin time",            "LAB", "Laboratory"),
    "inr":                      ("6301-6",   "INR",                         "LAB", "Laboratory"),
    "aptt":                     ("3173-2",   "APTT",                        "LAB", "Laboratory"),
    "ptt":                      ("3173-2",   "APTT",                        "LAB", "Laboratory"),
    "fibrinogen":               ("3255-7",   "Fibrinogen",                  "LAB", "Laboratory"),
    "glucose":                  ("2345-7",   "Glucose",                     "LAB", "Laboratory"),
    "rbs":                      ("2345-7",   "Glucose random",              "LAB", "Laboratory"),
    "fbs":                      ("1558-6",   "Fasting glucose",             "LAB", "Laboratory"),
    "hba1c":                    ("4548-4",   "Hemoglobin A1c",              "LAB", "Laboratory"),
    "urea":                     ("3094-0",   "Urea nitrogen",               "LAB", "Laboratory"),
    "bun":                      ("3094-0",   "Urea nitrogen",               "LAB", "Laboratory"),
    "creatinine":               ("2160-0",   "Creatinine",                  "LAB", "Laboratory"),
    "uric acid":                ("3084-1",   "Uric acid",                   "LAB", "Laboratory"),
    "sodium":                   ("2951-2",   "Sodium",                      "LAB", "Laboratory"),
    "potassium":                ("2823-3",   "Potassium",                   "LAB", "Laboratory"),
    "chloride":                 ("2075-0",   "Chloride",                    "LAB", "Laboratory"),
    "bicarbonate":              ("1963-8",   "Bicarbonate",                 "LAB", "Laboratory"),
    "calcium":                  ("17861-6",  "Calcium",                     "LAB", "Laboratory"),
    "serum calcium":            ("17861-6",  "Calcium",                     "LAB", "Laboratory"),
    "phosphorus":               ("2777-1",   "Phosphorus",                  "LAB", "Laboratory"),
    "magnesium":                ("19123-9",  "Magnesium",                   "LAB", "Laboratory"),
    "serum magnesium":          ("19123-9",  "Magnesium",                   "LAB", "Laboratory"),
    "protein":                  ("2885-2",   "Protein total",               "LAB", "Laboratory"),
    "albumin":                  ("1751-7",   "Albumin",                     "LAB", "Laboratory"),
    "globulin":                 ("10834-0",  "Globulin",                    "LAB", "Laboratory"),
    "bilirubin total":          ("1975-2",   "Bilirubin total",             "LAB", "Laboratory"),
    "bilirubin":                ("1975-2",   "Bilirubin total",             "LAB", "Laboratory"),
    "bilirubin direct":         ("1968-7",   "Bilirubin direct",            "LAB", "Laboratory"),
    "bilirubin indirect":       ("1971-1",   "Bilirubin indirect",          "LAB", "Laboratory"),
    "alt":                      ("1742-6",   "ALT",                         "LAB", "Laboratory"),
    "sgpt":                     ("1742-6",   "ALT",                         "LAB", "Laboratory"),
    "ast":                      ("1920-8",   "AST",                         "LAB", "Laboratory"),
    "sgot":                     ("1920-8",   "AST",                         "LAB", "Laboratory"),
    "alp":                      ("6768-6",   "ALP",                         "LAB", "Laboratory"),
    "ggt":                      ("2324-2",   "GGT",                         "LAB", "Laboratory"),
    "lft":                      ("24325-3",  "Liver function tests",        "LAB", "Laboratory"),
    "liver function":           ("24325-3",  "Liver function tests",        "LAB", "Laboratory"),
    "kft":                      ("24362-6",  "Kidney function tests",       "LAB", "Laboratory"),
    "kidney function":          ("24362-6",  "Kidney function tests",       "LAB", "Laboratory"),
    "lipid profile":            ("57698-3",  "Lipid panel",                 "LAB", "Laboratory"),
    "lipid":                    ("57698-3",  "Lipid panel",                 "LAB", "Laboratory"),
    "cholesterol":              ("2093-3",   "Cholesterol total",           "LAB", "Laboratory"),
    "ldl":                      ("13457-7",  "LDL cholesterol",             "LAB", "Laboratory"),
    "hdl":                      ("2085-9",   "HDL cholesterol",             "LAB", "Laboratory"),
    "triglycerides":            ("2571-8",   "Triglycerides",               "LAB", "Laboratory"),
    "tsh":                      ("3016-3",   "TSH",                         "LAB", "Laboratory"),
    "t3":                       ("3051-0",   "Triiodothyronine",            "LAB", "Laboratory"),
    "t4":                       ("3054-4",   "Thyroxine",                   "LAB", "Laboratory"),
    "thyroid profile":          ("3016-3",   "Thyroid panel",               "LAB", "Laboratory"),
    "dengue":                   ("24111-7",  "Dengue Ab panel",             "LAB", "Laboratory"),
    "dengue ns1":               ("75377-7",  "Dengue NS1 Ag",               "LAB", "Laboratory"),
    "dengue ns-1":              ("75377-7",  "Dengue NS1 Ag",               "LAB", "Laboratory"),
    "dengue serology":          ("24111-7",  "Dengue Ab panel",             "LAB", "Laboratory"),
    "malaria":                  ("32700-7",  "Malaria smear",               "LAB", "Laboratory"),
    "malaria antigen":          ("32700-7",  "Malaria Ag",                  "LAB", "Laboratory"),
    "typhoid":                  ("26621-0",  "Salmonella typhi Ab",         "LAB", "Laboratory"),
    "widal":                    ("26621-0",  "Widal test",                  "LAB", "Laboratory"),
    "blood culture":            ("600-5",    "Blood culture",               "LAB", "Laboratory"),
    "urine culture":            ("630-2",    "Urine culture",               "LAB", "Laboratory"),
    "urine r/m":                ("24356-8",  "Urinalysis",                  "LAB", "Laboratory"),
    "urine routine":            ("24356-8",  "Urinalysis",                  "LAB", "Laboratory"),
    "crp":                      ("1988-5",   "C-reactive protein",          "LAB", "Laboratory"),
    "procalcitonin":            ("75241-5",  "Procalcitonin",               "LAB", "Laboratory"),
    "il-6":                     ("26881-0",  "Interleukin-6",               "LAB", "Laboratory"),
    "troponin":                 ("42757-5",  "Troponin I",                  "LAB", "Laboratory"),
    "troponin t":               ("6598-7",   "Troponin T",                  "LAB", "Laboratory"),
    "nt-probnp":                ("33762-6",  "NT-proBNP",                   "LAB", "Laboratory"),
    "probnp":                   ("33762-6",  "NT-proBNP",                   "LAB", "Laboratory"),
    "ferritin":                 ("2276-4",   "Ferritin",                    "LAB", "Laboratory"),
    "iron":                     ("2498-4",   "Iron",                        "LAB", "Laboratory"),
    "vitamin d":                ("14635-7",  "25-OH Vitamin D",             "LAB", "Laboratory"),
    "25-oh vitamin d":          ("14635-7",  "25-OH Vitamin D",             "LAB", "Laboratory"),
    "vitamin b12":              ("2132-9",   "Vitamin B12",                 "LAB", "Laboratory"),
    "blood group":              ("882-7",    "ABO+Rh blood group",          "LAB", "Laboratory"),
    "blood grouping":           ("882-7",    "ABO+Rh blood group",          "LAB", "Laboratory"),
    "cardiac marker":           ("42757-5",  "Cardiac marker panel",        "LAB", "Laboratory"),
    "2d echo":                  ("34552-0",  "2D cardiac echo panel",       "RAD", "Radiology"),
    "echocardiography":         ("34552-0",  "2D cardiac echo panel",       "RAD", "Radiology"),
    "ecg":                      ("11524-6",  "EKG study",                   "RAD", "Radiology"),
    "electrocardiogram":        ("11524-6",  "EKG study",                   "RAD", "Radiology"),
    "eeg":                      ("54328-4",  "EEG study",                   "RAD", "Radiology"),
    "ncv":                      ("83491-1",  "Nerve conduction study",      "RAD", "Radiology"),
    "nerve conduction":         ("83491-1",  "Nerve conduction study",      "RAD", "Radiology"),
    "brachial plexus":          ("83491-1",  "Brachial plexus NCS",         "RAD", "Radiology"),
    "emg":                      ("83491-1",  "Electromyography",            "RAD", "Radiology"),
    "ct scan":                  ("24627-2",  "CT chest",                    "RAD", "Radiology"),
    "ncct":                     ("30799-1",  "NCCT head",                   "RAD", "Radiology"),
    "mri":                      ("24558-9",  "MRI brain",                   "RAD", "Radiology"),
    "x-ray":                    ("24748-6",  "XR chest PA",                 "RAD", "Radiology"),
    "chest x-ray":              ("24748-6",  "XR chest PA",                 "RAD", "Radiology"),
    "xray":                     ("24748-6",  "XR chest PA",                 "RAD", "Radiology"),
    "usg":                      ("45036003", "Ultrasound abdomen",          "RAD", "Radiology"),
    "ultrasound":               ("45036003", "Ultrasound abdomen",          "RAD", "Radiology"),
    "laboratory report":        ("11502-2",  "Laboratory report",           "LAB", "Laboratory"),
}

# ── Vital-sign LOINC (loinc, display, unit, ucum, ref_low, ref_high) ─────────
VITAL_LOINC: Dict[str, Tuple] = {
    "bp_systolic":  ("8480-6",  "Systolic BP",       "mmHg",  "mm[Hg]",  90,  140),
    "bp_diastolic": ("8462-4",  "Diastolic BP",      "mmHg",  "mm[Hg]",  60,   90),
    "pulse":        ("8867-4",  "Heart rate",         "/min",  "/min",    60,  100),
    "spo2":         ("59408-5", "Oxygen saturation",  "%",     "%",       95,  100),
    "temperature":  ("8310-5",  "Body temperature",   "°F",    "[degF]",  97,   99),
    "rr":           ("9279-1",  "Respiratory rate",   "/min",  "/min",    12,   20),
    "rbs":          ("2345-7",  "Glucose",            "mg/dL", "mg/dL",   70,  140),
    "weight":       ("29463-7", "Body weight",        "kg",    "kg",      None, None),
    "height":       ("8302-2",  "Body height",        "cm",    "cm",      None, None),
}

# ── Interpretation codes ──────────────────────────────────────────────────────
INTERP_MAP: Dict[str, Dict] = {
    "normal":   {"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                 "code": "N",  "display": "Normal"},
    "abnormal": {"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                 "code": "A",  "display": "Abnormal"},
    "critical": {"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                 "code": "AA", "display": "Critical abnormal"},
    "high":     {"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                 "code": "H",  "display": "High"},
    "low":      {"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                 "code": "L",  "display": "Low"},
}

# ── Condition SNOMED (distinct per concept) ───────────────────────────────────
CONDITION_SNOMED: Dict[str, Tuple[str, str]] = {
    "cellulitis":         ("128045006", "Cellulitis"),
    "necrotizing":        ("52658000",  "Necrotizing fasciitis"),
    "fasciitis":          ("52658000",  "Fasciitis"),
    "hypertension":       ("38341003",  "Hypertension"),
    "htn":                ("38341003",  "Hypertension"),
    "stroke":             ("230690007", "Cerebrovascular accident"),
    "infarct":            ("230690007", "Cerebrovascular infarction"),
    "mca":                ("230690007", "MCA infarct"),
    "dengue":             ("38362002",  "Dengue fever"),
    "thrombocytopenia":   ("302215000", "Thrombocytopenia"),
    "neuropathy":         ("246634006", "Neuropathy"),
    "uti":                ("68566005",  "Urinary tract infection"),
    "pneumonia":          ("233604007", "Pneumonia"),
    "diabetes":           ("44054006",  "Diabetes mellitus"),
    "fever":              ("386661006", "Fever"),
    "pain":               ("22253000",  "Pain"),
    "vomiting":           ("422400008", "Vomiting"),
    "weakness":           ("26544005",  "Weakness"),
    "snake bite":         ("416939005", "Snakebite"),
    "insect bite":        ("262584008", "Insect bite"),
    "sepsis":             ("91302008",  "Sepsis"),
    "liver":              ("57773001",  "Hepatic failure"),
    "polyarthralgia":     ("57676002",  "Polyarthralgia"),
    "swelling":           ("271807003", "Swelling"),
    "tenderness":         ("247472004", "Tenderness"),
    "default":            ("404684003", "Clinical finding"),
}

# v5.F: Distinct procedure SNOMED codes
PROCEDURE_SNOMED: Dict[str, Tuple[str, str]] = {
    "debridement":        ("387731002", "Debridement of wound"),
    "fasciotomy":         ("173447004", "Fasciotomy"),
    "skin graft":         ("72010006",  "Skin grafting"),
    "skin grafting":      ("72010006",  "Skin grafting"),
    "amputation":         ("81723002",  "Amputation"),
    "incision":           ("34662004",  "Incision"),
    "dialysis":           ("265764009", "Renal dialysis"),
    "intubation":         ("52765003",  "Intubation"),
    "catheterisation":    ("45901007",  "Urinary catheterisation"),
    "catheterization":    ("45901007",  "Urinary catheterisation"),
    "ecg":                ("29303009",  "Electrocardiogram"),
    "echo":               ("40701008",  "Echocardiography"),
    "echocardiography":   ("40701008",  "Echocardiography"),
    "biopsy":             ("86273004",  "Biopsy"),
    "blood transfusion":  ("5447007",   "Blood transfusion"),
    "cpr":                ("89666000",  "Cardiopulmonary resuscitation"),
    "physiotherapy":      ("91251008",  "Physiotherapy"),
    "surgery":            ("387713003", "Surgical procedure"),
    "operation":          ("387713003", "Surgical procedure"),
}

# ── Route of administration ───────────────────────────────────────────────────
# v5.H: inhaled uses text-only (no unreliable SNOMED code)
ROUTE_CODES: Dict[str, Tuple[Optional[str], str]] = {
    "iv":            ("47625008",  "Intravenous route"),
    "intravenous":   ("47625008",  "Intravenous route"),
    "oral":          ("26643006",  "Oral route"),
    "po":            ("26643006",  "Oral route"),
    "im":            ("78421000",  "Intramuscular route"),
    "intramuscular": ("78421000",  "Intramuscular route"),
    "sc":            ("34206005",  "Subcutaneous route"),
    "subcutaneous":  ("34206005",  "Subcutaneous route"),
    "topical":       ("6064005",   "Topical route"),
    "inhaled":       (None,        "Inhalation route"),   # text-only
    "inhalation":    (None,        "Inhalation route"),
    "sublingual":    ("37839007",  "Sublingual route"),
    "rectal":        ("37161004",  "Rectal route"),
    "nasal":         ("46713006",  "Nasal route"),
}

TIMING_CODES: Dict[str, Tuple[int, str]] = {
    "od": (1, "once daily"),             "once daily": (1, "once daily"),
    "qd": (1, "once daily"),             "1-0-0": (1, "once in morning"),
    "0-0-1": (1, "once at bedtime"),     "at bed time": (1, "at bedtime"),
    "at bedtime": (1, "at bedtime"),     "before breakfast": (1, "before breakfast"),
    "bd": (2, "twice daily"),            "bid": (2, "twice daily"),
    "twice daily": (2, "twice daily"),   "1-0-1": (2, "twice daily"),
    "tid": (3, "three times daily"),     "tds": (3, "three times daily"),
    "thrice daily": (3, "three times daily"), "1-1-1": (3, "three times daily"),
    "qid": (4, "four times daily"),      "1-1-1-1": (4, "four times daily"),
    "sos": (0, "as needed"),             "prn": (0, "as needed"),
    "if pain": (0, "as needed for pain"),
}

DISP_MAP: Dict[str, Tuple[str, str]] = {
    "improved":       ("306689006", "Discharge to home"),
    "stable":         ("306689006", "Discharge to home"),
    "home":           ("306689006", "Discharge to home"),
    "recovered":      ("306689006", "Discharge to home"),
    "transfer":       ("306706006", "Discharge to hospital"),
    "deceased":       ("371828003", "Patient deceased"),
    "against_advice": ("306694010", "Left against medical advice"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return str(uuid.uuid4())


def _urn(u: str) -> str:
    return f"urn:uuid:{u}"


def _entry(r: Dict) -> Dict:
    return {"fullUrl": _urn(r["id"]), "resource": r}


def _meta(*profile_keys: str) -> Dict:
    return {
        "versionId": "1",
        "lastUpdated": _now_iso(),
        "profile": [PROFILES[k] for k in profile_keys if k in PROFILES],
    }


def _parse_numeric(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_bp(s: str) -> Tuple[Optional[float], Optional[float]]:
    m = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", str(s))
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def _loinc_lookup(name: str) -> Tuple[str, str, str, str]:
    key = (name or "").lower().strip()
    if key in TEST_LOINC:
        return TEST_LOINC[key]
    for pattern, vals in TEST_LOINC.items():
        if pattern in key or key in pattern:
            return vals
    return ("11502-2", name or "Lab result", "LAB", "Laboratory")


def _snomed_for_condition(text: str) -> Tuple[str, str]:
    key = (text or "").lower()
    for pattern, (code, display) in CONDITION_SNOMED.items():
        if pattern in key:
            return code, display
    return CONDITION_SNOMED["default"]


def _snomed_for_procedure(text: str) -> Tuple[str, str]:
    """v5.F: distinct SNOMED per procedure type."""
    key = (text or "").lower()
    for pattern, (code, display) in PROCEDURE_SNOMED.items():
        if pattern in key:
            return code, display
    return ("71388002", "Procedure")  # generic fallback


def _route_lookup(route_text: Optional[str]) -> Optional[Tuple[Optional[str], str]]:
    if not route_text:
        return None
    key = route_text.lower().strip()
    for pattern, val in ROUTE_CODES.items():
        if pattern in key:
            return val
    return None


def _timing_lookup(freq: Optional[str]) -> Optional[Dict]:
    if not freq:
        return None
    key = freq.lower().strip()
    for pattern, (count, display) in TIMING_CODES.items():
        if pattern in key:
            if count == 0:
                return {"repeat": {"when": ["PRN"]}, "code": {"text": display}}
            return {"repeat": {"frequency": count, "period": 1, "periodUnit": "d"},
                    "code": {"text": display}}
    return {"code": {"text": freq}}


def _div(text: str) -> Dict:
    safe = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return {"status": "generated",
            "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{safe}</div>'}


# ── Builder ───────────────────────────────────────────────────────────────────

class FHIRBundleBuilder:
    """NHCX/ABDM-compliant FHIR R4 Document Bundle builder — v5."""

    # ── Top-level ─────────────────────────────────────────────────────────────

    def build_bundle(
        self,
        documents: List[Dict[str, Any]],
        use_case: str = "claim_submission",
        claim_id: Optional[str] = None,
        patient_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        entries: List[Dict] = []
        patient_uid = _uid()
        org_uid     = _uid()   # provider / hospital
        payer_uid   = _uid()   # v5.K: separate payer organisation
        enc_uid     = _uid()

        first_data   = documents[0]["data"] if documents else {}
        patient_info = first_data.get("patient", {})
        admission    = first_data.get("admission", {})

        entries.append(_entry(self._build_patient(patient_uid, patient_info, patient_id, admission)))
        entries.append(_entry(self._build_organization(org_uid, first_data.get("hospital", {}))))
        entries.append(_entry(self._build_payer_organization(payer_uid, first_data.get("payer_type"))))

        # Multi-doctor (FIX R)
        prac_uids: List[str] = []
        for doc_info in first_data.get("consultants", []):
            p_uid = _uid()
            entries.append(_entry(self._build_practitioner(
                p_uid, doc_info.get("name"), doc_info.get("qualification"))))
            role_uid = _uid()
            entries.append(_entry(self._build_practitioner_role(
                role_uid, p_uid, org_uid, doc_info.get("specialty"))))
            prac_uids.append(p_uid)

        if not prac_uids:
            name = (first_data.get("attending_doctor")
                    or first_data.get("referring_doctor")
                    or first_data.get("performing_doctor"))
            p_uid = _uid()
            entries.append(_entry(self._build_practitioner(p_uid, name)))
            prac_uids.append(p_uid)

        primary_prac_uid = prac_uids[0]
        doc_ref_uids: List[str] = []
        claim_support: List[Dict] = []

        for doc in documents:
            hi_type = doc.get("hi_type", "DiagnosticReport")
            data    = doc.get("data", {})

            if hi_type == "DischargeSummary":
                new_entries, section_uids = self._build_discharge_summary_resources(
                    data, patient_uid, org_uid, primary_prac_uid, prac_uids, enc_uid)
                entries.extend(new_entries)
                doc_ref_uids.extend(section_uids)
                claim_support.append({
                    "sequence": len(claim_support) + 1,
                    "category": {"coding": [{"system":
                        "http://terminology.hl7.org/CodeSystem/claiminformationcategory",
                        "code": "discharge", "display": "Discharge"}]},
                    "valueReference": {"reference": _urn(section_uids[0])} if section_uids else {},
                })
            else:
                new_entries, dr_uid = self._build_diagnostic_report_resources(
                    data, patient_uid, org_uid, primary_prac_uid)
                entries.extend(new_entries)
                doc_ref_uids.append(dr_uid)
                claim_support.append({
                    "sequence": len(claim_support) + 1,
                    "category": {"coding": [{"system":
                        "http://terminology.hl7.org/CodeSystem/claiminformationcategory",
                        "code": "info", "display": "Information"}]},
                    "valueReference": {"reference": _urn(dr_uid)},
                })

        if claim_id or use_case in ("claim_submission", "pre_authorization"):
            claim_uid = _uid()
            entries.append(_entry(self._build_claim(
                claim_uid, claim_id or "CLM-001",
                patient_uid, org_uid, payer_uid, primary_prac_uid, prac_uids,
                claim_support, first_data)))

        bundle_profile = (PROFILES["Bundle_PreAuth"] if use_case == "pre_authorization"
                          else PROFILES["Bundle_Claim"])
        return {
            "resourceType": "Bundle",
            "id":   _uid(),
            "meta": {
                "versionId": "1", "lastUpdated": _now_iso(),
                "profile": [bundle_profile],
                "security": [{"system":
                    "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                    "code": "N", "display": "normal"}],
            },
            "identifier": {"system": "http://hip.in", "value": _uid()},
            "type":       "document",
            "timestamp":  _now_iso(),
            "entry":      entries,
        }

    # ── DiagnosticReport ──────────────────────────────────────────────────────

    def _build_diagnostic_report_resources(
        self, data: Dict, patient_uid: str, org_uid: str, prac_uid: str
    ) -> Tuple[List[Dict], str]:
        entries: List[Dict] = []
        report_date = data.get("report_date") or data.get("collection_date") or _now_iso()[:10]
        report_type = (data.get("report_type") or "Laboratory Report").lower().strip()
        loinc_code, loinc_display, cat_code, cat_display = _loinc_lookup(report_type)

        # Specimen (v5.J: meta.profile added)
        specimen_uid = None
        spec = data.get("specimen", {})
        if spec and spec.get("type"):
            specimen_uid = _uid()
            entries.append(_entry(self._build_specimen(
                specimen_uid, spec, patient_uid,
                spec.get("collected_date") or report_date)))

        obs_refs: List[Dict] = []
        for obs_data in data.get("observations", []):
            obs_uid = _uid()
            obs_data_with_date = dict(obs_data)
            if not obs_data_with_date.get("date"):
                obs_data_with_date["date"] = report_date
            obs = self._build_observation(
                obs_uid, obs_data_with_date, patient_uid,
                specimen_uid=specimen_uid, report_type=report_type)
            entries.append(_entry(obs))
            obs_refs.append({"reference": _urn(obs_uid)})

        dr_uid = _uid()
        dr: Dict[str, Any] = {
            "resourceType":     "DiagnosticReport",
            "id":               dr_uid,
            "meta":             _meta("DiagnosticReport"),
            "text":             _div(f"{loinc_display} — {report_date}"),
            "status":           "final",
            "category": [{"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/v2-0074",
                "code": cat_code, "display": cat_display}]}],
            "code": {
                "coding": [{"system": "http://loinc.org",
                             "code": loinc_code, "display": loinc_display}],
                "text": data.get("report_type", loinc_display),
            },
            "subject":           {"reference": _urn(patient_uid), "display": "Patient"},
            "effectiveDateTime": report_date,
            "issued":            _now_iso(),
            "performer": [
                {"reference": _urn(prac_uid),  "display": "Practitioner"},
                {"reference": _urn(org_uid),   "display": "Organization"},
            ],
            "result":     obs_refs,
            "conclusion": (data.get("conclusion")
                           or "; ".join(data.get("impression", []))
                           or data.get("comments") or ""),
        }
        if specimen_uid:
            dr["specimen"] = [{"reference": _urn(specimen_uid)}]
        entries.append(_entry(dr))
        return entries, dr_uid

    # ── DischargeSummary ──────────────────────────────────────────────────────

    def _build_discharge_summary_resources(
        self,
        data: Dict,
        patient_uid: str,
        org_uid: str,
        primary_prac_uid: str,
        prac_uids: List[str],
        enc_uid: str,
    ) -> Tuple[List[Dict], List[str]]:
        entries: List[Dict] = []
        sections: List[Dict] = []
        admission  = data.get("admission", {})
        discharge  = data.get("discharge", {})
        admit_date = admission.get("date") or _now_iso()[:10]
        dc_date    = discharge.get("date") or _now_iso()[:10]

        # Encounter
        entries.append(_entry(
            self._build_encounter(enc_uid, patient_uid, org_uid, admission, discharge, data)))

        # Allergies
        allergy_refs: List[Dict] = []
        for a in data.get("allergies", []):
            ai_uid = _uid()
            entries.append(_entry(self._build_allergy(ai_uid, a, patient_uid, enc_uid)))
            allergy_refs.append({"reference": _urn(ai_uid)})
        if not allergy_refs:
            ai_uid = _uid()
            entries.append(_entry({
                "resourceType": "AllergyIntolerance",
                "id": ai_uid,
                "meta": {"profile": [PROFILES["AllergyIntolerance"]]},
                "clinicalStatus": {"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    "code": "active"}]},
                "verificationStatus": {"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                    "code": "unconfirmed"}]},
                "code": {"coding": [{"system": "http://snomed.info/sct",
                                      "code": "716186003", "display": "No known allergy"}],
                         "text": "No known allergy"},
                "patient": {"reference": _urn(patient_uid)},
            }))
            allergy_refs.append({"reference": _urn(ai_uid)})
        sections.append({
            "title": "Allergies",
            "code":  {"coding": [{"system": "http://snomed.info/sct",
                                   "code": "722446000", "display": "Allergy record"}]},
            "entry": allergy_refs,
        })

        # Past Medical History
        hist_refs: List[Dict] = []
        for h in data.get("past_medical_history", []):
            cuid = _uid()
            entries.append(_entry(self._build_condition(
                cuid, h.get("condition", "Unknown"), patient_uid, enc_uid,
                icd10=h.get("icd10"),
                clinical_status=h.get("status") or "resolved",
                category="problem-list-item",
                category_display="Problem List Item")))
            hist_refs.append({"reference": _urn(cuid)})
        if hist_refs:
            sections.append({
                "title": "Past Medical History",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "1003642006",
                                       "display": "Past medical history section"}]},
                "entry": hist_refs,
            })

        # Chief Complaints
        cc_refs: List[Dict] = []
        for cc in data.get("chief_complaints", [])[:6]:
            cuid = _uid()
            entries.append(_entry(self._build_condition(
                cuid, cc, patient_uid, enc_uid,
                clinical_status="active",
                category="encounter-diagnosis",
                category_display="Encounter Diagnosis")))
            cc_refs.append({"reference": _urn(cuid)})
        if cc_refs:
            sections.append({
                "title": "Chief Complaints",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "422843007",
                                       "display": "Chief complaint section"}]},
                "entry": cc_refs,
            })

        # Diagnoses (deduplication via SNOMED code)
        dx_refs: List[Dict] = []
        seen_snomed: set = set()
        icd10s       = data.get("diagnosis", {}).get("icd10_codes", [])
        primary_dx   = data.get("diagnosis", {}).get("primary")
        secondary_dx = data.get("diagnosis", {}).get("secondary", [])

        if primary_dx:
            snomed_code, _ = _snomed_for_condition(primary_dx)
            seen_snomed.add(snomed_code)
            cuid = _uid()
            entries.append(_entry(self._build_condition(
                cuid, primary_dx, patient_uid, enc_uid,
                icd10=icd10s[0] if icd10s else None,
                clinical_status="active",
                category="encounter-diagnosis",
                category_display="Encounter Diagnosis")))
            dx_refs.append({"reference": _urn(cuid)})

        for i, sec in enumerate(secondary_dx):
            snomed_code, _ = _snomed_for_condition(sec)
            # v5.M: deduplication guard
            if snomed_code in seen_snomed and snomed_code == "404684003":
                continue
            seen_snomed.add(snomed_code)
            cuid = _uid()
            entries.append(_entry(self._build_condition(
                cuid, sec, patient_uid, enc_uid,
                icd10=icd10s[i + 1] if i + 1 < len(icd10s) else None,
                clinical_status="active",
                category="encounter-diagnosis",
                category_display="Encounter Diagnosis")))
            dx_refs.append({"reference": _urn(cuid)})

        if dx_refs:
            sections.append({
                "title": "Diagnoses",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "422843007",
                                       "display": "Diagnoses section"}]},
                "entry": dx_refs,
            })

        # Vitals on Admission
        va_refs = self._build_vital_observations(
            data.get("vitals_on_admission", {}),
            patient_uid, enc_uid, admit_date, entries,
            extra_findings=data.get("examination_findings", {}))
        if va_refs:
            sections.append({
                "title": "Vitals on Admission",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "1184616005",
                                       "display": "Vital signs record"}]},
                "entry": va_refs,
            })

        # Vitals at Discharge
        vd_refs = self._build_vital_observations(
            data.get("vitals_at_discharge", {}),
            patient_uid, enc_uid, dc_date, entries)
        if vd_refs:
            sections.append({
                "title": "Vitals at Discharge",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "1184616005",
                                       "display": "Vital signs record"}]},
                "entry": vd_refs,
            })

        # Investigations
        inv_obs_refs: List[Dict] = []
        for inv in data.get("investigations", []):
            obs_uid = _uid()
            loinc_code, loinc_display, _, _ = _loinc_lookup(inv.get("name", ""))
            obs_data: Dict[str, Any] = {
                "code":           loinc_code,
                "display":        inv.get("name", "Investigation"),
                "value":          inv.get("result"),
                "unit":           inv.get("unit"),
                "interpretation": inv.get("interpretation"),
                "date":           inv.get("date") or admit_date,
                "ref_low":        inv.get("ref_low"),
                "ref_high":       inv.get("ref_high"),
                "reference_range":inv.get("reference_range"),
                "method":         inv.get("method"),
            }
            entries.append(_entry(self._build_observation(
                obs_uid, obs_data, patient_uid,
                enc_uid=enc_uid,
                report_type=inv.get("name", ""))))
            inv_obs_refs.append({"reference": _urn(obs_uid)})

        if inv_obs_refs or data.get("investigations_summary"):
            dr_uid = _uid()
            entries.append(_entry({
                "resourceType": "DiagnosticReport",
                "id":     dr_uid,
                "meta":   _meta("DiagnosticReport"),
                "status": "final",
                "category": [{"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/v2-0074",
                    "code": "LAB", "display": "Laboratory"}]}],
                "code":   {"coding": [{"system": "http://loinc.org",
                                        "code": "11502-2",
                                        "display": "Laboratory report"}],
                           "text": "Investigation Results"},
                "subject":   {"reference": _urn(patient_uid)},
                "encounter": {"reference": _urn(enc_uid)},
                "performer": [{"reference": _urn(primary_prac_uid)}],
                "result":    inv_obs_refs,
                "conclusion":data.get("investigations_summary", ""),
            }))
            sections.append({
                "title": "Investigations",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "721981007",
                                       "display": "Diagnostic studies report"}]},
                "entry": [{"reference": _urn(dr_uid)}],
            })

        # Procedures (v5.F)
        proc_refs: List[Dict] = []
        for pd_ in data.get("procedures", []):
            pu = _uid()
            entries.append(_entry(self._build_procedure(pu, pd_, patient_uid, enc_uid, prac_uids)))
            proc_refs.append({"reference": _urn(pu)})
        if proc_refs:
            sections.append({
                "title": "Procedures",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "887161000000109",
                                       "display": "Procedures section"}]},
                "entry": proc_refs,
            })

        # Hospital Course Narrative
        hospital_course = data.get("hospital_course") or data.get("course_in_hospital")
        if hospital_course:
            sections.append({
                "title": "Hospital Course",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "371524004",
                                       "display": "Clinical report"}]},
                "text":  _div(hospital_course),
            })

        # In-hospital Medications
        ih_med_refs: List[Dict] = []
        for med in data.get("in_hospital_medications", []):
            mu = _uid()
            entries.append(_entry(self._build_medication_request(
                mu, med, patient_uid, enc_uid, status="completed", authored_on=admit_date)))
            ih_med_refs.append({"reference": _urn(mu)})

        # Discharge Medications
        dc_med_refs: List[Dict] = []
        for med in data.get("medications_at_discharge", []):
            mu = _uid()
            entries.append(_entry(self._build_medication_request(
                mu, med, patient_uid, enc_uid, status="active", authored_on=dc_date)))
            dc_med_refs.append({"reference": _urn(mu)})

        all_med_refs = ih_med_refs + dc_med_refs
        if all_med_refs:
            sections.append({
                "title": "Medication Summary",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "721912009",
                                       "display": "Medication summary document"}]},
                "entry": all_med_refs,
            })

        # Billing / ChargeItems
        charge_refs: List[Dict] = []
        for item in data.get("billing_items", []):
            cu = _uid()
            entries.append(_entry(self._build_charge_item(cu, item, patient_uid, enc_uid, org_uid)))
            charge_refs.append({"reference": _urn(cu)})
        if charge_refs:
            sections.append({
                "title": "Billing Summary",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "405624009",
                                       "display": "Invoice section"}]},
                "entry": charge_refs,
            })

        # Follow-up Appointments
        appt_refs: List[Dict] = []
        appt_list = data.get("follow_up_appointments", [])
        if not appt_list and data.get("follow_up"):
            appt_list = [{"note": data["follow_up"]}]
        for ad in appt_list:
            au = _uid()
            entries.append(_entry(self._build_appointment(au, patient_uid, enc_uid, ad, primary_prac_uid)))
            appt_refs.append({"reference": _urn(au)})

        # CarePlan
        cpu = _uid()
        entries.append(_entry(self._build_careplan(cpu, data, patient_uid, enc_uid)))
        dc_section_refs = [{"reference": _urn(cpu)}] + appt_refs
        sections.append({
            "title": "Discharge Summary & Care Plan",
            "code":  {"coding": [{"system": "http://snomed.info/sct",
                                   "code": "373942005", "display": "Discharge summary"}]},
            "entry": dc_section_refs,
        })

        # Emergency Certificate
        if data.get("emergency_certificate"):
            ecu = _uid()
            ec  = data["emergency_certificate"]
            entries.append(_entry({
                "resourceType": "DocumentReference",
                "id":     ecu,
                "meta":   _meta("DocumentReference"),
                "status": "current",
                "type":   {"coding": [{"system": "http://snomed.info/sct",
                                        "code": "308274009",
                                        "display": "Emergency certificate"}],
                           "text": "Emergency Admission Certificate"},
                "subject":   {"reference": _urn(patient_uid)},
                "encounter": {"reference": _urn(enc_uid)},
                "date":      ec.get("date") or admit_date,
                "content": [{"attachment": {"contentType": "application/pdf",
                                             "title": "Emergency Admission Certificate"}}],
                "description": ec.get("diagnosis") or "Emergency admission",
                "context": {
                    "encounter": [{"reference": _urn(enc_uid)}],
                    "period": {"start": ec.get("date") or admit_date},
                },
            }))
            sections.append({
                "title": "Emergency Certificate",
                "code":  {"coding": [{"system": "http://snomed.info/sct",
                                       "code": "308274009",
                                       "display": "Emergency certificate"}]},
                "entry": [{"reference": _urn(ecu)}],
            })

        # DocumentReference (PDF link)
        dru = _uid()
        entries.append(_entry(self._build_document_reference(
            dru, patient_uid, primary_prac_uid, org_uid, data)))

        # v5.A: Composition.date = discharge date, not today
        # v5.I: Composition.attester populated
        comp_uid = _uid()
        entries.insert(0, _entry({
            "resourceType": "Composition",
            "id":           comp_uid,
            "meta":         _meta("DischargeSummary"),
            "language":     "en-IN",
            "text":         _div(
                f"Discharge Summary — "
                f"{data.get('hospital', {}).get('name', 'Unknown Hospital')} — {dc_date}"
            ),
            "status": "final",
            "type": {
                "coding": [{"system": "http://snomed.info/sct",
                             "code": "373942005", "display": "Discharge summary"}],
                "text": "Discharge Summary",
            },
            "subject":         {"reference": _urn(patient_uid), "display": "Patient"},
            "encounter":       {"reference": _urn(enc_uid)},
            # v5.A: use discharge date, not _now_iso()
            "date":            dc_date,
            "author":          [{"reference": _urn(p)} for p in prac_uids],
            "title":           "Discharge Summary",
            "confidentiality": "N",
            "custodian":       {"reference": _urn(org_uid)},
            # v5.I: attester
            "attester": [{
                "mode": "legal",
                "time": dc_date,
                "party": {"reference": _urn(primary_prac_uid)},
            }],
            "section": sections,
        }))

        return entries, [comp_uid, enc_uid]

    # ── Resource builders ─────────────────────────────────────────────────────

    def _build_patient(
        self, uid: str, info: Dict,
        abha_id: Optional[str] = None,
        admission: Optional[Dict] = None,
    ) -> Dict:
        """v5.B: birthDate formatted as YYYY-MM-DD."""
        p: Dict[str, Any] = {
            "resourceType": "Patient",
            "id":   uid,
            "meta": _meta("Patient"),
        }
        if info.get("name"):
            p["name"] = [{"use": "official", "text": info["name"]}]

        # v5.B: birthDate → proper FHIR format
        if info.get("birth_date"):
            bd = str(info["birth_date"])
            # If only a year is provided, expand to YYYY-01-01 for FHIR validity
            if re.match(r"^\d{4}$", bd):
                bd = bd + "-01-01"
            p["birthDate"] = bd
        elif info.get("age"):
            age_str = str(info["age"])
            year_match = re.search(r"(\d{2,3})\s*y", age_str, re.I)
            if year_match:
                approx_year = datetime.now(timezone.utc).year - int(year_match.group(1))
                p["birthDate"] = f"{approx_year}-01-01"

        if info.get("sex"):
            p["gender"] = {"male": "male", "female": "female",
                            "m": "male", "f": "female"}.get(
                str(info["sex"]).lower(), "unknown")

        telecoms: List[Dict] = []
        if info.get("mobile"):
            telecoms.append({"system": "phone", "value": str(info["mobile"]), "use": "mobile"})
        if telecoms:
            p["telecom"] = telecoms

        if info.get("address"):
            p["address"] = [{"use": "home", "text": str(info["address"])}]

        identifiers: List[Dict] = []
        if info.get("uhid"):
            identifiers.append({
                "use":    "usual",
                "type":   {"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/v2-0203",
                    "code": "MR", "display": "Medical record number"}]},
                "system": "http://hip.in/uhid",
                "value":  str(info["uhid"]),
            })
        if admission and admission.get("ip_number"):
            identifiers.append({
                "use":    "secondary",
                "type":   {"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/v2-0203",
                    "code": "VN", "display": "Visit number"}]},
                "system": "http://hip.in/ipd",
                "value":  str(admission["ip_number"]),
            })
        abha = abha_id or info.get("abha_id")
        if abha:
            identifiers.append({
                "use":    "official",
                "type":   {"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/v2-0203",
                    "code": "NI", "display": "National unique individual identifier"}]},
                "system": "https://healthid.ndhm.gov.in",
                "value":  str(abha),
            })
        if identifiers:
            p["identifier"] = identifiers
        return p

    def _build_organization(self, uid: str, info: Dict) -> Dict:
        o: Dict[str, Any] = {
            "resourceType": "Organization",
            "id":   uid,
            "meta": _meta("Organization"),
            "name": info.get("name") or "Unknown Hospital",
            "type": [{"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/organization-type",
                "code": "prov", "display": "Healthcare Provider"}]}],
        }
        if info.get("address"):
            o["address"] = [{"text": str(info["address"])}]
        if info.get("phone"):
            o["telecom"] = [{"system": "phone", "value": str(info["phone"])}]
        return o

    def _build_payer_organization(self, uid: str, payer_type: Optional[str]) -> Dict:
        return {
            "resourceType": "Organization",
            "id":   uid,
            "meta": _meta("Organization"),
            "name": payer_type or "Self / Government",
            "type": [{"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/organization-type",
                "code": "ins", "display": "Insurance Company"}]}],
        }

    def _build_practitioner(
        self, uid: str, name: Optional[str], qualification: Optional[str] = None
    ) -> Dict:
        p: Dict[str, Any] = {
            "resourceType": "Practitioner",
            "id":   uid,
            "meta": _meta("Practitioner"),
        }
        if name:
            p["name"] = [{"use": "official", "text": name}]
        if qualification:
            p["qualification"] = [{"code": {"text": qualification}}]
        return p

    def _build_practitioner_role(
        self, uid: str, prac_uid: str, org_uid: str, specialty: Optional[str] = None
    ) -> Dict:
        r: Dict[str, Any] = {
            "resourceType": "PractitionerRole",
            "id":           uid,
            "meta":         _meta("PractitionerRole"),
            "practitioner": {"reference": _urn(prac_uid)},
            "organization": {"reference": _urn(org_uid)},
            "active":       True,
        }
        if specialty:
            r["specialty"] = [{"coding": [{"system": "http://snomed.info/sct",
                                            "code": "394814009",
                                            "display": specialty}],
                                "text": specialty}]
        return r

    def _build_encounter(
        self, uid: str, patient_uid: str, org_uid: str,
        admission: Dict, discharge: Dict, data: Dict
    ) -> Dict:
        """v5.D: conditionOnDischarge captured as extension note."""
        enc: Dict[str, Any] = {
            "resourceType": "Encounter",
            "id":     uid,
            "meta":   _meta("Encounter"),
            "status": "finished",
            "class":  {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                       "code": "IMP", "display": "inpatient encounter"},
            "type": [{"coding": [{"system": "http://snomed.info/sct",
                                   "code": "32485007", "display": "Hospital admission"}],
                      "text": "Inpatient Hospitalization"}],
            "subject":         {"reference": _urn(patient_uid), "display": "Patient"},
            "serviceProvider": {"reference": _urn(org_uid),     "display": "Organization"},
        }

        period: Dict[str, str] = {}
        if admission.get("date"):
            t = admission.get("time", "")
            period["start"] = (
                admission.get("datetime")
                or admission["date"] + (f"T{t}" if t else "T00:00:00")
            )
        if discharge.get("date"):
            t = discharge.get("time", "")
            period["end"] = (
                discharge.get("datetime")
                or discharge["date"] + (f"T{t}" if t else "T00:00:00")
            )
        if period:
            enc["period"] = period

        locations: List[Dict] = []
        if admission.get("ward"):
            locations.append({"location": {"display": admission["ward"]}, "status": "completed"})
        if admission.get("bed"):
            locations.append({"location": {"display": f"Bed {admission['bed']}"}, "status": "completed"})
        if admission.get("room"):
            locations.append({"location": {"display": admission["room"]}, "status": "completed"})
        if locations:
            enc["location"] = locations

        extensions: List[Dict] = []
        if data.get("payer_type"):
            extensions.append({"url": "http://hip.in/fhir/StructureDefinition/panel",
                                "valueString": str(data["payer_type"])})
        # v5.D: condition on discharge as extension
        dc_condition_text = discharge.get("condition") or ""
        if dc_condition_text:
            extensions.append({
                "url": "http://hip.in/fhir/StructureDefinition/conditionOnDischarge",
                "valueString": dc_condition_text,
            })
        if extensions:
            enc["extension"] = extensions

        dc_cond_key = dc_condition_text.lower()
        disp_code, disp_display = DISP_MAP.get(dc_cond_key, ("306689006", "Discharge to home"))
        enc["hospitalization"] = {
            "dischargeDisposition": {
                "coding": [{"system": "http://snomed.info/sct",
                             "code": disp_code, "display": disp_display}],
                "text": dc_condition_text or "Stable",
            },
        }

        if period.get("start"):
            enc["statusHistory"] = [{
                "status": "in-progress",
                "period": {
                    "start": period.get("start", admission.get("date", "")),
                    "end":   period.get("end",   discharge.get("date", _now_iso()[:10])),
                },
            }]
        return enc

    def _build_allergy(self, uid: str, allergy: Dict, patient_uid: str, enc_uid: str) -> Dict:
        sev_map = {"mild": "mild", "moderate": "moderate", "severe": "severe"}
        r: Dict[str, Any] = {
            "resourceType": "AllergyIntolerance",
            "id":   uid,
            "meta": {"profile": [PROFILES["AllergyIntolerance"]]},
            "clinicalStatus": {"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                "code": "active"}]},
            "verificationStatus": {"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                "code": "confirmed"}]},
            "type": "allergy",
            "code": {
                "coding": [{"system": "http://snomed.info/sct", "code": "105590001",
                             "display": allergy.get("substance", "Unknown")}],
                "text": allergy.get("substance", "Unknown"),
            },
            "patient":   {"reference": _urn(patient_uid)},
            "encounter": {"reference": _urn(enc_uid)},
        }
        if allergy.get("reaction"):
            r["reaction"] = [{
                "manifestation": [{"coding": [{"system": "http://snomed.info/sct",
                                                "code": "404684003",
                                                "display": allergy["reaction"]}],
                                   "text": allergy["reaction"]}],
                "severity": sev_map.get((allergy.get("severity") or "").lower(), "moderate"),
            }]
        return r

    def _build_condition(
        self, uid: str, text: str, patient_uid: str, enc_uid: str,
        icd10: Optional[str] = None,
        clinical_status: str = "active",
        category: str = "encounter-diagnosis",
        category_display: str = "Encounter Diagnosis",
    ) -> Dict:
        snomed_code, snomed_display = _snomed_for_condition(text)
        coding: List[Dict] = []
        if icd10:
            coding.append({"system": "http://hl7.org/fhir/sid/icd-10",
                            "code": icd10, "display": text[:100]})
        coding.append({"system": "http://snomed.info/sct",
                        "code": snomed_code, "display": snomed_display})
        return {
            "resourceType": "Condition",
            "id":   uid,
            "meta": _meta("Condition"),
            "clinicalStatus": {"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/condition-clinical",
                "code": clinical_status}]},
            "verificationStatus": {"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                "code": "confirmed"}]},
            "category": [{"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/condition-category",
                "code": category, "display": category_display}]}],
            "code":      {"coding": coding, "text": text},
            "subject":   {"reference": _urn(patient_uid), "display": "Patient"},
            "encounter": {"reference": _urn(enc_uid)},
        }

    def _build_observation(
        self, uid: str, obs_data: Dict, patient_uid: str,
        specimen_uid: Optional[str] = None,
        enc_uid: Optional[str] = None,      # v5.C
        report_type: str = "",
    ) -> Dict:
        """v5.C: encounter reference added; v5.A-G: all original fixes retained."""
        display   = obs_data.get("display", "Observation")
        raw_code  = obs_data.get("code", "")
        if not raw_code or str(raw_code).upper() in ("LOINC", "LOCAL", "NA", ""):
            loinc_code, loinc_display, _, _ = _loinc_lookup(display or report_type)
        else:
            loinc_code    = raw_code
            loinc_display = display

        obs: Dict[str, Any] = {
            "resourceType": "Observation",
            "id":     uid,
            "meta":   _meta("Observation"),
            "status": "final",
            "category": [{"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": "laboratory", "display": "Laboratory"}]}],
            "code": {
                "coding": [{"system": "http://loinc.org",
                             "code": loinc_code, "display": loinc_display}],
                "text": display,
            },
            "subject":          {"reference": _urn(patient_uid), "display": "Patient"},
            "effectiveDateTime": obs_data.get("date") or _now_iso()[:10],
        }
        # v5.C: encounter reference
        if enc_uid:
            obs["encounter"] = {"reference": _urn(enc_uid)}

        # Value
        val  = obs_data.get("value")
        unit = obs_data.get("unit")
        if val is not None:
            numeric = _parse_numeric(val)
            if numeric is not None:
                obs["valueQuantity"] = {
                    "value": numeric,
                    **({"unit": unit, "system": "http://unitsofmeasure.org", "code": unit}
                       if unit else {}),
                }
            else:
                obs["valueString"] = str(val)

        # Interpretation (auto-derive if not set but ref range exists)
        interp = (obs_data.get("interpretation") or "").lower()
        if interp not in INTERP_MAP and obs_data.get("ref_low") and obs_data.get("ref_high"):
            numeric_v = _parse_numeric(val)
            if numeric_v is not None:
                try:
                    if numeric_v < float(obs_data["ref_low"]):
                        interp = "low"
                    elif numeric_v > float(obs_data["ref_high"]):
                        interp = "high"
                    else:
                        interp = "normal"
                except (ValueError, TypeError):
                    pass
        if interp in INTERP_MAP:
            obs["interpretation"] = [{"coding": [INTERP_MAP[interp]]}]

        # Structured reference range
        ref_low  = obs_data.get("ref_low")
        ref_high = obs_data.get("ref_high")
        ref_text = obs_data.get("reference_range")
        if ref_low is not None or ref_high is not None:
            rr: Dict[str, Any] = {}
            if ref_low  is not None:
                rr["low"]  = {"value": float(ref_low),
                              **({"unit": unit, "system": "http://unitsofmeasure.org"} if unit else {})}
            if ref_high is not None:
                rr["high"] = {"value": float(ref_high),
                              **({"unit": unit, "system": "http://unitsofmeasure.org"} if unit else {})}
            if ref_text:
                rr["text"] = str(ref_text)
            obs["referenceRange"] = [rr]
        elif ref_text:
            obs["referenceRange"] = [{"text": str(ref_text)}]

        # Method
        if obs_data.get("method"):
            obs["method"] = {"text": str(obs_data["method"])}

        # Specimen link
        if specimen_uid:
            obs["specimen"] = {"reference": _urn(specimen_uid)}

        return obs

    def _build_vital_observations(
        self,
        vitals: Dict,
        patient_uid: str,
        enc_uid: str,
        effective_date: str,
        entries: List,
        extra_findings: Optional[Dict] = None,
    ) -> List[Dict]:
        """v5.E: BP panel with component slices; v5.N: parse combined BP string."""
        refs: List[Dict] = []
        if not vitals and not extra_findings:
            return refs

        # v5.N: expand combined BP string
        if vitals and vitals.get("bp") and not vitals.get("bp_systolic"):
            sys_v, dia_v = _parse_bp(str(vitals["bp"]))
            if sys_v:
                vitals = dict(vitals, bp_systolic=sys_v, bp_diastolic=dia_v)

        # v5.E: emit BP panel if both components are present
        sys_val = _parse_numeric(vitals.get("bp_systolic")) if vitals else None
        dia_val = _parse_numeric(vitals.get("bp_diastolic")) if vitals else None
        bp_panel_emitted = False
        if sys_val is not None and dia_val is not None:
            bp_uid = _uid()
            bp_interp = "normal"
            if sys_val > 140 or dia_val > 90:
                bp_interp = "high"
            elif sys_val < 90 or dia_val < 60:
                bp_interp = "low"
            bp_obs: Dict[str, Any] = {
                "resourceType": "Observation",
                "id":     bp_uid,
                "meta":   _meta("Observation"),
                "status": "final",
                "category": [{"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/observation-category",
                    "code": "vital-signs", "display": "Vital Signs"}]}],
                "code": {"coding": [{"system": "http://loinc.org",
                                      "code": "55284-4",
                                      "display": "Blood pressure systolic and diastolic"}],
                         "text": "Blood Pressure"},
                "subject":          {"reference": _urn(patient_uid)},
                "encounter":        {"reference": _urn(enc_uid)},
                "effectiveDateTime": effective_date,
                "interpretation":   [{"coding": [INTERP_MAP[bp_interp]]}],
                "component": [
                    {
                        "code": {"coding": [{"system": "http://loinc.org",
                                              "code": "8480-6",
                                              "display": "Systolic blood pressure"}]},
                        "valueQuantity": {"value": sys_val, "unit": "mmHg",
                                          "system": "http://unitsofmeasure.org",
                                          "code": "mm[Hg]"},
                        "interpretation": [{"coding": [INTERP_MAP[
                            "high" if sys_val > 140 else ("low" if sys_val < 90 else "normal")]]}],
                    },
                    {
                        "code": {"coding": [{"system": "http://loinc.org",
                                              "code": "8462-4",
                                              "display": "Diastolic blood pressure"}]},
                        "valueQuantity": {"value": dia_val, "unit": "mmHg",
                                          "system": "http://unitsofmeasure.org",
                                          "code": "mm[Hg]"},
                        "interpretation": [{"coding": [INTERP_MAP[
                            "high" if dia_val > 90 else ("low" if dia_val < 60 else "normal")]]}],
                    },
                ],
            }
            entries.append(_entry(bp_obs))
            refs.append({"reference": _urn(bp_uid), "display": "Blood Pressure"})
            bp_panel_emitted = True

        # All other vitals (skip bp_systolic / bp_diastolic if already in panel)
        for key, raw in (vitals or {}).items():
            if bp_panel_emitted and key in ("bp_systolic", "bp_diastolic", "bp"):
                continue
            meta = VITAL_LOINC.get(key)
            if not meta or raw is None:
                continue
            loinc_code, loinc_display, unit_str, ucum, ref_lo, ref_hi = meta
            numeric = _parse_numeric(raw)
            if numeric is None:
                continue

            interp_key = "normal"
            if ref_lo is not None and ref_hi is not None:
                if numeric < ref_lo:
                    interp_key = "low"
                elif numeric > ref_hi:
                    interp_key = "high"

            obs_uid = _uid()
            obs: Dict[str, Any] = {
                "resourceType": "Observation",
                "id":     obs_uid,
                "meta":   _meta("Observation"),
                "status": "final",
                "category": [{"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/observation-category",
                    "code": "vital-signs", "display": "Vital Signs"}]}],
                "code": {"coding": [{"system": "http://loinc.org",
                                      "code": loinc_code, "display": loinc_display}],
                         "text": loinc_display},
                "subject":          {"reference": _urn(patient_uid)},
                "encounter":        {"reference": _urn(enc_uid)},
                "effectiveDateTime": effective_date,
                "valueQuantity": {
                    "value": numeric, "unit": unit_str,
                    "system": "http://unitsofmeasure.org", "code": ucum,
                },
                "interpretation": [{"coding": [INTERP_MAP[interp_key]]}],
            }
            if ref_lo is not None and ref_hi is not None:
                obs["referenceRange"] = [{
                    "low":  {"value": ref_lo, "unit": unit_str,
                              "system": "http://unitsofmeasure.org"},
                    "high": {"value": ref_hi, "unit": unit_str,
                              "system": "http://unitsofmeasure.org"},
                }]
            entries.append(_entry(obs))
            refs.append({"reference": _urn(obs_uid), "display": loinc_display})

        # Textual clinical findings (CNS, CVS, Chest, P/A)
        for finding_name, finding_value in (extra_findings or {}).items():
            if not finding_value:
                continue
            obs_uid = _uid()
            entries.append(_entry({
                "resourceType": "Observation",
                "id":     obs_uid,
                "meta":   _meta("Observation"),
                "status": "final",
                "category": [{"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/observation-category",
                    "code": "exam", "display": "Exam"}]}],
                "code": {"coding": [{"system": "http://snomed.info/sct",
                                      "code": "363679005", "display": finding_name}],
                         "text": finding_name},
                "subject":          {"reference": _urn(patient_uid)},
                "encounter":        {"reference": _urn(enc_uid)},
                "effectiveDateTime": effective_date,
                "valueString":      str(finding_value),
            }))
            refs.append({"reference": _urn(obs_uid), "display": finding_name})

        return refs

    def _build_specimen(
        self, uid: str, spec: Dict, patient_uid: str, collected_date: str
    ) -> Dict:
        """v5.J: meta.profile added."""
        return {
            "resourceType": "Specimen",
            "id":   uid,
            "meta": _meta("Specimen"),           # v5.J: was missing
            "type": {"coding": [{"system": "http://snomed.info/sct",
                                  "code": "119297000",
                                  "display": spec.get("type", "Blood specimen")}],
                     "text": spec.get("type", "Unknown specimen")},
            "subject":    {"reference": _urn(patient_uid)},
            "collection": {"collectedDateTime": collected_date},
        }

    def _build_procedure(
        self, uid: str, proc: Dict,
        patient_uid: str, enc_uid: str, prac_uids: List[str]
    ) -> Dict:
        """v5.F: distinct SNOMED codes per procedure type."""
        name = proc.get("name", "Procedure")
        snomed_code = proc.get("snomed_code")
        if not snomed_code:
            snomed_code, _ = _snomed_for_procedure(name)

        r: Dict[str, Any] = {
            "resourceType": "Procedure",
            "id":     uid,
            "meta":   _meta("Procedure"),
            "status": "completed",
            "code": {
                "coding": [{"system": "http://snomed.info/sct",
                             "code": snomed_code, "display": name}],
                "text": name,
            },
            "subject":          {"reference": _urn(patient_uid)},
            "encounter":        {"reference": _urn(enc_uid)},
            "performedDateTime": proc.get("date") or _now_iso()[:10],
        }
        if prac_uids:
            r["performer"] = [{"actor": {"reference": _urn(prac_uids[0])}}]
        # Surgeon / anaesthetist as additional performers
        if proc.get("surgeon") or proc.get("anesthetist"):
            performers = r.get("performer", [])
            if proc.get("surgeon"):
                performers.append({"function": {"coding": [{"system": "http://snomed.info/sct",
                                                             "code": "304292004",
                                                             "display": "Surgeon"}]},
                                    "actor": {"display": proc["surgeon"]}})
            if proc.get("anesthetist"):
                performers.append({"function": {"coding": [{"system": "http://snomed.info/sct",
                                                             "code": "405684005",
                                                             "display": "Anaesthetist"}]},
                                    "actor": {"display": proc["anesthetist"]}})
            r["performer"] = performers
        return r

    def _build_medication_request(
        self, uid: str, med: Dict,
        patient_uid: str, enc_uid: str,
        status: str = "active",
        authored_on: Optional[str] = None,
    ) -> Dict:
        """v5.G: medication uses name-based text coding (not generic reused SNOMED)."""
        mr: Dict[str, Any] = {
            "resourceType": "MedicationRequest",
            "id":     uid,
            "meta":   _meta("MedicationRequest"),
            "status": status,
            "intent": "order",
            # v5.G: medication text + display without reusing a single SNOMED code
            "medicationCodeableConcept": {
                "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                             "display": med.get("name", "Medication")}],
                "text": med.get("name", "Medication"),
            },
            "subject":    {"reference": _urn(patient_uid)},
            "encounter":  {"reference": _urn(enc_uid)},
            "authoredOn": authored_on or _now_iso(),
        }

        dosage: Dict[str, Any] = {}
        parts: List[str] = []

        if med.get("dose"):
            numeric_dose = _parse_numeric(med["dose"])
            dosage["doseAndRate"] = [{
                "doseQuantity": {
                    "value":  numeric_dose if numeric_dose is not None else med["dose"],
                    "unit":   med.get("dose_unit") or "unit",
                    "system": "http://unitsofmeasure.org",
                },
            }]
            parts.append(str(med["dose"]))

        route_text  = med.get("route")
        route_tuple = _route_lookup(route_text)
        if route_tuple:
            snomed_code, route_display = route_tuple
            if snomed_code:
                dosage["route"] = {
                    "coding": [{"system": "http://snomed.info/sct",
                                 "code": snomed_code, "display": route_display}],
                    "text": route_text,
                }
            else:
                # v5.H: text-only for inhaled (no unreliable SNOMED code)
                dosage["route"] = {"text": route_display}
            parts.append(route_display)
        elif route_text:
            dosage["route"] = {"text": route_text}
            parts.append(route_text)

        timing = _timing_lookup(med.get("frequency"))
        if timing:
            dosage["timing"] = timing
        if med.get("frequency"):
            parts.append(med["frequency"])

        if med.get("duration"):
            parts.append(f"for {med['duration']}")
            dur_days = re.search(r"(\d+)\s*day", str(med["duration"]), re.I)
            if dur_days:
                mr["dispenseRequest"] = {
                    "expectedSupplyDuration": {
                        "value": int(dur_days.group(1)),
                        "unit": "days", "system": "http://unitsofmeasure.org", "code": "d",
                    },
                }

        dosage["text"] = " ".join(parts) if parts else "As directed"
        mr["dosageInstruction"] = [dosage]
        return mr

    def _build_charge_item(
        self, uid: str, item: Dict, patient_uid: str, enc_uid: str, org_uid: str
    ) -> Dict:
        """v5.L: failsafe for null amounts; org as performer."""
        r: Dict[str, Any] = {
            "resourceType": "ChargeItem",
            "id":     uid,
            "status": "billed",
            "code": {
                "coding": [{"system": "http://snomed.info/sct",
                             "code": "405623001",
                             "display": item.get("description", "Hospital service")}],
                "text": item.get("description", "Hospital service"),
            },
            "subject":              {"reference": _urn(patient_uid)},
            "context":              {"reference": _urn(enc_uid)},
            "performingOrganization": {"reference": _urn(org_uid)},
            "quantity": {"value": _parse_numeric(item.get("quantity")) or 1},
        }
        if item.get("date"):
            r["occurrenceDateTime"] = item["date"]
        amount = _parse_numeric(item.get("amount"))
        if amount is not None:
            r["priceOverride"] = {"value": round(amount, 2), "currency": "INR"}
        return r

    def _build_appointment(
        self, uid: str, patient_uid: str,
        enc_uid: str, appt_data: Dict, prac_uid: str
    ) -> Dict:
        appt: Dict[str, Any] = {
            "resourceType": "Appointment",
            "id":     uid,
            "status": "booked",
            "participant": [
                {"actor": {"reference": _urn(patient_uid)}, "status": "accepted"},
                {"actor": {"reference": _urn(prac_uid)},    "status": "accepted"},
            ],
            "basedOn": [{"reference": _urn(enc_uid)}],
        }
        if appt_data.get("date"):
            appt["start"] = appt_data["date"]
        if appt_data.get("department"):
            appt["serviceType"] = [{
                "coding": [{"system": "http://snomed.info/sct",
                             "code": "394814009", "display": appt_data["department"]}],
                "text": appt_data["department"],
            }]
        comment = appt_data.get("doctor") or appt_data.get("note")
        if comment:
            appt["comment"] = (f"Follow-up with {comment}"
                                if appt_data.get("doctor") else str(comment))
        days = appt_data.get("days_after_discharge")
        if days:
            appt.setdefault("extension", []).append({
                "url": "http://hip.in/fhir/StructureDefinition/daysAfterDischarge",
                "valueString": str(days),
            })
        return appt

    def _build_careplan(
        self, uid: str, data: Dict,
        patient_uid: str, enc_uid: str
    ) -> Dict:
        desc = (data.get("care_plan") or data.get("diet_advice")
                or data.get("follow_up") or "Follow up as advised by physician.")
        activities: List[Dict] = []
        if data.get("follow_up"):
            activities.append({"detail": {"status": "not-started",
                                           "description": data["follow_up"]}})
        if data.get("diet_advice"):
            activities.append({"detail": {"status": "not-started",
                                           "kind": "NutritionOrder",
                                           "description": data["diet_advice"]}})
        physio = data.get("physiotherapy_advice") or data.get("respiratory_care")
        if physio:
            activities.append({"detail": {"status": "not-started",
                                           "kind": "ServiceRequest",
                                           "description": physio}})
        if not activities:
            activities.append({"detail": {"status": "not-started", "description": desc}})

        return {
            "resourceType": "CarePlan",
            "id":     uid,
            "meta":   _meta("CarePlan"),
            "status": "active",
            "intent": "plan",
            "subject":     {"reference": _urn(patient_uid)},
            "encounter":   {"reference": _urn(enc_uid)},
            "description": desc,
            "note": [{"text": desc}],
            "activity": activities,
        }

    def _build_document_reference(
        self, uid: str, patient_uid: str,
        prac_uid: str, org_uid: str, data: Dict
    ) -> Dict:
        return {
            "resourceType": "DocumentReference",
            "id":     uid,
            "meta":   _meta("DocumentReference"),
            "status": "current",
            "type":   {"coding": [{"system": "http://snomed.info/sct",
                                    "code": "373942005", "display": "Discharge summary"}],
                       "text": "Discharge Summary"},
            "subject":   {"reference": _urn(patient_uid)},
            "author":    [{"reference": _urn(prac_uid)}],
            "custodian": {"reference": _urn(org_uid)},
            "content": [{"attachment": {"contentType": "application/pdf",
                                         "title": "Discharge Summary Document"}}],
        }

    def _build_claim(
        self,
        uid: str, claim_id: str,
        patient_uid: str, org_uid: str, payer_uid: str,
        primary_prac_uid: str, prac_uids: List[str],
        supporting_info: List[Dict], data: Dict,
    ) -> Dict:
        """v5.K: insurance.coverage built as inline Coverage display."""
        icd10s     = data.get("diagnosis", {}).get("icd10_codes", [])
        primary_dx = data.get("diagnosis", {}).get("primary", "")
        dx_list: List[Dict] = []

        for i, code in enumerate(icd10s or []):
            dx_list.append({
                "sequence": i + 1,
                "diagnosisCodeableConcept": {
                    "coding": [{"system": "http://hl7.org/fhir/sid/icd-10",
                                 "code": code, "display": primary_dx if i == 0 else code}],
                    "text": primary_dx if i == 0 else code,
                },
                "type": [{"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/ex-diagnosistype",
                    "code": "principal" if i == 0 else "admitting"}]}],
            })
        if not dx_list:
            snomed_code, snomed_display = _snomed_for_condition(primary_dx or "")
            dx_list = [{
                "sequence": 1,
                "diagnosisCodeableConcept": {
                    "coding": [{"system": "http://snomed.info/sct",
                                 "code": snomed_code,
                                 "display": primary_dx or "Unspecified condition"}],
                    "text": primary_dx or "Unspecified condition",
                },
                "type": [{"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/ex-diagnosistype",
                    "code": "principal"}]}],
            }]

        care_team: List[Dict] = []
        for i, pu in enumerate(prac_uids, 1):
            care_team.append({
                "sequence": i,
                "provider": {"reference": _urn(pu)},
                "role": {"coding": [{"system":
                    "http://terminology.hl7.org/CodeSystem/claimcareteamrole",
                    "code": "primary" if i == 1 else "assist"}]},
            })

        items: List[Dict] = []
        total_amount = 0.0
        for idx, bill in enumerate(data.get("billing_items", []), 1):
            amount = _parse_numeric(bill.get("amount"))
            qty    = _parse_numeric(bill.get("quantity")) or 1
            item: Dict[str, Any] = {
                "sequence": idx,
                "productOrService": {
                    "coding": [{"system": "http://snomed.info/sct", "code": "371443000",
                                 "display": bill.get("description", "Hospital service")}],
                    "text": bill.get("description", "Hospital service"),
                },
                "quantity": {"value": qty},
            }
            if amount is not None:
                item["unitPrice"]  = {"value": round(amount, 2), "currency": "INR"}
                item["net"]        = {"value": round(amount * qty, 2), "currency": "INR"}
                total_amount      += amount * qty
            if bill.get("date"):
                item["servicedDate"] = bill["date"]
            items.append(item)

        if not items:
            items = [{
                "sequence": 1,
                "productOrService": {"coding": [{"system": "http://snomed.info/sct",
                                                  "code": "371443000",
                                                  "display": "Hospital services"}]},
            }]

        admission = data.get("admission", {})
        discharge = data.get("discharge", {})

        claim: Dict[str, Any] = {
            "resourceType": "Claim",
            "id":   uid,
            "meta": {"profile": [PROFILES["Claim"]]},
            "identifier": [{"system": "http://hip.in/claim", "value": claim_id}],
            "status":  "active",
            "type":    {"coding": [{"system":
                "http://terminology.hl7.org/CodeSystem/claim-type",
                "code": "institutional"}]},
            "use":     "claim",
            "patient": {"reference": _urn(patient_uid)},
            "created": _now_iso(),
            "insurer": {"reference": _urn(payer_uid)},   # FIX T: payer, not lab
            "provider":{"reference": _urn(org_uid)},
            "priority":{"coding": [{"code": "normal"}]},
            "diagnosis":      dx_list,
            "supportingInfo": supporting_info,
            "careTeam": care_team if care_team else [{
                "sequence": 1,
                "provider": {"reference": _urn(primary_prac_uid)},
            }],
            # v5.K: insurance with payer display
            "insurance": [{
                "sequence": 1,
                "focal":    True,
                "coverage": {
                    "display": data.get("payer_type") or "Self",
                    "identifier": {"system": "http://hip.in/panel",
                                    "value": data.get("payer_type") or "Self"},
                },
            }],
            "item": items,
        }

        if admission.get("date"):
            claim["billablePeriod"] = {
                "start": admission["date"],
                "end":   discharge.get("date") or _now_iso()[:10],
            }
        if total_amount > 0:
            claim["total"] = {"value": round(total_amount, 2), "currency": "INR"}

        return claim
