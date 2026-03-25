"""
Generative Media Integration

Capabilities for generating patient portraits (DALL-E 3) and patient spoken voice (OpenAI TTS).
"""

from __future__ import annotations

import logging
import base64
from pathlib import Path

from openai import AsyncOpenAI
import httpx

logger = logging.getLogger(__name__)

async def generate_patient_portrait(client: AsyncOpenAI, presentation: str, age: str = "", sex: str = "") -> str:
    """
    Generates a highly realistic clinical portrait of the patient based on their presentation.
    Returns the URL of the generated image.
    """
    try:
        if not client:
            return ""
            
        prompt = (
            "A highly professional, photorealistic clinical medical photograph of a patient. "
            f"Patient demographics: {age} {sex}. "
            f"Clinical context: {presentation}. "
            "The image should be a standard chest-up clinical portrait typical of an EMR "
            "(Electronic Medical Record). High quality, neutral lighting, plain background. "
            "No text, no UI elements."
        )
        
        response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        
        if response.data and len(response.data) > 0:
            return response.data[0].url
        return ""
    except Exception as e:
        logger.warning("Failed to generate patient portrait: %s", e)
        return ""


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
