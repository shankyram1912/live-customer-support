import os
from dotenv import load_dotenv
from pathlib import Path
import logging
import asyncio
import base64
import json
import time
import warnings
import uvicorn

from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.genai.types import ProactivityConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)   
logger = logging.getLogger(__name__)

# Suppress Pydantic serialization warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# config imports are placed after logging configuration so loggers in AgentConfig can be captured
import config
from config import AgentConfig
from agents import get_customersupport_agent  
import agent_knowledge
from voiceconfig import VoiceConfig

# Load environment variables first
load_dotenv(override=True)

app_name = config.APP_NAME
agent_config = config.agent_config

app = FastAPI(title="CustomerSupport AI Agent")
session_service = InMemorySessionService()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for demo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/live-customer-support/static", StaticFiles(directory=static_dir), name="static")

# Define the headers once to keep things clean
NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

@app.middleware("http")
async def add_cache_control_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.endswith(".js"):
        response.headers.update(NO_CACHE_HEADERS)
    return response

# ========================================
# Front End Endpoints
# ========================================

@app.get("/live-customer-support")
async def root():
    """Serve the index.html page."""
    return FileResponse(Path(__file__).parent / "static" / "index.html", headers=NO_CACHE_HEADERS)

@app.get("/live-customer-support/initialize")
async def init_page():
    """Serve the initialize.html page."""
    return FileResponse(Path(__file__).parent / "static" / "initialize.html", headers=NO_CACHE_HEADERS)

