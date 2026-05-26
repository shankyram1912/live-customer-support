"""
agent_knowledge.py

Backend module to process knowledge files for CustomerSupport AI Agents.
Extracts document content directly from Cloud Storage and saves 
the raw Markdown output into the agent's Firestore document.
"""

import os
import logging
from google.cloud import firestore
from google import genai
from google.genai import types
from dotenv import load_dotenv
import config

# Configure logging

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(override=True)

# Configurations
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
DATABASE_ID = os.getenv("GOOGLE_CLOUD_FIRESTORE")

MIME_TYPE_MAPPING = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pdf": "application/pdf"
}

# ----------------------------------------------------------------------------
# Initialization & Setup
# ----------------------------------------------------------------------------

def get_firestore_client():
    """Initializes and returns the Firestore client connected to the specific DB."""
    return firestore.Client(project=PROJECT_ID, database=DATABASE_ID)

def get_genai_client():
    """Initializes the Google GenAI client natively for Vertex AI."""
    return genai.Client(location=config.agent_config.SUBAGENT_PRO_CLOUD_LOCATION)

def _get_mime_type(gs_path: str) -> str:
    """Validates and infers the MIME type directly from the Cloud Storage URI."""
    ext = os.path.splitext(gs_path)[1].lower()
    if ext not in MIME_TYPE_MAPPING:
        raise ValueError(
            f"Unsupported file extension: {ext}. "
            f"Allowed extensions are: {', '.join(MIME_TYPE_MAPPING.keys())}"
        )
    return MIME_TYPE_MAPPING[ext]

# ----------------------------------------------------------------------------
# Core Processing Method
# ----------------------------------------------------------------------------

def process_agent_knowledge(agent_name: str) -> str:
    """
    Connects to Firestore, retrieves the GCS URI for an agent's knowledge file,
    extracts the content using the subagent model into markdown, and saves it
    back to the database under 'knowledgeBase'.
    """
    logger.info(f"--- Starting Knowledge Processing for '{agent_name}' ---")
    
    # 1. Retrieve the Agent Document from Firestore
    db = get_firestore_client()
    doc_ref = db.collection('ai-agents').document(agent_name)
    doc = doc_ref.get()
    
    if not doc.exists:
        logger.error(f"[{agent_name}] Agent not found in database.")
        raise Exception(f"Agent '{agent_name}' not found in database.")
        
    data = doc.to_dict()
    gs_path = data.get('knowledgeFilePath')
    
    if not gs_path:
        logger.error(f"[{agent_name}] No 'knowledgeFilePath' configured in Firestore.")
        raise Exception(f"Agent '{agent_name}' has no knowledge file configured.")

    # 2. Prepare Document Part (No Local Download Required)
    mime_type = _get_mime_type(gs_path)
    logger.info(f"[{agent_name}] Preparing document directly from Cloud Storage: {gs_path}")
    
    document_part = types.Part.from_uri(
        file_uri=gs_path,
        mime_type=mime_type
    )

    # 3. Call Gemini to Extract Markdown
    client = get_genai_client()
    extraction_prompt = (
        "Extract all of the information in the document into a markdown file "
        "preserving all sections and information as is with strictly zero information loss."
    )
    
    logger.info(f"[{agent_name}] Calling '{config.SUBAGENT_PRO_MODEL}' for markdown extraction...")
    
    try:
        response = client.models.generate_content(
            model=config.agent_config.SUBAGENT_PRO_MODEL,
            contents=[document_part, extraction_prompt],
            config=types.GenerateContentConfig(
                temperature=0.1 # Lower temperature for more factual, verbatim extraction
            )
        )
        
        markdown_content = response.text
        logger.info(f"[{agent_name}] Extraction successful. Payload size: {len(markdown_content)} characters.")
        
    except Exception as e:
        logger.error(f"[{agent_name}] Failed to extract markdown via Gemini: {e}")
        raise

    # 4. Save the Result to Firestore
    try:
        doc_ref.update({"knowledgeBase": markdown_content, "isKnowledgeBaseReady": True})
        logger.info(f"[{agent_name}] Successfully updated database with 'knowledgeBase'.")
    except Exception as e:
        logger.error(f"[{agent_name}] Failed to save 'knowledgeBase' to Firestore: {e}")
        raise
        
    logger.info(f"--- Completed Knowledge Processing for '{agent_name}' ---")
    
    return markdown_content