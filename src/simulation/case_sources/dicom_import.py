"""
DICOM Dataset Import Pipeline

Handles uploading ZIP files containing `.dcm` files, parsing them with pydicom
to extract metadata (age, sex, modality, study description), and generating
a base `ClinicalCase` for the simulation engine.
"""

from __future__ import annotations

import logging
import zipfile
import uuid
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import pydicom

from src.simulation.cases import ClinicalCase
from src.simulation.imaging import ensure_imaging_dir

logger = logging.getLogger(__name__)

def process_dicom_zip(zip_path: Path) -> ClinicalCase:
    """
    Extracts a ZIP of DICOM files, parses metadata from a representative slice,
    and returns a scaffolding `ClinicalCase`.
    
    The extracted files are persistently stored in data/imaging/dicom/{case_id}/.
    """
    case_id = f"SIM-DCM-{uuid.uuid4().hex[:8].upper()}"
    dicom_dest = ensure_imaging_dir() / "dicom" / case_id

    # Extract all files into a temporary directory first to validate
    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = zf.infolist()
                if len(members) > 1000 or sum(m.file_size for m in members) > 200 * 1024 * 1024:
                    raise ValueError("DICOM archive exceeds extraction limits")
                for member in members:
                    target = (tmp_path / member.filename).resolve()
                    if not target.is_relative_to(tmp_path.resolve()) or "\\" in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("Unsafe archive member")
                    if member.file_size > 40 * 1024 * 1024 or member.flag_bits & 1:
                        raise ValueError("Unsupported archive member")
                zf.extractall(tmp_path)
        except zipfile.BadZipFile:
            raise ValueError("Uploaded file is not a valid ZIP archive.")

        # Find all .dcm files
        dcm_files = []
        for file in tmp_path.rglob("*"):
            if file.is_file():
                # Some DICOMs lack extensions; we try to parse files that look promising
                if file.suffix.lower() == ".dcm" or not file.suffix:
                    dcm_files.append(file)

        if not dcm_files:
            raise ValueError("No DICOM files found in ZIP archive.")

        # Read the first valid DICOM to extract patient metadata
        metadata = None
        for f in dcm_files:
            try:
                # Stop before reading pixel data to be fast
                ds = pydicom.dcmread(f, stop_before_pixels=True)
                metadata = ds
                break
            except pydicom.errors.InvalidDicomError:
                continue

        if not metadata:
            raise ValueError("None of the files in the ZIP appear to be valid DICOM format.")

        # Move files to permanent storage
        dicom_dest.mkdir(parents=True, exist_ok=True)
        # Keep original structure within the case folder
        copied_files = []
        for f in dcm_files:
            try:
                pydicom.dcmread(f, stop_before_pixels=True)
            except pydicom.errors.InvalidDicomError as exc:
                raise ValueError("All archive images must be valid DICOM") from exc
            rel_path = f.relative_to(tmp_path)
            target = dicom_dest / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            # Store the relative web path ("dicom/case_uuid/...dcm")
            copied_files.append(f"dicom/{case_id}/{rel_path.as_posix()}")

    # Extract Clinical Features from DICOM Tags
    # Often stored in standard DICOM attributes
    
    def get_tag(ds, tag_name, default="Unknown"):
        val = getattr(ds, tag_name, default)
        return str(val.value) if hasattr(val, 'value') else str(val)

    age = get_tag(metadata, "PatientAge", "").strip('Y') or "Adult"
    sex = get_tag(metadata, "PatientSex", "U")
    if sex == 'M': sex_str = "Male"
    elif sex == 'F': sex_str = "Female"
    else: sex_str = "Patient"

    modality = get_tag(metadata, "Modality", "CT")
    body_part = get_tag(metadata, "BodyPartExamined", "Unknown Body Part")
    study_desc = get_tag(metadata, "StudyDescription", f"{modality} Scan")

    presentation = f"A {age}-year-old {sex_str} presents for {study_desc} ({body_part})."
    
    # We use a single string to indicate it's a directory stack
    # The frontend will fetch the list of files if it sees a directory structure
    case = ClinicalCase(
        case_id=case_id,
        title=f"Radiology Case: {study_desc}",
        specialty="Radiology",
        difficulty="intermediate",
        learning_objectives=[f"Interpret {modality} of the {body_part}"],
        presentation=presentation,
        initial_vitals={"HR": 80, "BP": "120/80", "RR": 16, "SpO2": "98%", "Temp": "37.0°C"},
        history_data={"pain": "No specific pain reported.", "history": "Referred for imaging."},
        physical_exam={"General": "Alert and oriented.", "Chest": "Clear."},
        investigations={},
        correct_diagnosis="Pending educator review",
        acceptable_diagnoses=["TBD"],
        correct_management=["Formal radiology report", "Clinical correlation"],
        key_learning_points=["Systematic approach to imaging review", "Identifying key pathologies"],
        score_weights={"history": 10, "exam": 10, "investigations": 30, "diagnosis": 40, "management": 10},
        imaging_studies=[
            {
                "study_id": f"IMG-{case_id}-{modality}",
                "modality": modality,
                "description": study_desc,
                # Special marker for frontend: base directory of the DICOMs
                "file_path": f"dicom/{case_id}",
                "findings": "Pending interpretation guidelines.",
                "thumbnail": "",  # To be generated or handled safely
            }
        ]
    )

    return case
