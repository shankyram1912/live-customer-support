import os
from dotenv import load_dotenv

import logging

logger = logging.getLogger(__name__)

# Load environment variables early
load_dotenv(override=True)

class AgentConfig:
    """
    Manages environment-based routing between Gemini AI Studio 
    and Vertex AI models dynamically for the Live Translator.
    """
    def __init__(self):
        if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "false":
            
            logger.info("Need to route to GEMINI API (Google AI Studio).")
            
            self.ORCHESTRATOR_MODEL = os.getenv(
                "LIVEAGENT_GEMINI_MODEL", 
                "gemini-3.1-flash-live-preview"
            )            
            
            self.IS_VERTEX_AI_LIVE_API = False
            
        else:
            logger.info("Need to route to VERTEX AI API.")
            
            self.ORCHESTRATOR_MODEL = os.getenv(
                "LIVEAGENT_VERTEXAI_MODEL", 
                "gemini-live-2.5-flash-native-audio"
            )
            
            self.IS_VERTEX_AI_LIVE_API = True
            
        self.SUBAGENT_LITE_MODEL = os.getenv(
            "SUBAGENT_LITE_MODEL", 
            "gemini-3.1-flash-lite"
        )
        
        self.SUBAGENT_LITE_CLOUD_LOCATION = os.getenv(
            "SUBAGENT_LITE_CLOUD_LOCATION", 
            "global"
        )
        
        self.SUBAGENT_PRO_MODEL = os.getenv(
            "SUBAGENT_PRO_MODEL", 
            "gemini-2.5-pro"
        )
        
        self.SUBAGENT_PRO_CLOUD_LOCATION = os.getenv(
            "SUBAGENT_PRO_CLOUD_LOCATION", 
            "global"
        )        
        
        logger.info(f"INITAILIZATION COMPLETE with ORCHESTRATOR_MODEL {self.ORCHESTRATOR_MODEL}; IS_VERTEX_AI_LIVE_API {self.IS_VERTEX_AI_LIVE_API}; SUBAGENT_LITE_MODEL {self.SUBAGENT_LITE_MODEL}; SUBAGENT_LITE_CLOUD_LOCATION {self.SUBAGENT_LITE_CLOUD_LOCATION}; SUBAGENT_PRO_MODEL {self.SUBAGENT_PRO_MODEL}; SUBAGENT_PRO_CLOUD_LOCATION {self.SUBAGENT_PRO_CLOUD_LOCATION};")

# App Configuration
APP_NAME = "ai_customersupport"

# Initialize a singleton configuration object for importing across the app
agent_config = AgentConfig()