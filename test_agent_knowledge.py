"""
test_agent_knowledge.py

A script to test the agent_knowledge module.
It dynamically finds the knowledge file for a specific agent in Firestore,
extracts it to markdown using Vertex AI, and saves it back to the database.
"""

import logging
import os
import warnings
from dotenv import load_dotenv

# Suppress Pydantic serialization warnings 
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Import the backend module we created
import agent_knowledge

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)   
logger = logging.getLogger(__name__)

# Load environment variables first
load_dotenv(override=True)

if __name__ == "__main__":
    # Test Parameters
    test_agent = "FARAH"
    
    print("="*60)
    print(f"Testing Knowledge Extraction for Agent: {test_agent}")
    print("="*60)
    
    try:
        logger.info(f"Initiating knowledge processing pipeline for '{test_agent}'...")
        
        # Execute the processing function
        markdown_result = agent_knowledge.process_agent_knowledge(test_agent)
        
        print("\n" + "="*60)
        print("SUCCESS! Extracted Markdown :")
        print("="*60)
        
        # Print a preview of the markdown to verify it worked without flooding the console
        if markdown_result:
            print(markdown_result + "\n..............................")
        else:
            print("Warning: Returned markdown is empty.")
            
    except Exception as e:
        logger.error(f"Test failed for agent '{test_agent}': {e}")
        print("\n" + "="*60)
        print("TEST FAILED. See logs above for details.")
        print("="*60)