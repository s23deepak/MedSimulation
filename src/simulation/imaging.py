"""
Medical imaging support for clinical simulation cases.

Provides:
  - ECG SVG generation (programmatic 12-lead waveforms)
  - Image file path resolution
  - Imaging study management helpers
"""

from __future__ import annotations

import logging
import math
import hashlib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Where imaging files live
IMAGING_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "imaging"


# ── ECG SVG Generator ────────────────────────────────────────────────────────

# Standard 12-lead ECG layout (4 columns × 3 rows)
ECG_LEAD_LAYOUT = [
    ["I",   "aVR", "V1", "V4"],
    ["II",  "aVL", "V2", "V5"],
    ["III", "aVF", "V3", "V6"],
]

# ECG paper: 25mm/s, 10mm/mV — standard grid
ECG_CONFIG = {
    "paper_speed": 25,        # mm/s
    "gain": 10,               # mm/mV
    "strip_width_mm": 250,    # total width (10 seconds)
    "strip_height_mm": 200,   # total height
    "px_per_mm": 4,           # rendering resolution
    "grid_major_mm": 5,       # 5mm = 0.2s
    "grid_minor_mm": 1,       # 1mm = 0.04s
}


def generate_ecg_svg(
    case_id: str,
    ecg_pattern: str = "normal_sinus",
    heart_rate: int = 75,
    st_changes: dict | None = None,
    annotations: list[str] | None = None,
) -> str:
    """
    Generate a 12-lead ECG as an SVG string.

    Parameters
    ----------
    case_id : str
        Used for deterministic random seed
    ecg_pattern : str
        'normal_sinus', 'stemi_inferior', 'stemi_anterior', 'af',
        'sinus_tachycardia', 'peaked_t', 'rbbb', 'st_depression'
    heart_rate : int
        Beats per minute
    st_changes : dict, optional
        Per-lead ST deviation in mm: {"II": 2.5, "III": 3, "aVF": 2, ...}
    annotations : list[str], optional
        Text annotations below the ECG

    Returns
    -------
    str
        Complete SVG document string
    """
    cfg = ECG_CONFIG
    w = cfg["strip_width_mm"] * cfg["px_per_mm"]
    h = cfg["strip_height_mm"] * cfg["px_per_mm"]
    px = cfg["px_per_mm"]

    # Deterministic seed from case_id
    seed = int(hashlib.md5(case_id.encode()).hexdigest()[:8], 16)

    # Pattern-specific ST changes
    if st_changes is None:
        st_changes = _get_pattern_st_changes(ecg_pattern)

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" style="background:#fef9ef;">',
    ]

    # ── Grid ──────────────────────────────────────────────────────────────
    svg_parts.append(_draw_ecg_grid(w, h, px, cfg))

    # ── Lead strips ───────────────────────────────────────────────────────
    lead_w = w // 4
    lead_h = h // 3 - 12 * px  # leave room for labels

    for row_idx, row in enumerate(ECG_LEAD_LAYOUT):
        for col_idx, lead_name in enumerate(row):
            x_off = col_idx * lead_w + 8 * px
            y_off = row_idx * (h // 3) + 16 * px
            y_base = y_off + lead_h // 2

            # Lead label
            svg_parts.append(
                f'<text x="{x_off}" y="{y_off - 4*px}" '
                f'font-family="Inter,sans-serif" font-size="{3.5*px}" '
                f'font-weight="700" fill="#1a1a2e">{lead_name}</text>'
            )

            # Waveform
            st_dev = st_changes.get(lead_name, 0)
            path_d = _generate_lead_waveform(
                lead_name, heart_rate, lead_w - 16 * px, lead_h,
                st_dev, px, seed + hash(lead_name), ecg_pattern
            )
            svg_parts.append(
                f'<path d="M {x_off} {y_base} {path_d}" '
                f'stroke="#1a1a2e" stroke-width="{0.6*px}" fill="none" '
                f'stroke-linecap="round" stroke-linejoin="round"/>'
            )

    # ── Title bar ─────────────────────────────────────────────────────────
    title_text = f"12-Lead ECG — {heart_rate} bpm"
    if ecg_pattern != "normal_sinus":
        title_text += f" — {ecg_pattern.replace('_', ' ').title()}"
    svg_parts.append(
        f'<text x="{4*px}" y="{h - 3*px}" '
        f'font-family="Inter,sans-serif" font-size="{3*px}" '
        f'fill="#64748b">{title_text} | 25mm/s 10mm/mV</text>'
    )

    # ── Annotations ───────────────────────────────────────────────────────
    if annotations:
        for i, note in enumerate(annotations[:3]):
            svg_parts.append(
                f'<text x="{w - 4*px}" y="{h - (3 + i*4)*px}" '
                f'font-family="Inter,sans-serif" font-size="{2.8*px}" '
                f'fill="#dc2626" text-anchor="end" font-weight="600">{note}</text>'
            )

    svg_parts.append('</svg>')
    return '\n'.join(svg_parts)


def _draw_ecg_grid(w: int, h: int, px: int, cfg: dict) -> str:
    """Draw ECG paper grid (major + minor lines)."""
    parts = []

    # Minor grid (1mm)
    minor = cfg["grid_minor_mm"] * px
    parts.append(f'<g stroke="#f0dcc0" stroke-width="0.5">')
    for x in range(0, w + 1, minor):
        parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}"/>')
    for y in range(0, h + 1, minor):
        parts.append(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}"/>')
    parts.append('</g>')

    # Major grid (5mm)
    major = cfg["grid_major_mm"] * px
    parts.append(f'<g stroke="#dfc5a0" stroke-width="1">')
    for x in range(0, w + 1, major):
        parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}"/>')
    for y in range(0, h + 1, major):
        parts.append(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}"/>')
    parts.append('</g>')

    return '\n'.join(parts)


