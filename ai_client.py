import httpx
import logging
from typing import List, Dict, Any

logger = logging.getLogger("vibelegal")

async def get_gemini_models(api_key: str) -> List[str]:
    """
    List available Gemini models that support generateContent.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            
            models = []
            if "models" in data:
                for m in data["models"]:
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        # Format: models/gemini-1.5-flash -> gemini-1.5-flash
                        name = m["name"].replace("models/", "")
                        models.append(name)
            return models
        except httpx.HTTPError as e:
            logger.error(f"Gemini Models API error: {str(e)}")
            raise

async def call_gemini(prompt: str, model: str, api_key: str, system_message: str = "") -> str:
    # Handle model name: ensure it doesn't have 'models/' prefix unless needed? 
    # API usually expects 'models/check' or just 'gemini-...' if we use the url /models/{model}:generate
    # We will use the URL format that accepts the model name directly.
    
    clean_model = model.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    
    parts = [{"text": prompt}]
    
    payload = {
        "contents": [{"parts": parts}]
    }
    
    if system_message:
        payload["systemInstruction"] = {
            "parts": [{"text": system_message}]
        }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, timeout=60.0)
            
            if response.status_code != 200:
                logger.error(f"Gemini API Error {response.status_code}: {response.text}")
                response.raise_for_status()
                
            data = response.json()
            
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text
            except (KeyError, IndexError):
                logger.error(f"Unexpected Gemini response format: {data}")
                raise ValueError("Failed to parse Gemini response")
                
        except httpx.HTTPError as e:
            logger.error(f"Gemini API error: {str(e)}")
            raise

async def call_groq(prompt: str, model: str, api_key: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}]
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=60.0)
            response.raise_for_status()
            data = response.json()
            # Extract text from response
            # Response format: {"choices": [{"message": {"content": "..."}}]}
            try:
                text = data["choices"][0]["message"]["content"]
                return text
            except (KeyError, IndexError):
                logger.error(f"Unexpected Groq response format: {data}")
                raise ValueError("Failed to parse Groq response")
        except httpx.HTTPError as e:
            logger.error(f"Groq API error: {str(e)}")
            raise
