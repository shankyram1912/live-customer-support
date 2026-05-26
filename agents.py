import os
from typing import Optional
import logging
from google.cloud import firestore
from google.adk.agents import LlmAgent

import config
from tools import Tools

toolInstance = Tools()

logger = logging.getLogger(__name__)

# Configurations
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
DATABASE_ID = os.getenv("GOOGLE_CLOUD_FIRESTORE")

# ----------------------------------------------------------------------------
# Initialization & Setup
# ----------------------------------------------------------------------------

def get_firestore_client():
    """Initializes and returns the Firestore client connected to the specific DB."""
    return firestore.Client(project=PROJECT_ID, database=DATABASE_ID)

db = get_firestore_client()

# ==========================================
# Static Base Instructions
# ==========================================
BASE_TOOLS_AND_RULES = """
    1. SPEAK IN THE LANGUAGE YOU ARE SPOKEN TO
    2. STRICTLY ANSWER BASED ON THE KNOWLEDGE BASE. DO NOT MAKE UP FACTS OUTSIDE OF IT. IF YOU DO NOT HAVE THE ANSWER, STATE APPROPRIATELY
    3. ONLY ADDRESS QUESTIONS RELATED TO THE CUSTOMER CONTEXT. 
    4. NOTE THAT CUSTOMER MAY PROVIDE IMAGES OR VIDEOS IN THEIR CONVERSATION, ALWAYS TAKE CARE TO COMPARE THAT INFORMATION WITH THE KNOWLEDGE BASE AND ANSWER STRICTLY BASED ON FACTS IN KNOWLEDGE BASE
    5. ALWAYS ENSURE finalize_order TOOL IS INVOKED BEFORE CONFIRMING ORDER FINALIZATION TO CUSTOMER
"""

# <tools>
# You have multiple tools to manage customer orders. Each tool interacts directly with the live database, ensuring the information is always the current truth.
# - finalize_order: Saves a finalized customer order to the database and generates a unique Order ID.
# - retrieve_orders: Retrieves a customer's existing or historical orders based on their contact number.

# finalize_order(agent_name: str, delivery_date: str, contact_number: str, delivery_address: str, full_order_details: str)
#   Saves a finalized customer order to the database. 
#   Arguments:
#     - agent_name (str): Your exact assigned agent name.
#     - delivery_date (str): The agreed-upon date for delivery (format: YYYY-MM-DD).
#     - contact_number (str): The customer's exact contact number exactly as the customer provides it. Do not add country code or + sign
#     - delivery_address (str): The exact destination address for the order delivery.
#     - full_order_details (str): Comprehensive details of the order, including items, quantities, allergies, and special instructions.
#   Usage rules:
#     - Call this ONLY when the customer has explicitly confirmed all order details and is ready to check out.
#     - Ensure you have gathered every piece of required information (date, number, address, details) before executing.

# retrieve_orders(agent_name: str, contact_number: str)
#   Retrieves a customer's existing or past orders based on their contact number.
#   Arguments:
#     - agent_name (str): Your exact assigned agent name.
#     - contact_number (str): The customer's exact contact number exactly as the customer provides it. Do not add country code or + sign
#   Usage rules:
#     - Call this when a customer asks for the status of an order, wants to repeat a previous order, or asks about their history.
#     - If you do not have the customer's contact number in the current context, you must ask them for it before attempting to call this tool.
# </tools>

# <tool_action_protocol>
# 1. Before taking action: Always verify you have the correct contact number. If missing, politely ask the user for it.
# 2. For order creation: Ensure the user explicitly agrees to the summary of items, delivery date, and address before calling finalize_order.
# 3. Call tools silently. Never announce intent to the user (e.g., do not say "Let me check the database...", "I will create your order now...").
# 4. The moment a tool returns, respond immediately to the user with the outcome (e.g., confirm their new Order ID or list their retrieved items). Do not wait for another prompt.
# </tool_action_protocol>

# ==========================================
# Dynamic Agent Factory
# ==========================================
def get_customersupport_agent(agent_name: str, is_female: bool) -> LlmAgent:
    """
    Fetches agent configuration from Firestore and dynamically builds 
    an LlmAgent with injected prompts. Raises an exception if the agent is not found.
    """
    
    if(is_female):          
      speech_rules ="""
      <speech_rules>
      - If you are spoken to in Thai, always speak as a FEMALE Thai Voice in casual slow pace, using the right pronouns, particles and speaking notations
      - Example: Always use the Thai polite particle 'ค่ะ' (Ka) at the end of sentences. Do not use 'ครับ' (Krap) since you are a female gender voice.
      </speech_rules>      
      """
      logger.info(f"FEMALE Voice Agent configured.")
    else:
      speech_rules ="""
      <speech_rules>
      - If you are spoken to in Thai, always speak as a MALE Thai Voice in casual slow pace, using the right pronouns, particles and speaking notations
      - Example: Always use the Thai polite particle 'ครับ' (Krap) at the end of sentences. Do not use 'ค่ะ' (Ka) since you are a male gender voice.
      </speech_rules>      
      """
      logger.info(f"MALE Voice Agent configured.")     
    
    # Fetch agent config from Firestore (exceptions here will intentionally bubble up)
    doc_ref = db.collection("ai-agents").document(agent_name)
    doc = doc_ref.get()
    
    if not doc.exists:
        raise ValueError(f"Agent '{agent_name}' not found in Firestore.")
        
    data = doc.to_dict()
    purpose = data.get("purpose", "")
    instructions = data.get("customerHandlingInstructions", "")
    knowledge_base = data.get("knowledgeBase", "")

    # Construct the final dynamic instruction string
    dynamic_instruction = f"""
      <agent_name>
      {agent_name}
      </agent_name>
      
      <system_core_directive>
      Always speak VERY SLOWLY in a CASUAL pace & warm tone.
      </system_core_directive>      
    
      <purpose>
      {purpose}
      </purpose>

      <customer_handling_instructions>
      {instructions}
      </customer_handling_instructions>
      
      <knowledge_base>
      {knowledge_base}
      </knowledge_base>      

    {BASE_TOOLS_AND_RULES}
    """
    
    logger.info(f"Successfully loaded agent config for: {agent_name}\n {dynamic_instruction}")

    return LlmAgent(
        name=agent_name,
        model=config.agent_config.ORCHESTRATOR_MODEL,
        instruction=dynamic_instruction,
        # tools=[toolInstance.retrieve_orders, toolInstance.finalize_order]  # Wrapper tools for subagents can be added here
        tools=[]  # Wrapper tools for subagents can be added here
    )