def _generate_lead_waveform(
    lead: str,
    hr: int,
    width: int,
    height: int,
    st_deviation: float,
    px: int,
    seed: int,
    pattern: str,
) -> str:
    """
    Generate a single-lead ECG waveform as SVG path data.

    Uses mathematical approximation of PQRST complexes:
    - P wave: small upright deflection
    - QRS complex: sharp deflection (Q down, R up, S down)
    - T wave: broad deflection (affected by ST changes)
    """
    # RR interval in pixels
    beat_duration_s = 60.0 / hr
    beat_width_px = beat_duration_s * ECG_CONFIG["paper_speed"] * px
    num_beats = max(1, int(width / beat_width_px))

    points = []
    x = 0

    # Lead-specific amplitude modifiers
    amp = _get_lead_amplitudes(lead)
    gain = px * ECG_CONFIG["gain"] * 0.08  # scale factor

    for beat_idx in range(num_beats):
        beat_x = beat_idx * beat_width_px

        # Slight timing variation for realism
        jitter = ((seed + beat_idx * 7) % 11 - 5) * 0.3

        # Isoelectric baseline segments + PQRST
        segments = _pqrst_segments(beat_width_px, amp, gain, st_deviation * px, pattern, lead)

        for seg_x, seg_y in segments:
            points.append(f"l {seg_x:.1f} {seg_y + jitter * 0.1:.1f}")

    return " ".join(points)


def _get_lead_amplitudes(lead: str) -> dict:
    """Get relative amplitudes for each waveform component by lead."""
    # Standard amplitude patterns
    amps = {
        "I":    {"P": 0.8, "Q": -0.3, "R": 5.0, "S": -1.0, "T": 1.5},
        "II":   {"P": 1.0, "Q": -0.2, "R": 6.0, "S": -0.8, "T": 2.0},
        "III":  {"P": 0.5, "Q": -0.4, "R": 3.5, "S": -1.2, "T": 1.0},
        "aVR":  {"P": -0.8, "Q": 0.3, "R": -4.0, "S": 0.5, "T": -1.5},
        "aVL":  {"P": 0.6, "Q": -0.3, "R": 3.0, "S": -0.8, "T": 1.2},
        "aVF":  {"P": 0.7, "Q": -0.3, "R": 4.5, "S": -1.0, "T": 1.5},
        "V1":   {"P": 0.5, "Q": 0.0, "R": 1.5, "S": -5.0, "T": -0.8},
        "V2":   {"P": 0.6, "Q": 0.0, "R": 3.0, "S": -4.0, "T": 1.5},
        "V3":   {"P": 0.6, "Q": -0.2, "R": 5.0, "S": -2.5, "T": 2.0},
        "V4":   {"P": 0.5, "Q": -0.3, "R": 7.0, "S": -1.5, "T": 2.5},
        "V5":   {"P": 0.5, "Q": -0.4, "R": 6.0, "S": -0.8, "T": 2.0},
        "V6":   {"P": 0.5, "Q": -0.3, "R": 5.0, "S": -0.5, "T": 1.5},
    }
    return amps.get(lead, amps["II"])


