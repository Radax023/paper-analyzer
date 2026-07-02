import datetime
import json
import re
from typing import Any, Optional

from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.agents import LlmAgent as Agent, Context
from google.adk.workflow import node, START, DEFAULT_ROUTE, Edge, Workflow
from google.adk.events import Event, RequestInput

from google.adk.tools import AgentTool, McpToolset
from google.genai import types
from mcp import StdioServerParameters
from pydantic import BaseModel, Field

from app.config import config

# Define Workflow State Schema
class WorkflowState(BaseModel):
    user_message: str = ""
    pdf_path: str = ""
    extracted_text: str = ""
    pdf_analysis: str = ""
    research_summary: str = ""
    approved: bool = False
    feedback: str = ""
    report_path: str = ""
    security_error: str = ""

# Configure Gemini Model
agent_model = Gemini(
    model=config.model,
    retry_options=types.HttpRetryOptions(attempts=3),
)

# Connect to the local MCP server via Stdio transport
mcp_toolset = McpToolset(
    connection_params=StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "app.mcp_server"]
    )
)

# 1. Specialized Agent: PDF Analyzer
# Uses MCP server tools to read/extract text from the PDF file.
pdf_analyzer = Agent(
    name="pdf_analyzer",
    model=agent_model,
    instruction=(
        "You are a scientific paper parser. Your task is to analyze the text of the scientific paper. "
        "Extract the following structured sections:\n"
        "- Methodology: core algorithms, experimental design, and process flows.\n"
        "- Evaluation: datasets, metrics, and comparisons.\n"
        "- Math/Formulas: key equations and definitions.\n"
        "- Figures/Tables: descriptions of what figures/tables show.\n\n"
        "Input text: {extracted_text}"
    ),
    tools=[mcp_toolset],
    output_key="pdf_analysis",
)

# 2. Specialized Agent: Research Summarizer
# Generates a formatted academic summary, incorporating feedback if necessary.
summarizer = Agent(
    name="summarizer",
    model=agent_model,
    instruction=(
        "You are an expert academic summarizer. Your task is to generate a comprehensive, structured "
        "summary of the scientific paper using the detailed extraction notes from the pdf_analyzer.\n\n"
        "Your summary must include:\n"
        "1. **Title & Context**: The estimated title, field, and context.\n"
        "2. **Executive Summary**: Core thesis and contributions.\n"
        "3. **Methodology Summary**: Simplified explanation of the technical approach.\n"
        "4. **Key Findings**: Results and significance.\n"
        "5. **Limitations & Future Work**: Weaknesses and proposed next steps.\n\n"
        "Extraction Notes:\n{pdf_analysis}\n\n"
        "Human Feedback (if any, please address this to refine the summary):\n{feedback}"
    ),
    output_key="research_summary",
)

# 3. Specialized Agent: Report Writer
# Writes the final markdown report to disk using MCP file tools.
report_writer = Agent(
    name="report_writer",
    model=agent_model,
    instruction=(
        "You are a professional technical writer. Your task is to take the final approved research summary "
        "and write it into a clean, markdown report file.\n"
        "You MUST invoke the 'write_report' tool provided by the MCP server to save the report to disk.\n"
        "State the exact file path where the report was saved.\n\n"
        "Approved Summary:\n{research_summary}"
    ),
    tools=[mcp_toolset],
    output_key="report_path",
)

# 4. Orchestrator Agent
# Coordinates calling the specialized sub-agents.
orchestrator = Agent(
    name="orchestrator",
    model=agent_model,
    instruction=(
        "You are the main coordinator of the Scientific Paper Analyzer.\n"
        "The user's original request is: {user_message}\n\n"
        "Your goal is to coordinate the extraction and summary process:\n"
        "1. First, call the pdf_analyzer agent. Pass it the PDF path or the extracted text "
        "from the user's request so it can parse and structure the scientific content.\n"
        "2. Next, call the summarizer agent to generate the initial structured research summary.\n"
        "Do NOT call the report_writer here; report writing is done after human review."
    ),
    tools=[AgentTool(pdf_analyzer), AgentTool(summarizer)],
)

