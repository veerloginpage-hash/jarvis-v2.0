# actions/ai_document.py
import os
import json
from pathlib import Path
from core.ai_providers import get_router

def extract_pdf_text_helper(path: Path) -> str:
    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
    except ImportError:
        try:
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text += page.extract_text() + "\n"
        except ImportError:
            pass
    return text

def extract_docx_text_helper(path: Path) -> str:
    try:
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    except ImportError:
        return ""

def ai_document(parameters: dict, player=None, speak=None) -> str:
    """
    Advanced Document Intelligence using long-context free AI models.
    parameters:
      action: summarize | qa | generate_report | extract_entities
      file_path: path to the PDF, DOCX or TXT file (optional)
      question: question to ask (for qa action)
      requirements: formatting or outline requirements for reports
    """
    action = parameters.get("action", "").lower().strip()
    file_path = parameters.get("file_path", "").strip()
    
    if not file_path and player and player.current_file:
        file_path = player.current_file
        
    if not file_path:
        return "Please specify a file_path for document intelligence."
        
    p = Path(file_path)
    if not p.exists():
        return f"Document file not found: {file_path}"
        
    if player:
        player.write_log(f"AI Doc Intel: {p.name}")
        
    # Read text content based on file extension
    ext = p.suffix.lower()
    text_content = ""
    
    if ext == ".pdf":
        text_content = extract_pdf_text_helper(p)
    elif ext in (".docx", ".doc"):
        text_content = extract_docx_text_helper(p)
    else: # txt, md, log, etc.
        text_content = p.read_text(encoding="utf-8", errors="ignore")
        
    if not text_content.strip():
        return f"Could not extract any readable text from this document: {p.name}"
        
    try:
        router = get_router()
        
        if action == "summarize":
            if speak:
                speak("Sir, document load ho gya hai. Summarizing...")
                
            prompt = (
                f"Analyze the following document and provide a comprehensive summary, including key takeaways, "
                f"main points, and summary outline:\n\n"
                f"Document Name: {p.name}\n"
                f"Content:\n{text_content[:60000]}"
            )
            
            # Using heavy reasoning or general high capacity free models
            result = router.generate_text(prompt, task_type="reasoning")
            return result
            
        elif action == "qa":
            question = parameters.get("question", "").strip()
            if not question:
                return "Please provide a 'question' to ask about the document."
                
            if speak:
                speak("Sir, processing your question about the document.")
                
            prompt = (
                f"Answer the user's question based strictly on the provided document content:\n"
                f"Question: {question}\n\n"
                f"Document Name: {p.name}\n"
                f"Content:\n{text_content[:60000]}"
            )
            result = router.generate_text(prompt, task_type="general")
            return result
            
        elif action == "generate_report":
            reqs = parameters.get("requirements", "Create a standard business report from this data.")
            if speak:
                speak("Sir, report generate kar rha hoon.")
                
            prompt = (
                f"You are a professional report compiler. Generate a detailed formatted report based on the following document data:\n"
                f"Report Focus: {reqs}\n\n"
                f"Document Name: {p.name}\n"
                f"Content:\n{text_content[:60000]}"
            )
            result = router.generate_text(prompt, task_type="general")
            return result
            
        elif action == "extract_entities":
            if speak:
                speak("Sir, details extract kar rha hoon.")
            prompt = (
                f"Analyze the following document and extract all important entities, organizations, key figures, "
                f"names, email addresses, phone numbers, and dates. Format as a clean markdown table:\n\n"
                f"Document Name: {p.name}\n"
                f"Content:\n{text_content[:60000]}"
            )
            result = router.generate_text(prompt, task_type="general")
            return result
            
        else:
            return f"Unknown AI Document action: {action}"
            
    except Exception as e:
        return f"AI Document analysis failed: {e}"
