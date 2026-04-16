"""
Generative Media Integration

Capabilities for generating patient spoken voice (OpenAI TTS).
"""

from __future__ import annotations

import logging
import base64

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


async def generate_patient_voice(client: AsyncOpenAI, text: str, sex: str = "U") -> str:
    """
    Generates spoken audio of the given text using OpenAI TTS models.
    Maps male patients to 'alloy' and female to 'nova'.
    Returns a base64 encoded string of the mp3 audio data.
    """
    try:
        if not client:
            return ""
            
        # Voice mapping
        voice = "alloy" # Default / Male
        if sex.upper().startswith("F"):
            voice = "nova"
            
        response = await client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text
        )
        
        # Read the binary response content
        audio_bytes = response.read()
        
        # Encode as base64 so it can be shipped easily over JSON payload to frontend
        encoded = base64.b64encode(audio_bytes).decode('utf-8')
        return f"data:audio/mp3;base64,{encoded}"
        
    except Exception as e:
        logger.warning("Failed to generate patient voice: %s", e)
        return ""