# Helper function to extract text from various node_input shapes
def extract_text(node_input: Any) -> str:
    """Best-effort extraction of a string from whatever the workflow passes as node_input."""
    if node_input is None:
        return ""
    if isinstance(node_input, str):
        return node_input
    if isinstance(node_input, dict):
        return (
            node_input.get("text", "")
            or node_input.get("paper_path", "")
            or node_input.get("content", "")
            or ""
        )
    # google.genai types.Content object
    if hasattr(node_input, "parts") and node_input.parts:
        texts = []
        for part in node_input.parts:
            if getattr(part, "text", None):
                texts.append(part.text)
        return " ".join(texts)
    return str(node_input)

# 5. Security Checkpoint Node
@node(rerun_on_resume=False)
async def security_checkpoint(ctx: Context, node_input: Any):
    """Validates input, scrubs PII, detects injections, and logs audit events.

    PII Scrubbing:
      - Email addresses → [REDACTED_EMAIL]
      - Phone numbers (intl & US formats) → [REDACTED_PHONE]
      - ORCID researcher IDs → [REDACTED_ORCID]

    Injection Detection keywords:
      - "ignore previous instructions"
      - "system prompt"
      - "bypass filter"
      - "you are now a"
      - "disregard all prior"
      - "reveal your instructions"

    Domain-specific rule:
      - Academic content filter: only applied when input is >1000 chars
        (i.e. actual paper text). Short messages are user commands/file paths
        and pass straight through to the orchestrator.
    """
    text = extract_text(node_input)
    # Store the raw user message in state so the orchestrator can see it
    # (e.g. "Analyze the paper at C:/papers/foo.pdf" → orchestrator extracts path)
    ctx.state["user_message"] = text

    # ── Audit log skeleton ──────────────────────────────────────────────────
    audit_entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "event": "security_checkpoint_evaluation",
        "input_length": len(text),
        "status": "CHECKING",
    }

    # ── PII Scrubbing ───────────────────────────────────────────────────────
    # 1. Email addresses
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    scrubbed_text, email_count = re.subn(email_pattern, "[REDACTED_EMAIL]", text)

    # 2. Phone numbers (E.164 international and US domestic formats)
    phone_pattern = r'(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}'
    scrubbed_text, phone_count = re.subn(phone_pattern, "[REDACTED_PHONE]", scrubbed_text)

    # 3. ORCID researcher identifiers  (e.g. 0000-0001-2345-6789)
    orcid_pattern = r'\b\d{4}-\d{4}-\d{4}-\d{3}[0-9X]\b'
    scrubbed_text, orcid_count = re.subn(orcid_pattern, "[REDACTED_ORCID]", scrubbed_text)

    pii_summary = {
        "emails_redacted": email_count,
        "phones_redacted": phone_count,
        "orcids_redacted": orcid_count,
    }

    # ── Prompt Injection Detection ──────────────────────────────────────────
    injection_keywords = [
        "ignore previous instructions",
        "system prompt",
        "bypass filter",
        "you are now a",
        "disregard all prior",
        "reveal your instructions",
    ]
    detected_keywords = [kw for kw in injection_keywords if kw in text.lower()]

    # ── Guard: max input length (DDoS / resource exhaustion) ───────────────
    max_length = 500_000
    if len(text) > max_length:
        audit_entry.update({
            "status": "REJECTED",
            "reason": "Input length exceeds limit",
            "severity": "WARNING",
            "pii": pii_summary,
        })
        print(f"AUDIT LOG: {json.dumps(audit_entry)}")
        ctx.state["security_error"] = (
            "Input text exceeds maximum allowed length of 500,000 characters."
        )
        return Event(route="SECURITY_EVENT", output="Input exceeds maximum allowed length.")

    # ── Guard: prompt injection ─────────────────────────────────────────────
    if detected_keywords:
        audit_entry.update({
            "status": "REJECTED",
            "reason": f"Prompt injection detected: {detected_keywords}",
            "severity": "CRITICAL",
            "pii": pii_summary,
        })
        print(f"AUDIT LOG: {json.dumps(audit_entry)}")
        ctx.state["security_error"] = (
            f"Prompt injection detected in input: {detected_keywords}"
        )
        return Event(route="SECURITY_EVENT", output="Security check failed: prompt injection detected.")

    # ── Domain-specific rule: Academic Content Filter ───────────────────────
    # Only enforce when the input is long enough to be actual paper text.
    # Short inputs (< 1000 chars) are user commands / file paths — the PDF
    # content will be extracted later by the pdf_analyzer agent via the MCP
    # read_pdf_text tool.  Applying this filter on a short user message would
    # always reject it because it contains no academic section headers.
    academic_keywords = [
        "abstract", "introduction", "methodology", "methods",
        "results", "discussion", "conclusion", "references",
        "experiment", "hypothesis",
    ]
    text_lower = text.lower()
    found_academic = [kw for kw in academic_keywords if kw in text_lower]
    if len(text) > 1000 and not found_academic:
        audit_entry.update({
            "status": "REJECTED",
            "reason": "Content failed academic domain filter: no scientific section keywords found.",
            "severity": "WARNING",
            "pii": pii_summary,
        })
        print(f"AUDIT LOG: {json.dumps(audit_entry)}")
        ctx.state["security_error"] = (
            "Submitted text does not appear to be a scientific paper. "
            "Please upload a valid academic PDF."
        )
        return Event(route="SECURITY_EVENT", output="Content filter: non-academic text rejected.")

    # ── All checks passed ───────────────────────────────────────────────────
    audit_entry.update({
        "status": "PASSED",
        "severity": "INFO",
        "pii": pii_summary,
        "academic_keywords_found": found_academic[:5],
    })
    print(f"AUDIT LOG: {json.dumps(audit_entry)}")

    ctx.state["extracted_text"] = scrubbed_text
    ctx.state["approved"] = False
    ctx.state["feedback"] = ""
    ctx.state["security_error"] = ""

    return Event(route="passed", output=scrubbed_text)