def _pqrst_segments(
    beat_w: float,
    amp: dict,
    gain: float,
    st_shift: float,
    pattern: str,
    lead: str,
) -> list[tuple[float, float]]:
    """
    Generate PQRST waveform segments as (dx, dy) pairs.
    Y is inverted (positive = down in SVG).
    """
    segs = []
    # Timing as fraction of beat width
    # TP segment (baseline before P)
    segs.append((beat_w * 0.06, 0))

    # P wave (gaussian-ish)
    p_w = beat_w * 0.08
    p_h = -amp["P"] * gain
    segs.extend([
        (p_w * 0.5, p_h),
        (p_w * 0.5, -p_h),
    ])

    # PR segment
    segs.append((beat_w * 0.06, 0))

    # QRS complex
    q_w = beat_w * 0.02
    r_w = beat_w * 0.025
    s_w = beat_w * 0.025

    segs.append((q_w, -amp["Q"] * gain))       # Q wave down
    segs.append((r_w, -amp["R"] * gain))        # R wave up (sharp)
    segs.append((r_w, amp["R"] * gain + amp["Q"] * gain))  # R back to baseline
    segs.append((s_w, -amp["S"] * gain))        # S wave down
    segs.append((s_w, amp["S"] * gain))          # S back to baseline

    # ST segment (may be elevated or depressed)
    st_seg_w = beat_w * 0.08
    segs.append((st_seg_w * 0.3, -st_shift))    # ST shift
    segs.append((st_seg_w * 0.7, 0))            # Hold ST level

    # T wave
    t_w = beat_w * 0.10
    t_h = -amp["T"] * gain

    # Modify T wave for specific patterns
    if pattern == "peaked_t":
        t_h *= 2.0  # Tall peaked T waves (hyperkalaemia)
    if pattern in ("stemi_inferior", "stemi_anterior") and st_shift != 0:
        t_h *= 1.3  # Hyperacute T waves in STEMI leads

    segs.extend([
        (t_w * 0.5, t_h),
        (t_w * 0.5, -t_h),
    ])

    # Return to baseline (compensate for ST shift)
    segs.append((beat_w * 0.04, st_shift))

    # TP segment remainder
    used = sum(s[0] for s in segs)
    remaining = beat_w - used
    if remaining > 0:
        segs.append((remaining, 0))

    return segs


def _get_pattern_st_changes(pattern: str) -> dict:
    """Default ST changes for common ECG patterns."""
    patterns = {
        "normal_sinus": {},
        "sinus_tachycardia": {},
        "stemi_inferior": {
            "II": 2.5, "III": 3.0, "aVF": 2.5,
            "I": -1.0, "aVL": -1.5,  # reciprocal depression
        },
        "stemi_anterior": {
            "V1": 1.5, "V2": 2.5, "V3": 3.0, "V4": 2.5, "V5": 1.5,
            "II": -0.5, "III": -1.0, "aVF": -0.8,  # reciprocal
        },
        "st_depression": {
            "V4": -2.0, "V5": -2.0, "V6": -1.5,
            "I": -1.0, "II": -1.5,
        },
        "af": {},  # Irregular rhythm handled differently
        "peaked_t": {
            "V2": 0.5, "V3": 0.5, "V4": 0.5, "II": 0.3,
        },
        "rbbb": {
            "V1": 0.3, "V2": 0.3,
        },
    }
    return patterns.get(pattern, {})


# ── File management ───────────────────────────────────────────────────────────

def ensure_imaging_dir() -> Path:
    """Create the imaging directory if it doesn't exist."""
    IMAGING_DIR.mkdir(parents=True, exist_ok=True)
    return IMAGING_DIR


def save_ecg_svg(case_id: str, filename: str, svg_content: str) -> str:
    """
    Save an ECG SVG to the imaging directory.

    Returns the relative file path (for use in imaging_studies).
    """
    case_dir = ensure_imaging_dir() / case_id.lower()
    case_dir.mkdir(exist_ok=True)
    path = case_dir / filename
    path.write_text(svg_content, encoding="utf-8")
    logger.info("Saved ECG SVG: %s", path)
    return f"{case_id.lower()}/{filename}"


def resolve_image_path(file_path: str) -> Path | None:
    """Resolve a relative imaging path to an absolute filesystem path."""
    full = IMAGING_DIR / file_path
    if full.exists():
        return full
    return None


