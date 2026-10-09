"""Tests for patient response cleanup in the simulation engine."""

from simulation.cases import ClinicalCase
from simulation.simulator import SimulationEngine


def test_clean_response_removes_outer_double_quotes():
    assert SimulationEngine._clean_response('"My chest hurts."') == "My chest hurts."


def test_clean_response_keeps_inner_quotes():
    response = '"I feel like it is a \"pressure\" in my chest."'

    assert (
        SimulationEngine._clean_response(response)
        == 'I feel like it is a "pressure" in my chest.'
    )


def test_clean_response_removes_quotes_after_preamble():
    response = 'Thought: I should answer as the patient.\n\n"I started feeling sick yesterday."'

    assert (
        SimulationEngine._clean_response(response)
        == "I started feeling sick yesterday."
    )


def test_clean_response_removes_inline_echoed_question_and_patient_label():
    response = (
        '"When did it start?" Patient: "It started early this afternoon, probably '
        "around lunchtime. While I was eating at my desk.\""
    )

    assert (
        SimulationEngine._clean_response(response)
        == "It started early this afternoon, probably around lunchtime. "
        "While I was eating at my desk."
    )


def test_clean_response_turns_narrated_pointing_into_spoken_location():
    response = (
        '"It hurts right here," I said, pointing to my left shoulder. '
        '"Like someone is stabbing me with a knife. I can barely lift my arm."'
    )

    assert SimulationEngine._clean_response(response) == (
        "It hurts in my left shoulder. Like someone is stabbing me with a knife. "
        "I can barely lift my arm."
    )


def test_keyword_patient_response_removes_outer_quotes():
    case = ClinicalCase(
        case_id="test-case",
        title="Test Case",
        specialty="Emergency Medicine",
        difficulty="beginner",
        learning_objectives=[],
        presentation="Test presentation",
        initial_vitals={},
        history_data={"pain": '"It hurts in the middle of my chest."'},
        physical_exam={},
        investigations={},
        correct_diagnosis="Test diagnosis",
        acceptable_diagnoses=[],
        correct_management=[],
        key_learning_points=[],
    )

    assert (
        SimulationEngine._keyword_patient_response(case, "Tell me about the pain")
        == "It hurts in the middle of my chest."
    )
