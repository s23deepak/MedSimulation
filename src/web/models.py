from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from src.simulation.rubrics import Rubric


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Payload):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class Identity(BaseModel):
    user_id: str
    role: Literal["learner", "reviewer"]
    guest: bool = False


class Status(BaseModel):
    status: str


class Start(Payload):
    case_id: str = Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")
    resident_name: str = Field(default="Learner", max_length=100)


class SessionInput(Payload):
    session_id: str = Field(min_length=1, max_length=100)


class History(SessionInput):
    question: str = Field(min_length=1, max_length=2000)


class Exam(SessionInput):
    system: str = Field(min_length=1, max_length=200)


class Investigation(SessionInput):
    investigation: str = Field(min_length=1, max_length=200)


class Imaging(SessionInput):
    study_id: str = Field(min_length=1, max_length=200)


class Submit(SessionInput):
    diagnosis: str = Field(min_length=1, max_length=2000)
    management: list[str] = Field(max_length=100)


class Notes(SessionInput):
    clinical_notes: str = Field(max_length=10000)


class Generate(Payload):
    topic: str = Field(min_length=1, max_length=1000)
    source: Literal["auto", "pubmed", "wiley", "endless_medical"] = "auto"


class ImportRequest(Payload):
    specialty: str = Field(default="Emergency Medicine", max_length=200)
    query: str = Field(default="", max_length=1000)
    disease: str = Field(default="", max_length=200)
    max_results: int = Field(default=5, ge=1, le=20)
    dataset: Literal["medqa", "medqa_ext", "nejm", "nejm_ext"] = "medqa_ext"
    max_cases: int = Field(default=10, ge=1, le=100)


class Review(Payload):
    version: int = Field(ge=1)
    notes: str = Field(min_length=10, max_length=5000)
    clinical_review_confirmed: bool = False
    rubric: Rubric


class Reject(Payload):
    version: int = Field(ge=1)
    notes: str = Field(min_length=10, max_length=5000)


class CaseEdit(Payload):
    version: int = Field(ge=1)
    case: "CaseContent"


class CaseContent(Payload):
    case_id: str
    title: str
    specialty: str
    difficulty: str
    learning_objectives: list[str]
    presentation: str
    initial_vitals: dict[str, str | int | float]
    history_data: dict[str, str]
    physical_exam: dict[str, str]
    investigations: dict[str, str]
    correct_diagnosis: str
    acceptable_diagnoses: list[str]
    correct_management: list[str]
    key_learning_points: list[str]
    score_weights: dict[str, int]
    imaging_studies: list[dict[str, Any]] = Field(default_factory=list)
    patient_image_url: str = ""
    abnormal_vitals: list[str] = Field(default_factory=list)
    source: str = ""
    source_ref: str = ""
    version: int = 1
    status: str = "pending"
    reviewer: str | None = None
    approved_at: str | None = None
    review_notes: str | None = None
    rubric: dict[str, Any] = Field(default_factory=dict)


class Engagement(Payload):
    arm_id: str = Field(min_length=1, max_length=200)
    success: bool


class HistoryResult(BaseModel):
    question: str
    response: str
    ai: bool


class ExamResult(BaseModel):
    system: str
    findings: str


class InvestigationResult(BaseModel):
    investigation: str
    result: str


class ImagingResult(BaseModel):
    study_id: str
    modality: str
    description: str
    image_url: str
    findings: str


class SessionView(BaseModel):
    session_id: str
    resident_name: str
    case_id: str
    case_title: str
    specialty: str
    difficulty: str
    presentation: str
    initial_vitals: dict[str, Any]
    abnormal_vitals: list[str]
    learning_objectives: list[str]
    history_questions: list[dict[str, Any]]
    exam_systems_viewed: list[str]
    investigations_ordered: list[str]
    imaging_studies_viewed: list[str]
    imaging_studies: list[dict[str, Any]]
    patient_image_url: str
    diagnosis_submitted: str
    management_submitted: list[str]
    clinical_notes: str
    ordered_results: list[dict[str, str]]
    score: dict[str, Any] | None
    debrief: dict[str, Any] | None
    status: str
    started_at: str
    completed_at: str
    source: str
    source_ref: str
    case_version: int
    review_status: str
    reviewer: str | None
    approved_at: str | None
    scoring_available: bool
    available_exams: list[str]
    available_investigations: list[str]


class SessionListItem(BaseModel):
    session_id: str
    case_title: str
    status: str
    updated_at: str


class AssessmentResult(BaseModel):
    session_id: str
    submitted_at: str
    scores: dict[str, Any] | None
    debrief: dict[str, Any]
    ai_ready: bool
    disclaimer: str


class CaseSummary(BaseModel):
    case_id: str
    title: str
    specialty: str
    difficulty: str
    source: str
    source_ref: str = ""
    status: str
    version: int
    reviewer: str | None = None
    approved_at: str | None = None
    learning_objectives: list[str] = Field(default_factory=list)


class CaseResult(BaseModel):
    case_id: str
    title: str = ""
    status: str = "pending"


class ImportResult(BaseModel):
    imported: int
    cases: list[CaseResult]


class Health(BaseModel):
    status: str
    vllm_ready: bool
    vllm_warming_up: bool
    agent_loaded: bool
    cases_loaded: int


class PollStatus(BaseModel):
    session_id: str
    status: str
    ai_ready: bool
    scores: dict[str, Any] | None
    debrief: dict[str, Any] | None
    ai_feedback: str


class DebriefView(BaseModel):
    session_id: str
    scores: dict[str, Any] | None
    debrief: dict[str, Any]


class ModelStatus(BaseModel):
    connected: bool
    message: str


class DicomList(BaseModel):
    urls: list[str]


class AuditView(BaseModel):
    event_id: str
    actor: str
    event_type: str
    subject_id: str
    payload: str
    created_at: str
