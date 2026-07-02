import os
import json
from typing import Optional
from mcp.server.fastmcp import FastMCP
import pypdf

# Initialize FastMCP Server
mcp = FastMCP("Scientific Paper Analyzer Tools")

# Define target reports directory
REPORTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "reports"))

@mcp.tool()
def read_pdf_text(pdf_path: str) -> str:
    """Extracts all text content from the specified PDF file.

    Args:
        pdf_path: The filesystem path to the scientific PDF file.
    """
    if not os.path.exists(pdf_path):
        return f"Error: The file path '{pdf_path}' does not exist."
    
    try:
        reader = pypdf.PdfReader(pdf_path)
        text_parts = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            text_parts.append(f"--- Page {i+1} ---\n{page_text}")
        
        full_text = "\n".join(text_parts)
        if not full_text.strip():
            return "Warning: The PDF was parsed, but no text content was found (it might be scanned/image-only)."
        return full_text
    except Exception as e:
        return f"Error occurred while parsing PDF: {str(e)}"

@mcp.tool()
def write_report(content: str, filename: str) -> str:
    """Saves the final research summary report to a file in the workspace.

    Args:
        content: The text content of the report to write (typically markdown).
        filename: Target filename (e.g., 'summary_report.md').
    """
    # Clean filename to prevent path traversal
    safe_filename = os.path.basename(filename)
    if not safe_filename.endswith(".md"):
        safe_filename += ".md"
        
    os.makedirs(REPORTS_DIR, exist_ok=True)
    file_path = os.path.join(REPORTS_DIR, safe_filename)
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Success: Report successfully saved to: {os.path.abspath(file_path)}"
    except Exception as e:
        return f"Error writing report: {str(e)}"

@mcp.tool()
def fetch_paper_metadata(title_query: str) -> str:
    """Simulates fetching academic paper metadata (authors, journal, citations) based on a title query.

    Args:
        title_query: The title or topic of the scientific paper.
    """
    # Simulated database lookup
    simulated_db = {
        "attention is all you need": {
            "title": "Attention Is All You Need",
            "authors": "Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Lukasz Kaiser, Illia Polosukhin",
            "year": 2017,
            "journal": "Advances in Neural Information Processing Systems (NeurIPS)",
            "citations": "110,000+",
            "doi": "10.48550/arXiv.1706.03762"
        },
        "gemini": {
            "title": "Gemini: A Family of Highly Capable Multimodal Models",
            "authors": "Gemini Team, Google",
            "year": 2023,
            "journal": "arXiv preprint arXiv:2312.11805",
            "citations": "1,500+",
            "doi": "10.48550/arXiv.2312.11805"
        }
    }
    
    query_lower = title_query.lower().strip()
    for key, data in simulated_db.items():
        if key in query_lower:
            return json.dumps(data, indent=2)
            
    # Default fallback mock metadata
    fallback_metadata = {
        "title": title_query,
        "authors": "Unknown Authors (Simulated Search)",
        "year": 2025,
        "journal": "International Journal of Simulated Research",
        "citations": "Pending",
        "note": "Custom search query; mock academic metadata generated."
    }
    return json.dumps(fallback_metadata, indent=2)

@mcp.tool()
def list_saved_reports() -> str:
    """Lists all saved report files in the reports folder."""
    if not os.path.exists(REPORTS_DIR):
        return "No reports folder exists yet. No reports have been written."
        
    try:
        files = [f for f in os.listdir(REPORTS_DIR) if os.path.isfile(os.path.join(REPORTS_DIR, f))]
        if not files:
            return "No reports found in the reports folder."
            
        reports_list = []
        for file in files:
            path = os.path.join(REPORTS_DIR, file)
            size = os.path.getsize(path)
            reports_list.append(f"- {file} ({size} bytes)")
        return "Saved Reports:\n" + "\n".join(reports_list)
    except Exception as e:
        return f"Error listing reports: {str(e)}"

if __name__ == "__main__":
    mcp.run()
