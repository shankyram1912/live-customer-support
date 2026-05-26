import os
import logging
from datetime import date, datetime
from google.cloud import firestore
from dotenv import load_dotenv
from datetime import date, datetime, timezone, timedelta

# ==========================================
# Module-Level Setup
# ==========================================

logging.basicConfig(level=logging.INFO) 
logger = logging.getLogger(__name__)

load_dotenv(override=True)

# ----------------------------------------------------------------------------
# Database Path Constants
# ----------------------------------------------------------------------------
ORDERS_COLLECTION = "orders"
SYSTEM_METADATA_COLLECTION = os.getenv("FIRESTORE_SYSTEM_METADATA", "system_metadata")
ORDER_COUNTER_DOCUMENT = os.getenv("FIRESTORE_ORDER_COUNTER", "order_counter")


# ----------------------------------------------------------------------------
# Transaction Helper (Must be outside the class for Firestore to use it)
# ----------------------------------------------------------------------------
@firestore.transactional
def _execute_order_transaction(transaction, db, counter_ref, order_data):
    """
    Runs the atomic transaction to safely increment the counter and save the order.
    Returns the finalized document dictionary.
    """
    # 1. Read the current counter
    counter_snapshot = counter_ref.get(transaction=transaction)
    
    if counter_snapshot.exists:
        current_number = counter_snapshot.get('current_order_number')
    else:
        current_number = 1000 
        
    # 2. Increment the number (Pure Integer)
    new_order_number = current_number + 1
    
    # 3. Update the counter document
    transaction.set(counter_ref, {'current_order_number': new_order_number}, merge=True)
    
    # 4. Inject the purely numeric order_id inside the document
    order_data['order_id'] = new_order_number
    
    # 5. Define the flattened path directly in the root orders collection
    order_ref = db.collection(ORDERS_COLLECTION).document(str(new_order_number))
    
    # 6. Save the document to Firestore
    transaction.set(order_ref, order_data)
    
    # 7. Return the full document dictionary
    return order_data


# ----------------------------------------------------------------------------
# Tools Class Definition
# ----------------------------------------------------------------------------
class Tools:
    def __init__(self):
        """Initializes the Firestore connection with graceful degradation.""" 
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        db_id = os.getenv("GOOGLE_CLOUD_FIRESTORE")
        
        self.db = None
        
        if not project_id:
            logger.error("⚠️ GOOGLE_CLOUD_PROJECT is missing from the environment!")
        elif not db_id:
            logger.error("⚠️ GOOGLE_CLOUD_FIRESTORE is missing from the environment!")
        else:
            logger.info(f"Connecting to Firestore instance: {db_id} in project {project_id}")
            try:
                self.db = firestore.Client(
                    project=project_id, 
                    database=db_id
                )
                logger.info(f"✅ Connected to Firestore instance: {db_id} in project {project_id}")
                
            except Exception as e:
                logger.error(f"⚠️ Failed to connect to Firestore: {e}")
                # We log the error but allow the object to instantiate. 
                # Tools will cleanly return error JSONs to the AI agent if self.db is None.


    def finalize_order(
        self, 
        agent_name: str, 
        delivery_date: date, 
        contact_number: str, 
        delivery_address: str, 
        full_order_details: str
    ):
        """
        Saves the finalized order into the Firestore database using a sequential running number.
        Constructs the strict root schema and maps specific parameters into 'order_details'.
        """
        logger.info(f"[{agent_name}] Finalizing order for contact: {contact_number}")
        
        try:
            if not getattr(self, 'db', None):
                return {"error": "Database connection not initialized. Cannot process order."}

            counter_ref = self.db.collection(SYSTEM_METADATA_COLLECTION).document(ORDER_COUNTER_DOCUMENT)
            
            # 1. Safely parse and format the delivery_date from the AI string
            if isinstance(delivery_date, str):
                try:
                    # Parse the YYYY-MM-DD string from the AI into a datetime object
                    parsed_date = datetime.strptime(delivery_date, "%Y-%m-%d")
                    formatted_delivery_date = parsed_date.strftime("%d-%b-%Y")
                except ValueError:
                    # Fallback just in case the AI sends non-standard text
                    formatted_delivery_date = delivery_date
            else:
                # If it actually is a date object somehow, format it directly
                formatted_delivery_date = delivery_date.strftime("%d-%b-%Y")            
            
            # Map the arguments directly into the specific schema structure
            order_data = {
                "contact_number": contact_number,
                "created_at": firestore.SERVER_TIMESTAMP,
                "order_status": "PENDING QOUTE",
                "order_details": {
                    "agent_name": agent_name,
                    "delivery_date": formatted_delivery_date, 
                    "delivery_address": delivery_address,
                    "full_order_details": full_order_details,
                }
            }
            
            # Execute the transaction (agent_name removed from parameters as path is flattened)
            transaction = self.db.transaction()
            saved_document_dict = _execute_order_transaction(
                transaction, self.db, counter_ref, order_data
            )
            
            if 'created_at' in saved_document_dict:
                # Define SGT as UTC+8
                sgt_tz = timezone(timedelta(hours=8))
                
                # Get current time in SGT, strip microseconds, and format to ISO string
                saved_document_dict['created_at'] = datetime.now(sgt_tz).replace(microsecond=0).isoformat()
            
            logger.info(f"[{agent_name}] Order successfully saved with ID: {saved_document_dict.get('order_id')}")
            return saved_document_dict
            
        except Exception as e:
            logger.error(f"[{agent_name}] Failed to finalize order: {e}")
            return {"error": f"Database update failed: {str(e)}"}


    def retrieve_orders(self, agent_name: str, contact_number: str):
        """
        Retrieves all orders associated with a specific contact number for a specific agent.
        Orders results by order_id descending and caps the query to 50 results to prevent memory bloat.
        """
        logger.info(f"Retrieving orders for contact: {contact_number} under agent: {agent_name}")
        
        try:
            if not getattr(self, 'db', None):
                return {"error": "Database connection not initialized. Cannot retrieve orders."}
            
            # Query the root collection directly, utilizing the nested agent_name filter
            orders_query = (
                self.db.collection(ORDERS_COLLECTION)
                .where(filter=firestore.FieldFilter('contact_number', '==', contact_number))
                .where(filter=firestore.FieldFilter('order_details.agent_name', '==', agent_name))
                .order_by('order_id', direction=firestore.Query.DESCENDING)
                .limit(50)
                .stream()
            )
            
            results = []
            sgt_tz = timezone(timedelta(hours=8))
            
            for doc in orders_query:
                order_info = doc.to_dict()
                
                # Format timestamps for JSON serialization in SGT without microseconds
                if 'created_at' in order_info and order_info['created_at']:
                    # Convert Firestore's UTC datetime to SGT, strip microseconds, and format
                    order_info['created_at'] = (
                        order_info['created_at']
                        .astimezone(sgt_tz)
                        .replace(microsecond=0)
                        .isoformat()
                    )
                    
                results.append(order_info)
                
            logger.info(f"Successfully retrieved {len(results)} order(s) for {contact_number}.")
            return results
            
        except Exception as e:
            logger.error(f"Failed to retrieve orders for {contact_number}: {e}")
            return {"error": f"Query failed: {str(e)}"}