def get_image_url(file_path: str) -> str:
    """Convert a relative file path to a URL for the frontend."""
    return f"/imaging/{file_path}"


# ── Generate sample images for built-in cases ─────────────────────────────────

def generate_sample_ecgs() -> dict[str, list[dict]]:
    """
    Generate ECG SVGs for all 5 built-in cases.

    Returns a dict of case_id → list of imaging_study dicts.
    """
    samples = {}

    # SIM-001: STEMI — Inferior with ST elevation
    svg = generate_ecg_svg(
        "SIM-001",
        ecg_pattern="stemi_inferior",
        heart_rate=102,
        annotations=["ST elevation II, III, aVF", "Reciprocal depression I, aVL"],
    )
    ecg_path = save_ecg_svg("SIM-001", "ecg_stemi_inferior.svg", svg)
    samples["SIM-001"] = [
        {
            "study_id": "IMG-001-ECG",
            "modality": "ECG",
            "description": "12-lead ECG",
            "file_path": ecg_path,
            "findings": (
                "Sinus tachycardia at 102 bpm. ST elevation >2mm in leads II, III, aVF "
                "with reciprocal ST depression in I, aVL. Consistent with inferior STEMI."
            ),
        },
    ]

    # SIM-002: DKA — Peaked T waves (hyperkalaemia)
    svg = generate_ecg_svg(
        "SIM-002",
        ecg_pattern="peaked_t",
        heart_rate=118,
        annotations=["Peaked T-waves (K+ 5.8)", "Sinus tachycardia"],
    )
    ecg_path = save_ecg_svg("SIM-002", "ecg_peaked_t.svg", svg)
    samples["SIM-002"] = [
        {
            "study_id": "IMG-002-ECG",
            "modality": "ECG",
            "description": "12-lead ECG",
            "file_path": ecg_path,
            "findings": (
                "Sinus tachycardia at 118 bpm. Tall peaked T-waves most prominent "
                "in V2-V4, consistent with hyperkalaemia (K+ 5.8 mmol/L)."
            ),
        },
    ]

    # SIM-003: PE — Sinus tach + S1Q3T3 + RBBB
    svg = generate_ecg_svg(
        "SIM-003",
        ecg_pattern="rbbb",
        heart_rate=114,
        st_changes={"V1": 0.5, "V2": 0.5, "V3": -0.8, "V4": -0.5, "III": 0.8, "I": -0.3},
        annotations=["S1Q3T3 pattern", "RBBB", "T-wave inversion V1-V4"],
    )
    ecg_path = save_ecg_svg("SIM-003", "ecg_pe_s1q3t3.svg", svg)
    samples["SIM-003"] = [
        {
            "study_id": "IMG-003-ECG",
            "modality": "ECG",
            "description": "12-lead ECG",
            "file_path": ecg_path,
            "findings": (
                "Sinus tachycardia 114 bpm. S1Q3T3 pattern. Right bundle branch block. "
                "T-wave inversion V1-V4. Findings suggestive of right heart strain."
            ),
        },
    ]

    # SIM-004: Stroke — AF
    svg = generate_ecg_svg(
        "SIM-004",
        ecg_pattern="af",
        heart_rate=88,
        annotations=["Atrial fibrillation", "Irregularly irregular"],
    )
    ecg_path = save_ecg_svg("SIM-004", "ecg_af.svg", svg)
    samples["SIM-004"] = [
        {
            "study_id": "IMG-004-ECG",
            "modality": "ECG",
            "description": "12-lead ECG",
            "file_path": ecg_path,
            "findings": (
                "Atrial fibrillation with ventricular rate ~88 bpm. "
                "Irregularly irregular rhythm. No ST changes. Normal axis."
            ),
        },
    ]

    # SIM-005: Appendicitis — Normal sinus rhythm
    svg = generate_ecg_svg(
        "SIM-005",
        ecg_pattern="normal_sinus",
        heart_rate=96,
        annotations=["Normal sinus rhythm"],
    )
    ecg_path = save_ecg_svg("SIM-005", "ecg_normal.svg", svg)
    samples["SIM-005"] = [
        {
            "study_id": "IMG-005-ECG",
            "modality": "ECG",
            "description": "12-lead ECG",
            "file_path": ecg_path,
            "findings": "Normal sinus rhythm at 96 bpm. No ST-T changes. Normal axis.",
        },
    ]

    logger.info("Generated sample ECGs for %d cases", len(samples))
    return samples