@app.post("/live-customer-support/initialize/saveagent/{agent_name}")
async def save_agent(agent_name: str):
    """
    Accepts an agent name as a path parameter, triggers the knowledge 
    processing pipeline, and returns a 200 OK status on success.
    """
    logger.info(f"Received request to process knowledge for agent: {agent_name}")
    
    try:
        await run_in_threadpool(
            agent_knowledge.process_agent_knowledge, 
            agent_name
        )
        
        return {
            "status": "success", 
            "message": f"Knowledge successfully processed and saved for {agent_name}."
        }
        
    except Exception as e:
        logger.error(f"Failed to process knowledge for {agent_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ========================================
# WebSocket Endpoint
# ========================================

@app.websocket("/live-customer-support/ws/{agent_name}/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    agent_name: str,
    user_id: str,
    session_id: str,
    voice: Optional[str] = "aoede",              # Defaults to 'aoede'
    affective_dialog: Optional[bool] = False,    # Auto-converts "true" to True
    proactive_audio: Optional[bool] = False      # Auto-converts "false" to False    
) -> None:    
    
    logger.info(
        f"WebSocket connection request: agent={agent_name}, user_id={user_id}, session_id={session_id}"
    )    
    await websocket.accept()
    logger.info("WebSocket connection accepted")
    logger.info(f"Settings - Voice: {voice}, Affective: {affective_dialog}, Proactive: {proactive_audio}")

    # Fetch Agent Dynamically from Firestore (Run in Threadpool so we don't block the event loop)
    try:
        agent = await run_in_threadpool(get_customersupport_agent, agent_name, VoiceConfig.is_female(voice))
        logger.info(f"Successfully loaded agent profile for: {agent_name}")
    except Exception as e:
        logger.error(f"Failed to load agent '{agent_name}': {e}")
        await websocket.close(code=1008, reason=f"Agent load failed: {str(e)}")
        return

    # Initialize a localized Runner for this specific connection
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    # Get or create session
    session = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if not session:
        await session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )

    # ========================================
    # Phase 2: Session Initialization 
    # ========================================

    response_modalities = ["AUDIO"]
        
    # 1. Define the base configuration arguments that apply to both Gemini and Vertex AI
    run_config_kwargs = {
        "streaming_mode": StreamingMode.BIDI,
        "response_modalities": response_modalities,
        "speech_config": types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice
                )
            )
        ),            
        "input_audio_transcription": types.AudioTranscriptionConfig(language_codes=['en-US', 'th-TH']),
        "output_audio_transcription": types.AudioTranscriptionConfig(language_codes=['en-US', 'th-TH']),
    }

    # 2. Conditionally inject features exclusive to the Vertex AI Live API
    if agent_config.IS_VERTEX_AI_LIVE_API:
        run_config_kwargs["session_resumption"] = types.SessionResumptionConfig()
        run_config_kwargs["proactivity"] = ProactivityConfig(proactive_audio=proactive_audio)
        run_config_kwargs["enable_affective_dialog"] = affective_dialog

    # 3. Instantiate the RunConfig by unpacking the dictionary
    run_config = RunConfig(**run_config_kwargs)

    live_request_queue = LiveRequestQueue()
    
    # Fix to optimize voice delivery
    opening_system_direction = types.Content(
        parts=[types.Part(text="System Instruction: Speak SLOWLY, use casual warm tone but SLOWLy. Dont respond to this message, wait for user input.")]
    )    
    live_request_queue.send_content(opening_system_direction)

    # ========================================
    # Phase 3: Active Session Tasks
    # ========================================

    async def upstream_task() -> None:
        """Receives messages from WebSocket and sends to LiveRequestQueue."""
        logger.info("upstream_task started")
        while True:
            try:
                message = await websocket.receive()

                # Cleanly break the loop if the frontend sends a disconnect signal
                if message.get("type") == "websocket.disconnect":
                    logger.info("Frontend explicitly closed the connection. Stopping upstream task.")
                    live_request_queue.close()
                    break

                if "bytes" in message:
                    audio_blob = types.Blob(
                        mime_type="audio/pcm;rate=16000", data=message["bytes"]
                    )
                    logger.debug("Frontend sent AUDIO.")
                    live_request_queue.send_realtime(audio_blob)

                elif "text" in message:
                    json_message = json.loads(message["text"])                    

                    if json_message.get("type") == "text":
                        content = types.Content(
                            parts=[types.Part(text=json_message["text"])]
                        )
                        logger.info(f"Frontend sent TEXT - {json_message}")
                        live_request_queue.send_content(content)
                        

                    # elif json_message.get("type") == "image":
                    #     logger.info(f"Frontend sent IMAGE")
                    #     image_data = base64.b64decode(json_message["data"])
                    #     mime_type = json_message.get("mimeType", "image/jpeg")
                    #     image_blob = types.Blob(
                    #         mime_type=mime_type, data=image_data
                    #     )
                    #     live_request_queue.send_realtime(image_blob)
                    
                    elif json_message.get("type") == "multi-image":
                        logger.info(f"Frontend sent MULTI-IMAGE upload layer")
                        images_list = json_message.get("images", [])
                        
                        for img_item in images_list:
                            try:
                                image_data = base64.b64decode(img_item["data"])
                                mime_type = img_item.get("mimeType", "image/jpeg")
                                image_blob = types.Blob(
                                    mime_type=mime_type, data=image_data
                                )
                                live_request_queue.send_realtime(image_blob)
                                logger.info(f"Frontend sent IMAGE...")
                            except Exception as e:
                                logger.error(f"Failed to process individual image in array: {e}")
                                
                    # ==========================================================================
                    # 💡 PROCESS REAL-TIME VIDEO STREAM FRAMES
                    # ==========================================================================
                    elif json_message.get("type") == "video":
                        try:
                            video_data = base64.b64decode(json_message["data"])
                            mime_type = json_message.get("mimeType", "image/jpeg")
                            video_blob = types.Blob(
                                mime_type=mime_type, data=video_data
                            )
                            # Pipe the frame instantly into Gemini's multi-modal queue
                            logger.info(f"Frontend sent VIDEO...")
                            live_request_queue.send_realtime(video_blob)                        
                        except Exception as e:
                            logger.error(f"Failed to process streaming video frame: {e}")                                
                    
            except RuntimeError as e:
                if "disconnect message" in str(e):
                    logger.info("Caught disconnect RuntimeError, stopping upstream task.")
                    break
                logger.error(f"Unexpected RuntimeError in upstream_task: {e}")
                break
            
            except WebSocketDisconnect:
                logger.info("WebSocket disconnect exception caught.")
                break                        

    async def downstream_task() -> None:
        """Receives Events from run_live() and sends to WebSocket."""
        logger.info("downstream_task started")
        
        async for event in runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            event_json = event.model_dump_json(exclude_none=True, by_alias=True)
            event_dict = json.loads(event_json)
            
            event_type = None
            event_summary = None
            is_audio_stream = False
            
            if event.content and event.content.parts:
                part = event.content.parts[0]
                
                if part.inline_data:
                    event_summary = f"AUDIO {part.inline_data.mime_type} Received {len(part.inline_data.data)} bytes"
                elif part.text:
                    event_summary = f"TEXT {part.text} IS_PARTIAL {event.partial} TURN_COMPLETE {event.turn_complete}"
                for part in event.content.parts:
                    if part.function_call:
                        event_type = "function_call"
                        event_summary = f"MODEL FUNCTION CALL {part.function_call.name} INPUT PARAMS {part.function_call.args}"
                    elif part.function_response:
                        event_type = "function_response"
                        event_summary = f"USER FUNCTION CALL RESPONSE {part.function_response.name} OUTPUT PARAMS {part.function_response.response}"                        
                    
            if event.input_transcription:
                event_summary = f"🗣️ USER TALKING: {event.input_transcription.text} IS_FINISHED {event.input_transcription.finished} IS_PARTIAL {event.partial} TURN_COMPLETE {event.turn_complete}"                        
            elif event.output_transcription:
                event_summary = f"🤖 AI AGENT TALKING: {event.output_transcription.text} IS_FINISHED {event.output_transcription.finished} IS_PARTIAL {event.partial} TURN_COMPLETE {event.turn_complete}"                        
                
            # Uncomment for event logging
            #if event_summary:
            #    print(f"++ {event_summary}", flush=True)
            # else:
            #     print(f"xx UNTAGGED EVENT {event_dict}", flush=True)
            
            
            if event.input_transcription and event.input_transcription.finished:
                print("\n" + "-"*50)
                print(f"🗣️ USER FINISHED: {event.input_transcription.text}")
                print("-" *50 + "\n", flush=True)                        
            elif event.output_transcription and event.output_transcription.finished:
                print("\n" + "="*50)
                print(f"🤖 AI AGENT FINISHED: {event.output_transcription.text}")
                print("="*50 + "\n", flush=True)                 
            
            # Always forward the raw event to the frontend (for audio), everything else is JSON
            if event.content and event.content.parts:
                part = event.content.parts[0]
                if part.inline_data:                                                
                    if hasattr(part, 'inline_data') and part.inline_data:
                        if hasattr(part.inline_data, 'data') and part.inline_data.data:
                            logger.debug(f"### SENDING AUDIO RESPONSE TO FRONTEND")                                
                            await websocket.send_bytes(part.inline_data.data)
                else:
                    if(event_type in ("function_call", "function_response")):                
                        logger.info(f"### RESPONSE TO FRONTEND - {event_json}")
                    await websocket.send_text(event_json)                    
            else:                
                # logger.info(f"### RESPONSE TO FRONTEND - {event_json}")
                await websocket.send_text(event_json)

    # ========================================
    # Run the Concurrent Tasks
    # ========================================
    try:
        logger.info("Starting asyncio.gather for tasks")
        await asyncio.gather(
            upstream_task(), 
            downstream_task(),
        )
    except WebSocketDisconnect:
        logger.info("Client disconnected normally")
    except asyncio.CancelledError:
        logger.info("Server shutting down. Cancelling active WebSocket tasks...")        
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        logger.info("Closing live_request_queue")
        live_request_queue.close()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=True)