# 6. Security Alert Node
@node(rerun_on_resume=False)
async def security_alert_node(ctx: Context):
    """Outputs a security warning and halts the workflow."""
    error_msg = ctx.state.get("security_error", "Security violation detected.")
    return f"Execution blocked: {error_msg}"

# 7. Human Review HITL Node
class HumanReviewResponse(BaseModel):
    approved: bool = Field(description="Set to true if summary is approved, false if revisions are needed.")
    feedback: Optional[str] = Field(default="", description="Required changes or suggestions if not approved.")

@node(rerun_on_resume=True)
async def human_review(ctx: Context):
    """Pauses workflow for user review and decides to proceed or request refinement."""
    interrupt_id = "human_feedback"
    
    if interrupt_id in ctx.resume_inputs:
        user_input = ctx.resume_inputs[interrupt_id]
        
        # Parse inputs safely
        approved = True
        feedback = ""
        if isinstance(user_input, dict):
            approved = user_input.get("approved")
            if approved is None:
                approved = True
            feedback = user_input.get("feedback") or ""
        elif user_input is not None:
            approved = getattr(user_input, "approved", True)
            if approved is None:
                approved = True
            feedback = getattr(user_input, "feedback", "") or ""
            
        ctx.state["approved"] = approved
        ctx.state["feedback"] = feedback
        
        if approved:
            return Event(route="approved", output="Summary approved. Writing report...")
        else:
            return Event(route="needs_refinement", output=f"Revision requested: {feedback}")
            
    # Yield RequestInput to block and ask user
    return RequestInput(
        interrupt_id=interrupt_id,
        message=f"Please review the scientific paper summary:\n\n{ctx.state.get('research_summary', '')}\n\nDo you approve this summary? If not, please provide feedback.",
        response_schema=HumanReviewResponse
    )

# Define Workflow Graph
edges = [
    (START, security_checkpoint),
    (security_checkpoint, {
        "passed": orchestrator,
        "SECURITY_EVENT": security_alert_node
    }),
    (orchestrator, human_review),
    (human_review, {
        "needs_refinement": orchestrator,
        "approved": report_writer
    })
]

root_agent = Workflow(
    name="paper_analyzer_workflow",
    description="Multimodal Scientific Paper Analyzer Workflow",
    edges=edges,
    state_schema=WorkflowState,
)

app = App(
    root_agent=root_agent,
    name="app",
)
