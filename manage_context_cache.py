"""
manage_context_cache.py

Backend module to manage Vertex AI Context Caches for CustomerSupport AI Agents.

Deprecated module as this was originally intended to be leveraged for an agent based tool that would answer based on file in context cache
"""

import os
import logging
from google.cloud import firestore
from google import genai
from google.genai import types
import config
import datetime

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Configurations
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
DATABASE_ID = os.getenv("GOOGLE_CLOUD_FIRESTORE")
MODEL_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION_GLOBAL")

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
    # Relies entirely on the VM's Application Default Credentials (ADC)
    return genai.Client(location=MODEL_LOCATION)

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
# Core Context Caching Methods (Matches Vertex Docs)
# ----------------------------------------------------------------------------

def build_agent_cache(agent_name: str, gs_path: str, purpose: str, instructions: str):
    """Creates a Context Cache combining the GCS document and System Instructions."""
    client = get_genai_client()
    mime_type = _get_mime_type(gs_path)
    
    logger.info(f"[{agent_name}] Preparing document directly from Cloud Storage: {gs_path}")
    
    # Vertex AI reads directly from the bucket; NO LOCAL DOWNLOAD REQUIRED!
    document_part = types.Part.from_uri(
        file_uri=gs_path,
        mime_type=mime_type
    )
    
    full_system_instruction = f"Purpose: {purpose}\n\nInstructions: {instructions}"
    
    logger.info(f"[{agent_name}] Creating Context Cache on Vertex AI...")
    
    # Create a timezone-aware datetime for Dec 31, 2050
    cache_expiration = datetime.datetime(2050, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)
    
    # Context Caching uses specific models (like gemini-3-flash-preview)
    cached_content = client.caches.create(
        model=config.SUBAGENT_LITE_MODEL,
        config=types.CreateCachedContentConfig(
            contents=[document_part],
            system_instruction=full_system_instruction,
            display_name=agent_name,
            expire_time=cache_expiration
        )
    )
    
    logger.info(f"[{agent_name}] Cache created successfully: {cached_content.name}, expiring at {cached_content.expire_time}.")
    return cached_content

def get_customersupport_agent_cache_name(agent_name: str):
    """
    Retrieves the stored system cache name (e.g., 'cachedContents/123') 
    for the given agent from Firestore.
    Returns the cache name if found, or None if it doesn't exist.
    """
    db = get_firestore_client()
    
    try:
        doc_ref = db.collection('ai-agents').document(agent_name)
        doc = doc_ref.get()
        
        if doc.exists:
            data = doc.to_dict()
            cache_name = data.get('agentCacheName')
            
            if cache_name:
                logger.info(f"[{agent_name}] Retrieved cache name from DB: {cache_name}")
                return cache_name
            else:
                logger.warning(f"[{agent_name}] Document exists, but 'agentCacheName' is missing.")
                return None
        else:
            logger.warning(f"[{agent_name}] Agent not found in database.")
            return None
            
    except Exception as e:
        logger.error(f"[{agent_name}] Error retrieving cache name from Firestore: {e}")
        return None

# ----------------------------------------------------------------------------
# Main Orchestration Methods
# ----------------------------------------------------------------------------

def create_agent_cache(agent_name: str):
    """
    Creates a Gemini Context Cache for the given agent from scratch.
    Validates against Firestore, grabs the GS URI and instructions, and builds it.
    """
    logger.info(f"--- Starting Context Cache Creation for '{agent_name}' ---")
    
    db = get_firestore_client()
    doc_ref = db.collection('ai-agents').document(agent_name)
    doc = doc_ref.get()
    
    if not doc.exists:
        raise Exception(f"Agent '{agent_name}' not found in database.")
        
    data = doc.to_dict()
    gs_path = data.get('knowledgeFilePath')
    purpose = data.get('agentPurpose', '')
    instructions = data.get('customerHandlingInstructions', '')
    
    if not gs_path:
        raise Exception(f"Agent '{agent_name}' has no knowledge file configured in Firestore.")
        
    # Delete any stale cache just in case
    delete_agent_cache(agent_name)
    
    # Build the new cache
    cache = build_agent_cache(agent_name, gs_path, purpose, instructions)
    
    # Store the system cache name back in Firestore
    try:
        # Using store.name to get the system identifier (e.g., "cachedContents/123")
        doc_ref.update({"agentCacheName": cache.name})
        logger.info(f"[{agent_name}] Successfully saved cache name '{cache.name}' to database.")
    except Exception as e:
        logger.error(f"[{agent_name}] Failed to save cache name to database. Error: {e}")
        
    logger.info(f"--- Completed Context Cache Creation for '{agent_name}' ---")
    return cache

def update_agent_cache(agent_name: str):
    """
    Updates the Context Cache for a given agent. 
    Because you cannot modify the contents of a cache once created, 
    this completely deletes the old one and re-creates it with the latest Firestore data.
    """
    logger.info(f"--- Starting Context Cache Update for '{agent_name}' ---")
    
    # For content changes, a full recreate is required, so we just wrap the create function
    cache = create_agent_cache(agent_name)
    
    logger.info(f"--- Completed Context Cache Update for '{agent_name}' ---")
    return cache

def delete_agent_cache(agent_name: str):
    """
    Deletes the Context Cache for the agent using the stored ID from Firestore.
    Clears the database reference once deleted.
    """
    client = get_genai_client()
    
    logger.info(f"[{agent_name}] Checking database for existing cache ID...")
    
    # 1. Get the exact system cache name from Firestore
    cache_name = get_customersupport_agent_cache_name(agent_name)
    
    if not cache_name:
        logger.info(f"[{agent_name}] No cache name found in database. Nothing to delete.")
        return

    # 2. Delete the cache directly by its name
    logger.info(f"[{agent_name}] Found cache ID '{cache_name}'. Deleting from Gemini API...")
    try:
        client.caches.delete(name=cache_name)
        logger.info(f"[{agent_name}] Successfully deleted cache '{cache_name}'.")
    except Exception as e:
        # It's common for caches to expire naturally, which throws a 404 here
        logger.error(f"[{agent_name}] Error deleting cache from API (it may have already expired): {e}")

    # 3. Clean up the Firestore document
    try:
        db = get_firestore_client()
        doc_ref = db.collection('ai-agents').document(agent_name)
        # Remove the field entirely from the document
        doc_ref.update({"agentCacheName": firestore.DELETE_FIELD})
        logger.info(f"[{agent_name}] Cleared cache reference from database.")
    except Exception as db_err:
        logger.error(f"[{agent_name}] Failed to clear cache reference from DB: {db_err}")
            
def get_customersupport_agent_cache(agent_name: str):
    """
    Checks if a Context Cache exists for the agent using the stored ID in Firestore.
    Returns the CachedContent object if found, or None if it does not exist or an error occurs.
    """
    client = get_genai_client()
    
    logger.info(f"[{agent_name}] Looking up cache ID in database...")
    
    # 1. Fetch the exact system cache name from Firestore
    cache_name = get_customersupport_agent_cache_name(agent_name)
    
    if not cache_name:
        logger.info(f"[{agent_name}] No cache name found in database.")
        return None

    logger.info(f"[{agent_name}] Retrieving cache '{cache_name}' from Gemini API...")
    
    # 2. Retrieve the cache directly using the name
    try:
        cache = client.caches.get(name=cache_name)
        logger.info(f"[{agent_name}] Successfully retrieved cache from API.")
        return cache
    except Exception as e:
        # Catch and log all errors (404 if expired, network issues, etc.)
        logger.error(f"[{agent_name}] Error retrieving cache '{cache_name}': {e}")
        return None